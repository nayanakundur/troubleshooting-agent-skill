#!/usr/bin/env bash
#
# list-environments.sh
#
# Discovers every environment this repo currently knows how to connect to and
# prints a single consolidated list, so an engineer never has to manually grep
# for stray .env files or Themis build artifacts:
#
#   1. .env / .env.* files (repo root + environments/ + repro/**, up to 3 levels)
#      — shows PLATFORM_URL and MONGO_URL (if set) for each, without printing
#        secret values (passwords, tokens, keys are never echoed).
#   2. Themis-provisioned VM builds under
#      .claude/skills/themis-aws-deploy/environments/<arch>/inventory/hosts
#      — shows architecture, host(s), and which roles (platform/mongodb/
#        redis/gateway) landed on each host.
#
# This is a read-only discovery script — it never modifies, sources, or
# authenticates against anything it finds.
#
# Usage:
#   scripts/list-environments.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=== .env files (PLATFORM_URL / MONGO_URL only — secrets never printed) ==="
echo

FOUND_ENV=0
while IFS= read -r f; do
  FOUND_ENV=1
  rel="${f#"${REPO_ROOT}"/}"
  url=$(grep -m1 "^PLATFORM_URL=" "$f" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
  mongo=$(grep -m1 "^MONGO_URL=" "$f" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
  # Redact credentials embedded in the Mongo connection string, if any.
  mongo_redacted=$(echo "$mongo" | sed -E 's#//[^:@/]+:[^:@/]+@#//***:***@#')
  printf "  %-45s → %s\n" "$rel" "${url:-[PLATFORM_URL not set]}"
  if [ -n "$mongo_redacted" ]; then
    printf "  %-45s   mongo: %s\n" "" "$mongo_redacted"
  fi
done < <(find "${REPO_ROOT}" -maxdepth 3 \( -name ".env" -o -name ".env.*" -o -path "*/environments/*.env" \) \
            2>/dev/null | grep -v -e "/\.git/" -e "/node_modules/" -e "/\.venv/" | sort -u)

if [ "$FOUND_ENV" -eq 0 ]; then
  echo "  (none found)"
fi

echo
echo "=== Themis-provisioned VM builds (.claude/skills/themis-aws-deploy/environments/) ==="
echo

THEMIS_ENV_DIR="${REPO_ROOT}/.claude/skills/themis-aws-deploy/environments"
FOUND_THEMIS=0

DESTROYED_ARCHS=()

if [ -d "$THEMIS_ENV_DIR" ]; then
  while IFS= read -r hosts_file; do
    arch_dir=$(dirname "$(dirname "$hosts_file")")
    arch=$(basename "$arch_dir")
    status_file="${arch_dir}/status.json"

    # Skip (and remember) environments whose status.json marks them DESTROYED —
    # the inventory/hosts file is a static build artifact and is never cleaned
    # up after a `tofu destroy`, so without this check every torn-down
    # environment would be listed as if it were still live.
    if [ -f "$status_file" ] && grep -q '"overall"[[:space:]]*:[[:space:]]*"DESTROYED"' "$status_file" 2>/dev/null; then
      DESTROYED_ARCHS+=("$arch")
      continue
    fi

    FOUND_THEMIS=1
    python3 - "$hosts_file" "$arch" <<'PYEOF'
import json, sys

hosts_file, arch = sys.argv[1], sys.argv[2]

with open(hosts_file) as fh:
    data = json.load(fh)

children = data.get("all", {}).get("children", {})

def collect_hosts(node):
    found = {}
    for host, meta in node.get("hosts", {}).items():
        found[host] = meta.get("ansible_host", "?")
    for child in node.get("children", {}).values():
        found.update(collect_hosts(child))
    return found

# role -> {host: ip}
role_hosts = {role: collect_hosts(node) for role, node in children.items()}

# host -> set of roles (top-level roles only, e.g. platform/mongodb/redis/gateway)
host_roles = {}
host_ip = {}
for role, hosts in role_hosts.items():
    for host, ip in hosts.items():
        host_roles.setdefault(host, set()).add(role)
        host_ip[host] = ip

print(f"  [{arch}]")
for host in sorted(host_roles):
    roles = ", ".join(sorted(host_roles[host]))
    print(f"    {host} ({host_ip[host]})  →  {roles}")
PYEOF
  done < <(find "$THEMIS_ENV_DIR" -maxdepth 3 -path "*/inventory/hosts" 2>/dev/null | sort -u)
fi

if [ "$FOUND_THEMIS" -eq 0 ]; then
  echo "  (none found)"
fi

if [ "${#DESTROYED_ARCHS[@]}" -gt 0 ]; then
  echo
  echo "  (destroyed, excluded above: ${DESTROYED_ARCHS[*]} — see environments/<arch>/status.json)"
fi

echo
