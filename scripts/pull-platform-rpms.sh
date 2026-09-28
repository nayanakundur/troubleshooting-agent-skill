#!/usr/bin/env bash
#
# pull-platform-rpms.sh
#
# Downloads Itential Platform RPMs from itential.jfrog.io for local
# reproduction environments or VM-based deployments.
#
# Uses JFrog's AQL API to browse each repo and discover the exact file
# paths for a given version — no need to know the directory structure.
# Authentication is via a per-engineer JFrog Identity Token stored in .env.
#
# Authentication:
#   Requires JFROG_TOKEN in .env — a JFrog Identity Token.
#   Generate: itential.jfrog.io → User Profile → Generate Identity Token.
#   Each engineer uses their own token (not a shared credential).
#
# Version routing:
#   Legacy (23.2.x / 2023.x and below) → itential-config-service-files
#   P6 (6.x+)                          → PLATFORM, FLOWAI, CONFIG,
#                                         GATEWAY-MANAGER, INVENTORY-MANAGER,
#                                         SERVICE  (filter by --components)
#   IAG4 (any version)                 → automation-gateway  (--components iag4)
#   IAG5 (any version)                 → gateway5            (--components iag5)
#
# IAG4/IAG5 are always routed to their specific repos regardless of version era.
# They are not included in the default all-components pull — specify explicitly.
#
# Usage:
#   scripts/pull-platform-rpms.sh --version 6.4.0
#   scripts/pull-platform-rpms.sh --version 6.4.0 --components platform,config
#   scripts/pull-platform-rpms.sh --version 23.2.1   (auto-routes to legacy repo)
#   scripts/pull-platform-rpms.sh --version 6.4.0 --list    (no download)
#   scripts/pull-platform-rpms.sh --check                    (verify token)
#   scripts/pull-platform-rpms.sh --version 6.4.0 --out-dir /tmp/my-rpms
#   scripts/pull-platform-rpms.sh --version 4.4.46 --components iag4 --list
#   scripts/pull-platform-rpms.sh --version 4.4.46 --components iag4
#   scripts/pull-platform-rpms.sh --version 1.2.0  --components iag5 --list
#
# Exit codes:
#   0 = success / token valid / up to date
#   1 = auth failed (bad token)
#   2 = missing token, missing args, or network error
#

set -euo pipefail

JFROG_BASE="https://itential.jfrog.io/artifactory"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env}"

# ── Load JFROG_TOKEN from .env ────────────────────────────────────────────────

JFROG_TOKEN=""
if [[ -f "${ENV_FILE}" ]]; then
  JFROG_TOKEN="$(grep -E '^JFROG_TOKEN=' "${ENV_FILE}" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
fi

# ── Argument parsing ──────────────────────────────────────────────────────────

VERSION=""
COMPONENTS=""        # comma-separated; empty = all for the version era
LIST_ONLY=false
CHECK_ONLY=false
OUT_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)    VERSION="$2";     shift 2 ;;
    --components) COMPONENTS="$2";  shift 2 ;;
    --out-dir)    OUT_DIR="$2";     shift 2 ;;
    --list)       LIST_ONLY=true;   shift ;;
    --check)      CHECK_ONLY=true;  shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

# ── --check mode: verify token is valid ──────────────────────────────────────

if [[ "${CHECK_ONLY}" == true ]]; then
  if [[ -z "${JFROG_TOKEN}" ]]; then
    echo "Error: JFROG_TOKEN not set in .env." >&2
    echo "" >&2
    echo "  Generate a JFrog Identity Token:" >&2
    echo "    itential.jfrog.io → User Profile → Generate Identity Token" >&2
    echo "  Then add to .env:" >&2
    echo "    JFROG_TOKEN=<your-token>" >&2
    exit 2
  fi
  HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer ${JFROG_TOKEN}" \
    "${JFROG_BASE}/api/system/ping" 2>/dev/null || echo "000")
  if [[ "${HTTP_CODE}" == "200" ]]; then
    echo "JFROG_TOKEN is valid — itential.jfrog.io reachable."
    exit 0
  elif [[ "${HTTP_CODE}" == "401" || "${HTTP_CODE}" == "403" ]]; then
    echo "Error: JFROG_TOKEN is invalid or expired (HTTP ${HTTP_CODE})." >&2
    echo "  Regenerate at: itential.jfrog.io → User Profile → Generate Identity Token" >&2
    exit 1
  else
    echo "Error: could not reach itential.jfrog.io (HTTP ${HTTP_CODE})." >&2
    echo "  Check your network connection." >&2
    exit 2
  fi
fi

# ── Require --version for all other modes ─────────────────────────────────────

if [[ -z "${VERSION}" ]]; then
  echo "Error: --version is required." >&2
  echo "" >&2
  echo "Usage:" >&2
  echo "  scripts/pull-platform-rpms.sh --version 6.4.0" >&2
  echo "  scripts/pull-platform-rpms.sh --version 6.4.0 --list" >&2
  echo "  scripts/pull-platform-rpms.sh --check" >&2
  exit 2
