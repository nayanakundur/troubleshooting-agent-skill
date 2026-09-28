#!/usr/bin/env python3
"""
generate_account_tfvars.py

Reads AWS override variables from .env and instance type overrides, then writes
tfvars-overrides/auto-account.tfvars for use with OpenTofu's -var-file flag.

This file overrides values from Themis's own terraform.tfvars (region, profile,
key_name, security groups, subnets, instance types) without modifying the vendor repo.

Usage:
  python3 generate_account_tfvars.py \\
    --env-file      /path/to/.env \\
    --run-vars      /path/to/run-vars.yml \\
    --arch-tfvars   /path/to/themis/vms/aws/tfvars/<architecture>.tfvars \\
    --output        /path/to/tfvars-overrides/auto-account.tfvars

Exit codes:
  0 = generated successfully (or no overrides needed, file removed)
  2 = fatal error (missing required input)
"""

import argparse
import os
import re
import sys


def parse_env_file(env_path: str) -> dict:
    env = {}
    if not os.path.exists(env_path):
        return env
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def parse_run_vars(yml_path: str) -> dict:
    """Minimal YAML parser — only reads bare key: value lines (no block scalars)."""
    result = {}
    if not os.path.exists(yml_path):
        return result
    with open(yml_path) as f:
        for line in f:
            line = line.rstrip()
            if line.startswith("#") or ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.split("#")[0].strip().strip('"').strip("'")
            if key and val:
                result[key] = val
    return result


def parse_arch_instances(tfvars_path: str) -> list:
    """
    Extract the instances list from an architecture tfvars file.
    Returns list of dicts: [{"name": "...", "instance_type": "..."}, ...]
    """
    if not os.path.exists(tfvars_path):
        return []
    with open(tfvars_path) as f:
        content = f.read()
    instances = []
    for block in re.finditer(r"\{[^}]+\}", content, re.DOTALL):
        text = block.group()
        name_m = re.search(r'name\s*=\s*"([^"]+)"', text)
        type_m = re.search(r'instance_type\s*=\s*"([^"]+)"', text)
        if name_m and type_m:
            instances.append({"name": name_m.group(1), "instance_type": type_m.group(1)})
    return instances


