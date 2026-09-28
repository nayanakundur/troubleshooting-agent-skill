#!/usr/bin/env python3
"""
Adds the missing 'gateway' inventory group that generate_inventory.py never creates
for any of the 4 themis architectures (aio, minimal, ha2, asa) — see
project_themis_aws_deploy_gaps.md finding #2.

Generic across all 4 architectures:
  - Reads tofu output (hostnames + public_ips) from the given tofu working dir/workspace.
  - Any instance whose name contains "gateway" (case-insensitive) becomes a member of
    a flat 'gateway' inventory group — this naturally covers ha2 (1 host), minimal
    (1 host), and asa (2 hosts: dc1-gateway, dc2-gateway).
  - aio has no dedicated gateway instance (everything colocates on "all") — if no
    "gateway"-named instance is found, the single existing host already present in
    the inventory's redis_master group is reused instead.

Idempotent: safe to re-run against an inventory that already has a gateway group.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def tofu_output(working_dir, tf_binary):
    result = subprocess.run(
        [tf_binary, "output", "-json"],
        cwd=working_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory-dir", required=True)
    parser.add_argument("--tofu-working-dir", required=True)
    parser.add_argument("--tf-binary", default="tofu")
    args = parser.parse_args()

    hosts_file = Path(args.inventory_dir) / "hosts"
    inventory = json.loads(hosts_file.read_text())
    children = inventory["all"]["children"]

    if "gateway" in children:
        print("gateway group already present — nothing to do")
        return

    output = tofu_output(args.tofu_working_dir, args.tf_binary)
    hostnames = output["hostnames"]["value"]
    public_ips = output["public_ips"]["value"]

    gateway_instances = sorted(
        name for name in hostnames if "gateway" in name.lower()
    )

    if gateway_instances:
        gateway_hosts = {
            hostnames[name]: {"ansible_host": public_ips[name]}
            for name in gateway_instances
        }
        source = f"dedicated gateway instance(s): {', '.join(gateway_instances)}"
    else:
        # aio: no dedicated gateway VM — reuse the single existing host.
        any_group = next(iter(children.values()))
        gateway_hosts = dict(any_group["hosts"])
        source = "reused single aio host (no dedicated gateway instance found)"

    children["gateway"] = {"hosts": gateway_hosts}
    hosts_file.write_text(json.dumps(inventory, indent=2))

    group_vars_dir = Path(args.inventory_dir) / "group_vars" / "gateway"
    group_vars_dir.mkdir(parents=True, exist_ok=True)

    print(f"Added gateway group ({source}): {list(gateway_hosts.keys())}")


if __name__ == "__main__":
    sys.exit(main())