fi

# ── Require token for download/list ──────────────────────────────────────────

if [[ -z "${JFROG_TOKEN}" ]]; then
  echo "Error: JFROG_TOKEN not set in .env." >&2
  echo "" >&2
  echo "  Generate a JFrog Identity Token:" >&2
  echo "    itential.jfrog.io → User Profile → Generate Identity Token" >&2
  echo "  Then add to .env:" >&2
  echo "    JFROG_TOKEN=<your-token>" >&2
  exit 2
fi

# ── Determine version era and which repos to query ───────────────────────────
#
# If the major version segment is > 999 (e.g. 2023), it's the legacy
# date-based scheme and everything lives in itential-config-service-files.
# Otherwise it's P6+ (6.x) with per-component repos.

MAJOR="${VERSION%%.*}"
if [[ "${MAJOR}" -gt 999 ]]; then
  LEGACY=true
else
  LEGACY=false
fi

# iag4/iag5 are always routed to their own repos — not affected by version era.
# If the only components requested are iag4/iag5, skip LEGACY routing entirely.
ALL_GATEWAY_ONLY=false
if [[ -n "${COMPONENTS}" ]]; then
  _non_gw=false
  IFS=',' read -ra _clist <<< "${COMPONENTS}"
  for _c in "${_clist[@]}"; do
    _c="${_c// /}"
    [[ "${_c}" != "iag4" && "${_c}" != "iag5" ]] && _non_gw=true && break
  done
  [[ "${_non_gw}" == false ]] && ALL_GATEWAY_ONLY=true
fi

# Component name → JFrog repo key
# iag4/iag5 are always routed to their specific repos, regardless of version era.
declare -A REPO_MAP
REPO_MAP=(
  [platform]="PLATFORM"
  [flowai]="FLOWAI"
  [config]="CONFIG"
  [gateway-manager]="GATEWAY-MANAGER"
  [inventory-manager]="INVENTORY-MANAGER"
  [service]="SERVICE"
  [iag4]="automation-gateway"
  [iag5]="gateway5"
)

# Build the list of repos to query
declare -a REPOS_TO_QUERY
if [[ "${ALL_GATEWAY_ONLY}" == true ]]; then
  # iag4/iag5 only — bypass LEGACY routing, use their own repos directly
  REPOS_TO_QUERY=()
  IFS=',' read -ra COMP_LIST <<< "${COMPONENTS}"
  for c in "${COMP_LIST[@]}"; do
    c="${c// /}"
    [[ -n "${REPO_MAP[$c]+_}" ]] && REPOS_TO_QUERY+=("${REPO_MAP[$c]}")
  done
elif [[ "${LEGACY}" == true ]]; then
  REPOS_TO_QUERY=("itential-config-service-files")
else
  if [[ -z "${COMPONENTS}" ]]; then
    # Default: all P6 platform components (iag4/iag5 excluded from default pull)
    REPOS_TO_QUERY=("PLATFORM" "CONFIG" "GATEWAY-MANAGER" "INVENTORY-MANAGER" "SERVICE" "FLOWAI")
  else
    REPOS_TO_QUERY=()
    IFS=',' read -ra COMP_LIST <<< "${COMPONENTS}"
    for c in "${COMP_LIST[@]}"; do
      c="${c// /}"  # trim spaces
      if [[ -n "${REPO_MAP[$c]+_}" ]]; then
        REPOS_TO_QUERY+=("${REPO_MAP[$c]}")
      else
        echo "Warn: unknown component '${c}' — skipping. Valid: ${!REPO_MAP[*]}" >&2
      fi
    done
  fi
fi

# ── Helper: AQL search for a version in a repo ───────────────────────────────
#
# Returns newline-separated "repo:path/filename" strings.

aql_search() {
  local repo="$1" version="$2"
  local aql_query
  aql_query="items.find({\"repo\":\"${repo}\",\"name\":{\"\$match\":\"*${version}*\"},\"type\":\"file\"})"
  local result
  result=$(curl -sf \
    -H "Authorization: Bearer ${JFROG_TOKEN}" \
    -H "Content-Type: text/plain" \
    --data "${aql_query}" \
    "${JFROG_BASE}/api/search/aql" 2>/dev/null) || {
    echo "Warn: AQL query failed for repo ${repo} (network or auth error)" >&2
    return
  }

  # jq may not be available — use python3 for portability
  echo "${result}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for r in data.get('results', []):
    path = r.get('path', '').lstrip('/')
    name = r['name']
    full = (path + '/' + name).lstrip('/')
    print(r['repo'] + ':' + full)
" 2>/dev/null || true
}

# ── Discover files across all queried repos ───────────────────────────────────

echo "==> Searching JFrog for version '${VERSION}' in: ${REPOS_TO_QUERY[*]}"

