#!/usr/bin/env bash
#
# sync-platform-skills.sh
#
# Refreshes (or checks staleness of) operational platform skills from:
#   https://gitlab.com/itential/platform-engineering/platform-claude-skills
#
# Syncs skill files (SKILL.md + companion scripts/docs) into .claude/skills/
# so the Skill tool can invoke them directly from a troubleshooting session.
#
# This is a ONE-SHOT SYNC, not a live dependency:
#   - troubleshooting-agent never calls out to platform-claude-skills at runtime
#   - this script refreshes committed copies on demand or at session start
#   - only specific paths are pulled (sparse checkout), never the full repo
#
# Authentication:
#   Requires GITLAB_TOKEN in .env — a GitLab Deploy Token with read_repository
#   scope. To create one: GitLab → platform-claude-skills → Settings →
#   Repository → Deploy tokens. Share via 1Password or Vault.
#
# Usage:
#   scripts/sync-platform-skills.sh [branch]          # sync (default: master)
#   scripts/sync-platform-skills.sh --check [branch]  # staleness check only
#
# --check mode:
#   Reads the current vendored SHA from SYNC_MANIFEST.json, fetches the
#   upstream HEAD via git ls-remote (no clone), and prints a staleness report.
#   Exit 0 = up to date.  Exit 1 = stale.  Exit 2 = check could not run.
#
set -euo pipefail

GITLAB_REPO="gitlab.com/itential/platform-engineering/platform-claude-skills.git"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENDOR_DIR="${REPO_ROOT}/vendor/platform-skills"
SKILLS_DIR="${REPO_ROOT}/.claude/skills"
MANIFEST="${VENDOR_DIR}/SYNC_MANIFEST.json"
CHANGELOG="${VENDOR_DIR}/SYNC_CHANGELOG.md"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env}"

# ── Load GITLAB_TOKEN from .env ───────────────────────────────────────────────

GITLAB_TOKEN=""
if [[ -f "${ENV_FILE}" ]]; then
  GITLAB_TOKEN="$(grep -E '^GITLAB_TOKEN=' "${ENV_FILE}" | head -1 | cut -d= -f2-)"
fi

if [[ -z "${GITLAB_TOKEN}" ]]; then
  echo "Error: GITLAB_TOKEN not set in .env." >&2
  echo "" >&2
  echo "  Add a GitLab Deploy Token with read_repository scope to .env:" >&2
  echo "    GITLAB_TOKEN=<deploy-token-value>" >&2
  echo "" >&2
  echo "  To create one: GitLab → platform-claude-skills → Settings →" >&2
  echo "  Repository → Deploy tokens (name: troubleshooting-agent, scope: read_repository)" >&2
  exit 2
fi

REPO_URL="https://oauth2:${GITLAB_TOKEN}@${GITLAB_REPO}"

# ── Argument parsing ──────────────────────────────────────────────────────────

CHECK_ONLY=false
if [[ "${1:-}" == "--check" ]]; then
  CHECK_ONLY=true
  shift
fi
BRANCH="${1:-master}"

# ── Helper: read a field from SYNC_MANIFEST.json ─────────────────────────────

manifest_field() {
  local field="$1"
  python3 -c "import json,sys; d=json.load(open('${MANIFEST}')); print(d.get('${field}',''))" 2>/dev/null || echo ""
}

# ── Helper: print staleness report ───────────────────────────────────────────

staleness_report() {
  local local_sha="$1" local_date="$2" upstream_sha="$3"
  local local_short="${local_sha:0:12}"
  local upstream_short="${upstream_sha:0:12}"
  echo ""
  echo "⚠️  platform-skills vendor copy is out of date."
  echo "   Current:  ${local_short}  (synced ${local_date})"
  echo "   Upstream: ${upstream_short} (${BRANCH})"
  echo "   Run:      scripts/sync-platform-skills.sh  to update."
  echo ""
}

# ── --check mode ─────────────────────────────────────────────────────────────