def resolve_instance_type(instance_name: str, env: dict, default: str) -> str:
    """Return per-role override from .env if set, else default from tfvars."""
    name = instance_name.lower()
    if "platform" in name or name == "all":
        return env.get("AWS_INSTANCE_TYPE_PLATFORM") or default
    if "redis" in name:
        return env.get("AWS_INSTANCE_TYPE_REDIS") or default
    if "mongo" in name:
        return env.get("AWS_INSTANCE_TYPE_MONGODB") or default
    if "gateway" in name:
        return env.get("AWS_INSTANCE_TYPE_GATEWAY") or default
    return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file",    required=True, help=".env file path")
    ap.add_argument("--run-vars",    required=True, help="run-vars.yml path")
    ap.add_argument("--arch-tfvars", required=True,
                    help="Architecture tfvars path (e.g. themis/vms/aws/tfvars/ha2.tfvars)")
    ap.add_argument("--output",      required=True, help="Output auto-account.tfvars path")
    args = ap.parse_args()

    env      = parse_env_file(args.env_file)
    run_vars = parse_run_vars(args.run_vars)

    # ── Determine what to write ──────────────────────────────────────────────

    aws_region   = env.get("AWS_REGION", "").strip()
    aws_profile  = run_vars.get("aws_profile", "").strip()
    aws_key_name = env.get("AWS_KEY_NAME", "").strip()
    raw_sg_ids   = env.get("AWS_SECURITY_GROUP_IDS", "").strip()
    raw_subnets  = env.get("AWS_SUBNET_IDS", "").strip()
    default_sub  = env.get("AWS_DEFAULT_SUBNET", "public-1a").strip() or "public-1a"

    type_overrides = any(
        env.get(k)
        for k in [
            "AWS_INSTANCE_TYPE_PLATFORM",
            "AWS_INSTANCE_TYPE_REDIS",
            "AWS_INSTANCE_TYPE_MONGODB",
            "AWS_INSTANCE_TYPE_GATEWAY",
        ]
    )

    has_any_override = any([aws_region, aws_key_name, raw_sg_ids, raw_subnets, type_overrides])

    # Profile is always written if aws_profile differs from the pe-team-sbx default
    needs_profile = aws_profile and aws_profile != "pe-team-sbx"

    if not has_any_override and not needs_profile:
        print("No .env AWS overrides detected — skipping auto-account.tfvars generation.")
        if os.path.exists(args.output):
            os.remove(args.output)
            print(f"    Removed stale {args.output}")
        return

    # ── Build HCL lines ──────────────────────────────────────────────────────

    lines = [
        "# Generated by generate_account_tfvars.py from .env — do not edit manually.\n",
        "# Applied as -var-file after the architecture and OS tfvars in all tofu commands.\n",
        "\n",
    ]

    if aws_region:
        lines.append(f'region = "{aws_region}"\n')

    if needs_profile:
        lines.append(f'profile = "{aws_profile}"\n')

    if aws_key_name:
        lines.append(f'key_name = "{aws_key_name}"\n')

    if raw_sg_ids:
        sg_ids = [s.strip() for s in raw_sg_ids.split(",") if s.strip()]
        sg_hcl = ", ".join(f'"{sg}"' for sg in sg_ids)
        lines.append(f"default_security_group_ids = [{sg_hcl}]\n")

    if raw_subnets:
        subnet_ids = [s.strip() for s in raw_subnets.split(",") if s.strip()]
        aliases = ["public-1a", "public-1b", "public-1c"]
        pairs = []
        for alias, sid in zip(aliases, subnet_ids):
            pairs.append(f'  "{alias}" = "{sid}"')
        lines.append("subnet_map = {\n" + "\n".join(pairs) + "\n}\n")
        lines.append(f'default_subnet = "{default_sub}"\n')

    # ── Instance type overrides — rewrite full instances list ────────────────

    if type_overrides:
        instances = parse_arch_instances(args.arch_tfvars)
        if not instances:
            print(
                f"Warning: instance type overrides requested but could not parse "
                f"instances from {args.arch_tfvars} — skipping instance block.",
                file=sys.stderr,
            )
        else:
            inst_lines = []
            for inst in instances:
                resolved = resolve_instance_type(inst["name"], env, inst["instance_type"])
                inst_lines.append(f'  {{ name = "{inst["name"]}", instance_type = "{resolved}" }}')
            lines.append("\ninstances = [\n")
            lines.append(",\n".join(inst_lines) + "\n")
            lines.append("]\n")

    # ── Write output ─────────────────────────────────────────────────────────

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as f:
        f.writelines(lines)

    print(f"==> Generated {args.output}")
    if aws_region:
        print(f"    region              = {aws_region}")
    if needs_profile:
        print(f"    profile             = {aws_profile}")
    if aws_key_name:
        print(f"    key_name            = {aws_key_name}")
    if raw_sg_ids:
        print(f"    security_group_ids  = {raw_sg_ids}")
    if raw_subnets:
        print(f"    subnet_ids          = {raw_subnets} (aliases: public-1a/b/c)")
    if type_overrides:
        for k, label in [
            ("AWS_INSTANCE_TYPE_PLATFORM", "platform"),
            ("AWS_INSTANCE_TYPE_REDIS", "redis"),
            ("AWS_INSTANCE_TYPE_MONGODB", "mongodb"),
            ("AWS_INSTANCE_TYPE_GATEWAY", "gateway"),
        ]:
            if env.get(k):
                print(f"    instance_type[{label}] = {env[k]}")


if __name__ == "__main__":
    main()