declare -a FOUND_FILES
for repo in "${REPOS_TO_QUERY[@]}"; do
  while IFS= read -r line; do
    [[ -n "${line}" ]] && FOUND_FILES+=("${line}")
  done < <(aql_search "${repo}" "${VERSION}")
done

if [[ ${#FOUND_FILES[@]} -eq 0 ]]; then
  echo "" >&2
  echo "No files found for version '${VERSION}' in the queried repos." >&2
  echo "  Repos searched: ${REPOS_TO_QUERY[*]}" >&2
  echo "  Use --list to browse available versions." >&2
  exit 1
fi

# ── --list mode: print and exit ───────────────────────────────────────────────

if [[ "${LIST_ONLY}" == true ]]; then
  echo ""
  echo "Files matching '${VERSION}':"
  for f in "${FOUND_FILES[@]}"; do
    repo_key="${f%%:*}"
    file_path="${f#*:}"
    printf "  [%-22s]  %s\n" "${repo_key}" "${file_path}"
  done
  echo ""
  echo "  Total: ${#FOUND_FILES[@]} file(s)"
  echo "  To download: remove --list from the command."
  exit 0
fi

# ── Download mode ─────────────────────────────────────────────────────────────

if [[ -z "${OUT_DIR}" ]]; then
  OUT_DIR="${REPO_ROOT}/repro/rpms/${VERSION}"
fi
mkdir -p "${OUT_DIR}"

echo "==> Downloading ${#FOUND_FILES[@]} file(s) to ${OUT_DIR}/"

DOWNLOADED=()
FAILED=()

for f in "${FOUND_FILES[@]}"; do
  repo_key="${f%%:*}"
  file_path="${f#*:}"
  filename="$(basename "${file_path}")"
  dest="${OUT_DIR}/${filename}"

  printf "    %-50s ... " "${filename}"

  HTTP_CODE=$(curl -fL -w "%{http_code}" -o "${dest}" \
    -H "Authorization: Bearer ${JFROG_TOKEN}" \
    "${JFROG_BASE}/${repo_key}/${file_path}" 2>/dev/null || echo "000")

  if [[ "${HTTP_CODE}" == "200" ]]; then
    CHECKSUM=$(python3 -c "import hashlib; print(hashlib.sha256(open('${dest}','rb').read()).hexdigest()[:16]+'...')" 2>/dev/null || echo "?")
    echo "OK  (sha256: ${CHECKSUM})"
    DOWNLOADED+=("${filename}")
  elif [[ "${HTTP_CODE}" == "401" || "${HTTP_CODE}" == "403" ]]; then
    echo "FAIL (auth — check JFROG_TOKEN)"
    FAILED+=("${filename}")
    rm -f "${dest}"
  else
    echo "FAIL (HTTP ${HTTP_CODE})"
    FAILED+=("${filename}")
    rm -f "${dest}"
  fi
done

# ── Write manifest ────────────────────────────────────────────────────────────

MANIFEST_FILE="${OUT_DIR}/JFROG_MANIFEST.json"

# Build file entries using python3 for correct JSON encoding
FILE_ENTRIES=""
for fn in "${DOWNLOADED[@]+"${DOWNLOADED[@]}"}"; do
  fp="${OUT_DIR}/${fn}"
  sha256=""
  [[ -f "${fp}" ]] && sha256=$(python3 -c "import hashlib; print(hashlib.sha256(open('${fp}','rb').read()).hexdigest())" 2>/dev/null || true)
  entry=$(python3 -c "import json; print(json.dumps({'filename': '${fn}', 'sha256': '${sha256}'}))")
  FILE_ENTRIES="${FILE_ENTRIES:+${FILE_ENTRIES},}${entry}"
done

python3 - "${VERSION}" "${LEGACY}" "${OUT_DIR}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" <<EOF > "${MANIFEST_FILE}"
import json, sys
version, legacy, out_dir, ts = sys.argv[1:]
repos = $(python3 -c "import json,sys; print(json.dumps(sys.argv[1:]))" "${REPOS_TO_QUERY[@]}")
files_raw = '${FILE_ENTRIES}'
files = json.loads('[' + files_raw + ']') if files_raw else []
print(json.dumps({
  "version": version,
  "legacy": legacy == "true",
  "repos_searched": repos,
  "downloaded_at": ts,
  "out_dir": out_dir,
  "files": files
}, indent=2))
EOF

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "==> Done."
echo "    Downloaded: ${#DOWNLOADED[@]} file(s) → ${OUT_DIR}/"
if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "    Failed:     ${#FAILED[@]} file(s) — ${FAILED[*]}"
fi
echo "    Manifest:   ${MANIFEST_FILE}"
echo ""
echo "  Next steps:"
echo "    Install directly:   sudo dnf install '${OUT_DIR}'/*.rpm"
echo "    Or pass to deployer via platform_packages in run-vars.yml"
echo "    (see .claude/skills/themis-aws-deploy/SKILL.md)"

if [[ ${#FAILED[@]} -gt 0 ]]; then
  exit 1
fi