if [[ "${CHECK_ONLY}" == true ]]; then
  if [[ ! -f "${MANIFEST}" ]]; then
    echo "platform-skills: no SYNC_MANIFEST.json found — vendor copy has never been synced." >&2
    echo "Run: scripts/sync-platform-skills.sh" >&2
    exit 2
  fi

  LOCAL_SHA="$(manifest_field synced_commit)"
  LOCAL_DATE="$(manifest_field synced_commit_date)"
  if [[ -z "${LOCAL_SHA}" ]]; then
    echo "platform-skills: SYNC_MANIFEST.json is missing synced_commit field." >&2
    exit 2
  fi

  # Fetch upstream HEAD (fast: no clone, just a ref lookup)
  UPSTREAM_LINE="$(git ls-remote "${REPO_URL}" "refs/heads/${BRANCH}" 2>/dev/null || true)"
  if [[ -z "${UPSTREAM_LINE}" ]]; then
    echo "platform-skills: could not reach GitLab — check GITLAB_TOKEN and network." >&2
    echo "   Current vendor copy: ${LOCAL_SHA:0:12} (synced ${LOCAL_DATE})" >&2
    exit 2
  fi
  UPSTREAM_SHA="$(echo "${UPSTREAM_LINE}" | awk '{print $1}')"

  if [[ "${UPSTREAM_SHA}" == "${LOCAL_SHA}" ]]; then
    echo "platform-skills vendor copy is up to date (${LOCAL_SHA:0:12})."
    exit 0
  fi

  staleness_report "${LOCAL_SHA}" "${LOCAL_DATE}" "${UPSTREAM_SHA}"
  exit 1
fi

# ── Full sync mode ────────────────────────────────────────────────────────────
#
# All SKILL_PATHS land in .claude/skills/<name>/ — skills and their companion
# files (shell scripts, reference docs) are co-located, not split into vendor/.
# The VENDOR_DIR is used only for SYNC_MANIFEST.json and SYNC_CHANGELOG.md.

SKILL_PATHS=(
  # ── deployer-inventory ────────────────────────────────────────────────────
  "/.claude/skills/deployer-inventory/SKILL.md"

  # ── itential-gateway (IAG admin) ─────────────────────────────────────────
  "/.claude/skills/itential-gateway/SKILL.md"

  # ── itential-platform (IAP admin) ────────────────────────────────────────
  "/.claude/skills/itential-platform/SKILL.md"

  # ── mongodb — skill + offline collection companion files ──────────────────
  "/.claude/skills/mongodb/SKILL.md"
  "/.claude/skills/mongodb/offline-collect-cluster.sh"
  "/.claude/skills/mongodb/offline-collect-node.sh"
  "/.claude/skills/mongodb/fast-resync-seed.sh"
  "/.claude/skills/mongodb/OFFLINE-COLLECTION.md"
  "/.claude/skills/mongodb/CUSTOMER-DATA-COLLECTION.md"

  # ── perf-test-analysis — skill + headless wrapper ────────────────────────
  "/.claude/skills/perf-test-analysis/SKILL.md"
  "/.claude/skills/perf-test-analysis/run-perf-analysis.sh"

  # ── perflab ───────────────────────────────────────────────────────────────
  "/.claude/skills/perflab/SKILL.md"

  # ── prometheus ────────────────────────────────────────────────────────────
  "/.claude/skills/prometheus/SKILL.md"

  # ── redis ─────────────────────────────────────────────────────────────────
  "/.claude/skills/redis/SKILL.md"

  # ── themis-aws-deploy — skill + full companion tree ───────────────────────
  "/.claude/skills/themis-aws-deploy/SKILL.md"
  "/.claude/skills/themis-aws-deploy/run-vars.yml"
  "/.claude/skills/themis-aws-deploy/references/architectures.md"
  "/.claude/skills/themis-aws-deploy/references/healthy-certify.md"
  "/.claude/skills/themis-aws-deploy/references/preflight.md"
  "/.claude/skills/themis-aws-deploy/references/troubleshooting.md"
  "/.claude/skills/themis-aws-deploy/scripts/apply_run_vars.py"
  "/.claude/skills/themis-aws-deploy/scripts/fix_gateway_group.py"
  "/.claude/skills/themis-aws-deploy/scripts/run_environment_pipeline.py"
  "/.claude/skills/themis-aws-deploy/scripts/test_all_environments.py"
  "/.claude/skills/themis-aws-deploy/tfvars-overrides/asa-with-gateway.tfvars"
  "/.claude/skills/themis-aws-deploy/tfvars-overrides/ha2-with-gateway.tfvars"
  "/.claude/skills/themis-aws-deploy/tfvars-overrides/minimal-with-gateway.tfvars"
)

