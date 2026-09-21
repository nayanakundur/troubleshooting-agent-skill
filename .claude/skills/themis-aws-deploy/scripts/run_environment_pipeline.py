#!/usr/bin/env python3
"""
Runs the full themis-aws-deploy pipeline (provision -> inventory -> TLS -> deploy -> certify)
for a single architecture, entirely self-contained under
<skill_dir>/environments/<architecture>/ so multiple architectures can run concurrently
without touching each other's files.

Reads shared settings (themis_root, owner, deployer_repo, tls_repo, aws_profile,
ssh_key_path, deployment_scope, certify_scope, os, credentials, gateway_*, platform_*)
from a single shared run-vars.yml — only --architecture varies per invocation. The
`architecture:` key inside run-vars.yml itself is ignored; this script's --architecture
argument is authoritative.

Writes:
  <env_dir>/pipeline.log   - full combined stdout/stderr, append-only
  <env_dir>/status.json    - structured phase-by-phase status, updated after every phase

Exit code 0 = pipeline reached the end (individual phases may still be FAILED — check
status.json for the real verdict). Non-zero = the script itself crashed unexpectedly.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml


OS_TFVARS_MAP = {
    "al2023": "amazon2023",
}

# Architectures that need a supplemental tfvars file (outside the read-only themis repo)
# adding a gateway VM, since none of the 4 designs provision one natively.
GATEWAY_TFVARS_OVERRIDE = {
    "ha2": "ha2-with-gateway.tfvars",
    "minimal": "minimal-with-gateway.tfvars",
    "asa": "asa-with-gateway.tfvars",
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Pipeline:
    def __init__(self, architecture, skill_dir, run_vars_path):
        self.architecture = architecture
        self.skill_dir = Path(skill_dir)
        self.env_dir = self.skill_dir / "environments" / architecture
        self.env_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.env_dir / "pipeline.log"
        self.status_path = self.env_dir / "status.json"
        self.log_fh = open(self.log_path, "a")

        with open(run_vars_path) as f:
            self.run_vars = yaml.safe_load(f) or {}

        self.themis_root = self.run_vars["themis_root"]
        self.owner = self.run_vars["owner"]
        self.deployer_repo = self.run_vars["deployer_repo"]
        self.tls_repo = self.run_vars["tls_repo"]
        self.aws_profile = self.run_vars["aws_profile"]
        self.ssh_key_path = os.path.expanduser(self.run_vars["ssh_key_path"])
        self.deployment_scope = self.run_vars.get("deployment_scope", "full")
        self.certify_scope = self.run_vars.get("certify_scope", "full")
        os_key = self.run_vars["os"]
        self.os_tfvars = OS_TFVARS_MAP.get(os_key, os_key)

        self.status = {
            "architecture": architecture,
            "started_at": now_iso(),
            "finished_at": None,
            "phases": {},
            "overall": "RUNNING",
            "error": None,
        }
        self._write_status()

    def log(self, msg):
        line = f"[{now_iso()}] {msg}\n"
        self.log_fh.write(line)
        self.log_fh.flush()

    def _write_status(self):
        self.status_path.write_text(json.dumps(self.status, indent=2))

    def phase(self, name, ok, detail=""):
        self.status["phases"][name] = "PASSED" if ok else "FAILED"
        self._write_status()
        self.log(f"PHASE {name}: {'PASSED' if ok else 'FAILED'} {detail}".strip())

    def run(self, cmd, cwd=None, env_extra=None, check=True):
        env = os.environ.copy()
        env["AWS_PROFILE"] = self.aws_profile
        # TF_WORKSPACE avoids the shared .terraform/environment marker file entirely —
        # `tofu workspace select` from concurrent processes in the same vms/aws directory
        # races on that file (confirmed live 2026-08-12: one architecture's apply ran
        # against another's state and failed acquiring its lock). The env var is read
        # directly by tofu/terraform with no shared-file write, so it's race-free across
        # concurrently running architectures. Harmless (ignored) for non-tofu commands.
        env["TF_WORKSPACE"] = self.architecture
        if env_extra:
            env.update(env_extra)
        self.log(f"$ {' '.join(cmd)} (cwd={cwd})")
        result = subprocess.run(
            cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        self.log_fh.write(result.stdout)
        self.log_fh.flush()
        if check and result.returncode != 0:
            raise RuntimeError(f"command failed (rc={result.returncode}): {' '.join(cmd)}")
        return result

    def ansible_env(self):
        return {
            "ANSIBLE_COLLECTIONS_PATH": f"{self.skill_dir}/collections:~/.ansible/collections",
            "ANSIBLE_FORKS": "20",
            "ANSIBLE_PIPELINING": "True",
        }

    def fail_and_exit(self, phase_name, exc):
        self.phase(phase_name, ok=False, detail=str(exc))
        self.status["overall"] = "FAILED"
        self.status["error"] = f"{phase_name}: {exc}"
        self.status["finished_at"] = now_iso()
        self._write_status()
        self.log(f"PIPELINE STOPPED: {exc}")
        sys.exit(0)  # script itself succeeded at reporting the failure

    # ---- Step 3: provision ----
    def provision(self):
        vms_dir = f"{self.themis_root}/vms/aws"
        tf_bin = "tofu"

        # Workspace selection happens via TF_WORKSPACE (set on every self.run() call) —
        # never via `tofu workspace select/new` here, which would race against the other
        # architectures' concurrent processes in this same directory. The orchestrator
        # (test_all_environments.py) ensures all requested workspaces exist sequentially
        # *before* launching any of these pipelines, so by the time we get here the
        # workspace this env var points at is guaranteed to already exist.
        self.run([tf_bin, "init"], cwd=vms_dir)

        # Stale-state cleanup: drop any aws_instance entries AWS no longer has.
        result = self.run([tf_bin, "state", "list"], cwd=vms_dir, check=False)
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line.startswith("aws_instance.vm["):
                continue
            show = self.run([tf_bin, "state", "show", line], cwd=vms_dir, check=False)
            instance_id = None
            for l in show.stdout.splitlines():
                l = l.strip()
                if l.startswith("id "):
                    instance_id = l.split("=", 1)[1].strip().strip('"')
                    break
            if not instance_id:
                continue
            check_result = self.run(
                ["aws", "ec2", "describe-instances", "--instance-ids", instance_id,
                 "--profile", self.aws_profile],
                check=False,
            )
            if check_result.returncode != 0 and "InvalidInstanceID.NotFound" in check_result.stdout:
                self.log(f"Stale state entry {line} ({instance_id}) not found in AWS, removing")
                self.run([tf_bin, "state", "rm", line], cwd=vms_dir)

        var_files = [
            f"-var-file=tfvars/{self.architecture}.tfvars",
            f"-var-file=tfvars/{self.os_tfvars}.tfvars",
        ]
        if self.architecture in GATEWAY_TFVARS_OVERRIDE:
            var_files.insert(
                1,
                f"-var-file={self.skill_dir}/tfvars-overrides/{GATEWAY_TFVARS_OVERRIDE[self.architecture]}",
            )

        self.run(
            [tf_bin, "apply", *var_files, f"-var=owner={self.owner}", "-parallelism=20", "-auto-approve"],
            cwd=vms_dir,
        )
        self.phase("provision", ok=True)

    # ---- Step 3a: wait for SSH ----
    def wait_ssh(self):
        # Do not poll `cloud-init status` over SSH — output format varies by OS/distro and
        # a single flaky check can false-timeout even when the host is actually ready (this
        # happened live during testing: a host reported "running" for a full 30-attempt/7.5min
        # budget and was confirmed `done` seconds after the timeout fired). Instead watch for
        # /var/lib/cloud/instance/boot-finished via Ansible's own wait_for module — the
        # canonical cross-distro completion signal, checked in parallel across every host in
        # one command instead of one SSH round-trip per host per attempt.
        vms_dir = f"{self.themis_root}/vms/aws"
        result = self.run(["tofu", "output", "-json", "public_ips"], cwd=vms_dir)
        public_ips = json.loads(result.stdout)
        host_list = ",".join(public_ips.values()) + ","  # trailing comma = inline host list, no inventory file needed

        # The 900s wait_for timeout only applies once Ansible has connected. If sshd isn't
        # up yet (common in the ~30s right after tofu apply returns), the command fails fast
        # with UNREACHABLE instead of waiting — retry the connection attempt itself here.
        for attempt in range(1, 11):
            result = self.run(
                ["ansible", "all", "-i", host_list, "-u", "rocky", "--private-key", self.ssh_key_path,
                 "-m", "wait_for", "-a", "path=/var/lib/cloud/instance/boot-finished timeout=900"],
                env_extra={"ANSIBLE_HOST_KEY_CHECKING": "False"},
                check=False,
            )
            if result.returncode == 0:
                break
            self.log(f"wait_ssh attempt {attempt}: not all hosts reachable yet, retrying in 15s")
            time.sleep(15)
        else:
            raise RuntimeError("one or more hosts never became SSH-reachable / cloud-init never finished")
        self.phase("ssh_ready", ok=True)

    # ---- Step 4: inventory + known-gap fixes ----
    def build_inventory(self):
        inventory_dir = self.env_dir / "inventory"
        if inventory_dir.exists():
            import shutil
            shutil.rmtree(inventory_dir)

        self.run(
            [f"{self.skill_dir}/.venv/bin/python3", "scripts/generate_inventory.py",
             "--output-dir", str(inventory_dir)],
            cwd=self.themis_root,
        )

        self.run(
            [f"{self.skill_dir}/.venv/bin/python3", str(self.skill_dir / "scripts" / "fix_gateway_group.py"),
             "--inventory-dir", str(inventory_dir),
             "--tofu-working-dir", f"{self.themis_root}/vms/aws"],
        )
        self.phase("inventory", ok=True)

    # ---- Step 4a/4b: TLS ----
    def tls(self):
        https_enabled = self.run_vars.get("platform_webserver_https_enabled", True)
        if https_enabled is False:
            self.log("platform_webserver_https_enabled is False — skipping TLS")
            self.phase("tls", ok=True, detail="(skipped, https disabled)")
            return

        inventory_dir = self.env_dir / "inventory"
        pki_dir = self.env_dir / "pki"
        pki_dir.mkdir(exist_ok=True)

        for playbook in ("gen_ca_cert.yml", "gen_certs.yml"):
            self.run(
                ["ansible-playbook", f"playbooks/{playbook}",
                 "-i", str(inventory_dir),
                 "-e", f"tls_pki_local_dir={pki_dir}",
                 "-e", f"ansible_ssh_private_key_file={self.ssh_key_path}"],
                cwd=self.tls_repo,
                env_extra=self.ansible_env(),
            )

        ca_crt = pki_dir / "ca.crt"
        (pki_dir / "ca-bundle.crt").write_bytes(ca_crt.read_bytes())

        # Step 4b: wire TLS vars into group_vars, including the mongodb_replica/
        # mongodb_arbiter groups that apply_run_vars.py's prefix map omits.
        group_var_map = {
            "redis_master": {"redis_pki_src_dir": str(pki_dir)},
            "redis_replica": {"redis_pki_src_dir": str(pki_dir)},
            "redis_sentinel": {"redis_pki_src_dir": str(pki_dir)},
            "mongodb": {"mongodb_pki_src_dir": str(pki_dir)},
            "mongodb_primary": {"mongodb_pki_src_dir": str(pki_dir)},
            "mongodb_replica": {"mongodb_pki_src_dir": str(pki_dir)},
            "mongodb_arbiter": {"mongodb_pki_src_dir": str(pki_dir)},
            "platform": {
                "platform_https_pki_src_dir": str(pki_dir),
                "platform_mongodb_pki_src_dir": str(pki_dir),
                "platform_redis_pki_src_dir": str(pki_dir),
            },
            "platform_secondary": {
                "platform_https_pki_src_dir": str(pki_dir),
                "platform_mongodb_pki_src_dir": str(pki_dir),
                "platform_redis_pki_src_dir": str(pki_dir),
            },
            "gateway": {"gateway_pki_src_dir": str(pki_dir)},
        }
        for group, tls_vars in group_var_map.items():
            group_dir = inventory_dir / "group_vars" / group
            if not group_dir.is_dir():
                continue
            content = "---\n" + "\n".join(f"{k}: {v}" for k, v in tls_vars.items()) + "\n"
            (group_dir / "custom_tls.yml").write_text(content)

        self.phase("tls", ok=True)

    # ---- Step 4c: apply run-vars ----
    def apply_run_vars(self, run_vars_path):
        inventory_dir = self.env_dir / "inventory"
        self.run(
            [f"{self.skill_dir}/.venv/bin/python3", str(self.skill_dir / "scripts" / "apply_run_vars.py"),
             "--vars-file", str(run_vars_path),
             "--inventory-dir", str(inventory_dir),
             "--skill-dir", str(self.env_dir),
             "--architecture", self.architecture],
        )
        self.phase("apply_run_vars", ok=True)

    # ---- Step 5: deploy ----
    def deploy(self):
        inventory_dir = self.env_dir / "inventory"
        components = (
            ["redis", "mongodb", "platform", "gateway"]
            if self.deployment_scope == "full"
            else [self.deployment_scope]
        )
        if self.deployment_scope == "none":
            self.phase("deploy", ok=True, detail="(skipped, deployment_scope=none)")
            return

        for component in components:
            # Gateway has no safe default the way redis/mongodb/platform do — the role
            # asserts gateway_release is defined before doing anything else. Rather than
            # hard-failing deployment_scope: full whenever gateway isn't configured
            # (confirmed live 2026-09-10 across all 4 architectures), treat an unset
            # gateway_release as "this run isn't deploying Gateway" and skip it.
            if component == "gateway" and not self.run_vars.get("gateway_release"):
                self.phase("deploy_gateway", ok=True, detail="(skipped, gateway_release not set in run-vars.yml)")
                self.log("gateway_release not set in run-vars.yml — skipping Gateway deploy")
                continue
            try:
                self.run(
                    ["ansible-playbook", f"itential.deployer.{component}", "-i", str(inventory_dir)],
                    cwd=self.themis_root,
                    env_extra=self.ansible_env(),
                )
            except RuntimeError as exc:
                self.phase(f"deploy_{component}", ok=False, detail=str(exc))
                raise RuntimeError(f"deploy_{component} failed: {exc}")
            self.phase(f"deploy_{component}", ok=True)

    # ---- Step 6: certify ----
    def certify(self):
        if self.certify_scope == "none":
            self.phase("certify", ok=True, detail="(skipped, certify_scope=none)")
            return

        inventory_dir = self.env_dir / "inventory"
        reports_dir = self.env_dir / "reports"
        for sub in ("redis", "mongodb", "platform"):
            (reports_dir / sub).mkdir(parents=True, exist_ok=True)

        components = (
            ["redis", "mongodb", "platform"]
            if self.certify_scope == "full"
            else [self.certify_scope]
        )

        any_failed = False
        for component in components:
            result = self.run(
                ["ansible-playbook", f"itential.deployer.certify_{component}", "-i", str(inventory_dir)],
                cwd=self.themis_root,
                env_extra=self.ansible_env(),
                check=False,
            )
            ok = result.returncode == 0
            any_failed = any_failed or not ok
            self.phase(f"certify_{component}", ok=ok)

        # Known gap: Sentinel reports never land locally — fetch them if applicable.
        with open(inventory_dir / "hosts") as f:
            hosts = json.load(f)
        sentinel_group = hosts["all"]["children"].get("redis_sentinel")
        if sentinel_group:
            sentinel_dir = reports_dir / "sentinel"
            sentinel_dir.mkdir(exist_ok=True)
            for host_vars in sentinel_group["hosts"].values():
                ip = host_vars["ansible_host"]
                self.run(
                    ["scp", "-i", self.ssh_key_path, "-o", "StrictHostKeyChecking=no",
                     f"rocky@{ip}:/var/tmp/itential-reports/sentinel/sentinel-report-*.md",
                     str(sentinel_dir)],
                    check=False,
                )

        # Record every generated report's path so the final result clearly states
        # where to look, rather than requiring the reader to re-derive <ENV_DIR>/reports/.
        self.status["reports"] = [str(p) for p in sorted(reports_dir.rglob("*.md"))]
        self._write_status()
        for report_path in self.status["reports"]:
            self.log(f"report: {report_path}")

        if any_failed:
            raise RuntimeError("one or more certify playbooks failed")

    def finish_ok(self):
        self.status["overall"] = "PASSED"
        self.status["finished_at"] = now_iso()
        self._write_status()
        self.log("PIPELINE COMPLETE: PASSED")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--architecture", required=True, choices=["aio", "minimal", "ha2", "asa"])
    parser.add_argument("--skill-dir", required=True)
    parser.add_argument("--run-vars", required=True)
    args = parser.parse_args()

    pipeline = Pipeline(args.architecture, args.skill_dir, args.run_vars)
    try:
        pipeline.provision()
        pipeline.wait_ssh()
        pipeline.build_inventory()
        pipeline.tls()
        pipeline.apply_run_vars(args.run_vars)
        pipeline.deploy()
        pipeline.certify()
        pipeline.finish_ok()
    except RuntimeError as exc:
        pipeline.status["overall"] = "FAILED"
        pipeline.status["error"] = str(exc)
        pipeline.status["finished_at"] = now_iso()
        pipeline._write_status()
        pipeline.log(f"PIPELINE STOPPED: {exc}")
    except Exception as exc:  # noqa: BLE001 - top-level safety net, must never crash silently
        pipeline.status["overall"] = "FAILED"
        pipeline.status["error"] = f"unexpected error: {exc}"
        pipeline.status["finished_at"] = now_iso()
        pipeline._write_status()
        pipeline.log(f"PIPELINE CRASHED: {exc}")


if __name__ == "__main__":
    main()
