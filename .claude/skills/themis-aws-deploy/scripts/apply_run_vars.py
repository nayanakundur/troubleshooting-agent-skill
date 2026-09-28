#!/usr/bin/env python3
"""
Distributes deployer vars from run-vars.yml into the generated Ansible inventory's group_vars.

Routing by variable prefix:
  redis_*     -> group_vars/redis_master, redis_replica, redis_sentinel (whichever exist)
  mongodb_*   -> group_vars/mongodb
  platform_*  -> group_vars/platform, platform_secondary (whichever exist)
  gateway_*   -> group_vars/gateway
  (no prefix) -> group_vars/all  (created if absent)

If --skill-dir is provided, certify report dirs are computed from it and written to the
relevant group_vars automatically — no need for *_certify_report_dir_local in run-vars.yml.

For AIO architecture with MongoDB TLS enabled: automatically injects platform_mongo_url
using the actual EC2 hostname (not localhost) to avoid TLS SAN mismatch on startup.

Skill-consumed keys are skipped and never forwarded to Ansible:
  architecture, os, themis_root, owner, deployer_repo, tls_repo,
  deployment_scope, certify_scope, artifacts_retention_days
"""

import argparse
import json
import os
import sys

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

SKILL_VARS = {
    "architecture", "os", "themis_root", "owner",
    "deployer_repo", "tls_repo",
    "aws_profile", "ssh_key_path",
    "deployment_scope", "certify_scope",
    "artifacts_retention_days",
}

PREFIX_TO_GROUPS = {
    "redis_":    ["redis_master", "redis_replica", "redis_sentinel"],
    "mongodb_":  ["mongodb", "mongodb_primary"],
    "platform_": ["platform", "platform_secondary"],
    "gateway_":  ["gateway"],
}


def target_groups(key):
    for prefix, groups in PREFIX_TO_GROUPS.items():
        if key.startswith(prefix):
            return groups
    return ["all"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vars-file", required=True, help="Path to run-vars.yml")
    parser.add_argument("--inventory-dir", required=True, help="Path to generated inventory dir")
    parser.add_argument("--skill-dir", default=None, help="Skill directory — derives certify report dir paths")
    parser.add_argument(
        "--architecture", default=None,
        help="Authoritative architecture for this run. Overrides the vars-file's own "
             "'architecture:' key — required when multiple architectures share one "
             "run-vars.yml (e.g. the concurrent orchestrator), since that file's "
             "'architecture:' value is otherwise stale for every architecture but one.",
    )
    args = parser.parse_args()

    with open(args.vars_file) as f:
        all_vars = yaml.safe_load(f) or {}

    if args.architecture:
        all_vars["architecture"] = args.architecture

    # Separate skill vars from deployer vars; skip null/empty values
    deployer_vars = {
        k: v for k, v in all_vars.items()
        if k not in SKILL_VARS and v is not None and v != ""
    }

    # Inject ansible_ssh_private_key_file from ssh_key_path
    ssh_key = all_vars.get("ssh_key_path", "")
    if ssh_key:
        deployer_vars.setdefault("ansible_ssh_private_key_file", ssh_key)

    # Inject certify report dirs from skill-dir if provided
    if args.skill_dir:
        reports = os.path.join(args.skill_dir, "reports")
        deployer_vars.setdefault("redis_certify_report_dir_local",    os.path.join(reports, "redis"))
        deployer_vars.setdefault("mongodb_certify_report_dir_local",  os.path.join(reports, "mongodb"))
        deployer_vars.setdefault("platform_certify_report_dir_local", os.path.join(reports, "platform"))

    # AIO + TLS: inject platform_mongo_url using the actual hostname.
    # The deployer defaults to mongodb://localhost:27017 for AIO, but localhost is not in the
    # MongoDB cert SAN — TLS hostname verification fails and platform crashes on startup.
    if (all_vars.get("architecture") == "aio"
            and "platform_mongo_url" not in deployer_vars
            and all_vars.get("platform_webserver_https_enabled", True) is not False):
        hosts_file = os.path.join(args.inventory_dir, "hosts")
        if os.path.exists(hosts_file):
            with open(hosts_file) as f:
                hosts_data = json.load(f)
            platform_hosts = (
                hosts_data.get("all", {})
                          .get("children", {})
                          .get("platform", {})
                          .get("hosts", {})
            )
            if platform_hosts:
                hostname = next(iter(platform_hosts))
                mongo_user = all_vars.get("mongodb_user_itential_username", "itential")
                mongo_pass = all_vars.get("mongodb_user_itential_password", "itential")
                deployer_vars["platform_mongo_url"] = (
                    f"mongodb://{mongo_user}:{mongo_pass}@{hostname}:27017/itential"
                )
                print(f"AIO TLS: injected platform_mongo_url with hostname {hostname}")

    # Bucket vars by target group
    grouped = {}
    for key, value in deployer_vars.items():
        for group in target_groups(key):
            grouped.setdefault(group, {})[key] = value

    group_vars_root = os.path.join(args.inventory_dir, "group_vars")
    written = []
    skipped = []

    for group, vars_dict in grouped.items():
        group_dir = os.path.join(group_vars_root, group)
        if not os.path.isdir(group_dir):
            if group == "all":
                os.makedirs(group_dir)
            else:
                skipped.append(group)
                continue
        # Filename must sort alphabetically after every group_vars file themis's
        # generate_inventory.py copies in from inventories/common/ (platform_common.yml,
        # platform_site_common.yml, platform_worker_disabled.yml, redis_common.yml) —
        # Ansible loads group_vars/<group>/*.yml in alphabetical order and later files
        # win, so a plain "custom.yml" here is silently overridden back to themis's
        # hardcoded values (confirmed live 2026-09-10, e.g. platform_release/
        # platform_packages set here had no effect). "zz_" guarantees this always wins.
        out_file = os.path.join(group_dir, "zz_run_vars.yml")
        with open(out_file, "w") as f:
            f.write("---\n")
            yaml.dump(vars_dict, f, default_flow_style=False)
        written.append(out_file)

    for path in written:
        print(f"Wrote:   {path}")
    for group in skipped:
        print(f"Skipped: {group} (group dir not in inventory — not used by this architecture)")


if __name__ == "__main__":
    main()