# ── Destination routing ───────────────────────────────────────────────────────
#
# All paths start with /.claude/skills/ and land in SKILLS_DIR.
# Companion files (scripts, docs) land adjacent to their SKILL.md.

dest_for_path() {
  local p="$1"
  echo "${SKILLS_DIR}/${p#/.claude/skills/}"
}

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

# Trap for git clone / sparse-checkout failures
sync_failure_report() {
  local exit_code="$?"
  echo "" >&2
  echo "✗  platform-skills sync failed (exit ${exit_code})." >&2
  echo "" >&2
  if [[ -f "${MANIFEST}" ]]; then
    local sha date synced
    sha="$(manifest_field synced_commit)"
    date="$(manifest_field synced_commit_date)"
    synced="$(manifest_field synced_at)"
    echo "   Current vendor copy:" >&2
    echo "     SHA:    ${sha} (short: ${sha:0:12})" >&2
    echo "     Date:   ${date}" >&2
    echo "     Synced: ${synced}" >&2
    echo "" >&2
    echo "   To check staleness:" >&2
    echo "     scripts/sync-platform-skills.sh --check" >&2
    echo "" >&2
    echo "   Common causes: expired GITLAB_TOKEN, network unavailable, branch mismatch." >&2
  else
    echo "   No existing vendor copy found (first sync never completed)." >&2
  fi
  echo "" >&2
}
trap 'sync_failure_report' ERR

echo "==> Sparse-cloning platform-claude-skills (branch: ${BRANCH})"
git clone --quiet --filter=blob:none --sparse --depth 1 --branch "${BRANCH}" \
  "${REPO_URL}" "${TMP_DIR}/repo"

# Clone succeeded — clear the ERR trap
trap - ERR

pushd "${TMP_DIR}/repo" >/dev/null
git sparse-checkout set --no-cone "${SKILL_PATHS[@]}"
UPSTREAM_SHA="$(git rev-parse HEAD)"
UPSTREAM_DATE="$(git log -1 --format=%cI)"
popd >/dev/null

# ── Stage pulled files, diff against current, copy to destinations ────────────

NEW_STAGE="${TMP_DIR}/new"
mkdir -p "${NEW_STAGE}"
for p in "${SKILL_PATHS[@]}"; do
  src="${TMP_DIR}/repo/${p}"
  if [[ -f "${src}" ]]; then
    dest="${NEW_STAGE}${p}"
    mkdir -p "$(dirname "${dest}")"
    cp "${src}" "${dest}"
  else
    echo "WARN: ${p} not found upstream (renamed/removed?) — skipping" >&2
  fi
done

mkdir -p "${VENDOR_DIR}" "${SKILLS_DIR}"

# Diff each staged file against its current destination
CHANGED_SKILLS=()
while IFS= read -r -d '' f; do
  rel="/${f#"${NEW_STAGE}"/}"  # restore leading slash
  current="$(dest_for_path "${rel}")"
  if [[ ! -f "${current}" ]] || ! diff -q "${current}" "${f}" >/dev/null 2>&1; then
    CHANGED_SKILLS+=("${rel#/.claude/skills/}")
  fi
done < <(find "${NEW_STAGE}" -type f -print0)

# Apply to destinations
echo "==> Applying sync"
for p in "${SKILL_PATHS[@]}"; do
  src="${NEW_STAGE}${p}"
  [[ -f "${src}" ]] || continue
  dest="$(dest_for_path "${p}")"
  mkdir -p "$(dirname "${dest}")"
  cp "${src}" "${dest}"
done

# ── Update manifest and changelog ─────────────────────────────────────────────

