#!/usr/bin/env python3
"""
Stop hook: checks if platform-claude-skills vendor copy is stale relative to
upstream master. Prints a staleness report if behind. Always exits 0 —
informational only, never blocks the session. Fails silently if GITLAB_TOKEN
is missing, network is unavailable, or the manifest does not exist yet.
"""
import json, os, subprocess

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
MANIFEST = os.path.join(REPO_ROOT, "vendor", "platform-skills", "SYNC_MANIFEST.json")
GITLAB_REPO = "gitlab.com/itential/platform-engineering/platform-claude-skills.git"
BRANCH = "master"


def load_env():
    env_path = os.path.join(REPO_ROOT, ".env")
    env = {}
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip()
    except Exception:
        pass
    return env


def main():
    # Read the current vendored commit
    try:
        with open(MANIFEST) as f:
            m = json.load(f)
        local_sha = m.get("synced_commit", "")
        local_date = m.get("synced_commit_date", "unknown")
    except Exception:
        return  # no manifest or bad JSON — skip silently

    if not local_sha:
        return  # never synced — skip silently

    # Load GITLAB_TOKEN from .env
    env = load_env()
    gitlab_token = env.get("GITLAB_TOKEN", "")
    if not gitlab_token:
        return  # no token — skip silently (engineer will see error on first sync attempt)

    upstream_url = f"https://oauth2:{gitlab_token}@{GITLAB_REPO}"

    # Fetch upstream HEAD via git ls-remote (no clone, fast)
    try:
        result = subprocess.run(
            ["git", "ls-remote", upstream_url, f"refs/heads/{BRANCH}"],
            capture_output=True, text=True, timeout=10
        )
        parts = result.stdout.strip().split()
        upstream_sha = parts[0] if parts else None
    except Exception:
        return  # network unavailable — skip silently

    if not upstream_sha or upstream_sha == local_sha:
        return  # up to date or couldn't resolve

    local_short = local_sha[:12]
    upstream_short = upstream_sha[:12]
    print(
        f"\n⚠️  platform-skills vendor copy is out of date.\n"
        f"   Current:  {local_short}  (synced {local_date})\n"
        f"   Upstream: {upstream_short} ({BRANCH})\n"
        f"   Run:      scripts/sync-platform-skills.sh  to update.\n"
    )


main()