cat > "${MANIFEST}" <<EOF
{
  "source_repo": "https://${GITLAB_REPO}",
  "branch": "${BRANCH}",
  "synced_commit": "${UPSTREAM_SHA}",
  "synced_commit_date": "${UPSTREAM_DATE}",
  "synced_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "skill_files": $(printf '%s\n' "${SKILL_PATHS[@]}" | python3 -c 'import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')
}
EOF

{
  echo ""
  echo "## $(date -u +%Y-%m-%dT%H:%M:%SZ) — synced from ${UPSTREAM_SHA:0:12} (${BRANCH})"
  if [[ ${#CHANGED_SKILLS[@]} -eq 0 ]]; then
    echo "- No changes vs previously vendored copy."
  else
    echo "- ${#CHANGED_SKILLS[@]} skill file(s) changed (.claude/skills/):"
    for f in "${CHANGED_SKILLS[@]}"; do echo "  - ${f}"; done
  fi
} >> "${CHANGELOG}"

# ── Summary ───────────────────────────────────────────────────────────────────

echo "==> Done."
echo "    Synced commit: ${UPSTREAM_SHA}"

if [[ ${#CHANGED_SKILLS[@]} -eq 0 ]]; then
  echo "    No changes vs previous vendored copy."
else
  echo "    ${#CHANGED_SKILLS[@]} skill file(s) updated in .claude/skills/:"
  printf '      - %s\n' "${CHANGED_SKILLS[@]}"
fi
echo "    Review ${CHANGELOG} and 'git diff', then commit."

# ── Post-sync: validate LOCAL-EXTENSIONS.md against updated vendor files ──────

VALIDATOR="${SCRIPT_DIR}/validate-extensions.py"
if [[ -f "${VALIDATOR}" ]] && [[ ${#CHANGED_SKILLS[@]} -gt 0 ]]; then
  echo ""
  echo "==> Validating LOCAL-EXTENSIONS.md files against updated vendor skills..."

  # Determine which changed skills also have LOCAL-EXTENSIONS.md
  SKILLS_WITH_EXTENSIONS=()
  for s in "${CHANGED_SKILLS[@]}"; do
    # CHANGED_SKILLS entries are like "themis-aws-deploy/SKILL.md" or "themis-aws-deploy/scripts/..."
    # Extract just the skill name (first path segment)
    skill_name="${s%%/*}"
    ext_path="${SKILLS_DIR}/${skill_name}/LOCAL-EXTENSIONS.md"
    if [[ -f "${ext_path}" ]]; then
      # Deduplicate
      already=false
      for seen in "${SKILLS_WITH_EXTENSIONS[@]:-}"; do
        [[ "${seen}" == "${skill_name}" ]] && already=true && break
      done
      [[ "${already}" == false ]] && SKILLS_WITH_EXTENSIONS+=("${skill_name}")
    fi
  done

  if [[ ${#SKILLS_WITH_EXTENSIONS[@]} -eq 0 ]]; then
    echo "    No changed skills have LOCAL-EXTENSIONS.md — skipping."
  else
    for skill_name in "${SKILLS_WITH_EXTENSIONS[@]}"; do
      echo "    Checking: ${skill_name}"
      set +e
      python3 "${VALIDATOR}" --skill "${skill_name}" --save 2>/dev/null
      VALIDATION_EXIT=$?
      set -e
      if [[ ${VALIDATION_EXIT} -eq 0 ]]; then
        echo "    ✅ ${skill_name}: LOCAL-EXTENSIONS.md is consistent."
      elif [[ ${VALIDATION_EXIT} -eq 1 ]]; then
        echo ""
        echo "    ⚠️  ${skill_name}: New or modified vendor steps detected."
        echo "       Review the report above and run '/sync-vendor-skills' in Claude Code"
        echo "       to update LOCAL-EXTENSIONS.md interactively."
        echo ""
      elif [[ ${VALIDATION_EXIT} -eq 2 ]]; then
        echo ""
        echo "    🔴 ${skill_name}: CRITICAL — extension anchors no longer exist in vendor SKILL.md."
        echo "       LOCAL-EXTENSIONS.md sections will have NO EFFECT until fixed."
        echo "       Run '/sync-vendor-skills' in Claude Code to repair them."
        echo ""
      fi
    done
  fi
fi
