#!/usr/bin/env python3
"""
Regression-tests a itential.deployer release against all 4 themis architectures
(aio, minimal, ha2, asa) concurrently.

1. (optional) git fetch + pull origin main on the deployer_repo checkout — only ever
   fast-forwards an already-clean `main` checkout; never switches branches, so a
   deployer_repo checked out on any other branch (local testing, in-progress work)
   is always left untouched.
2. Launches run_environment_pipeline.py for each architecture as an independent
   subprocess — each writes to environments/<arch>/pipeline.log and
   environments/<arch>/status.json. One architecture failing does not stop, slow
   down, or affect the others; they are separate OS processes touching disjoint
   directories and disjoint AWS resources (separate tofu workspaces).
3. Waits for all 4 to finish, then prints a consolidated pass/fail matrix.

Usage:
  python3 test_all_environments.py --skill-dir <dir> --run-vars <path> [--pull-deployer] [--archs aio,ha2,minimal,asa]
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ALL_ARCHS = ["aio", "ha2", "minimal", "asa"]


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pull_deployer(deployer_repo):
    # Never switches branches — only ever fast-forwards a checkout already sitting
    # on main. This is what protects scenario 2 (testing local changes on any branch,
    # including main): being on a non-main branch is treated the same whether the
    # tree is dirty or cleanly committed, since checking `git status` alone would
    # miss the case of a clean checkout on e.g. `dev` with real committed work
    # (confirmed live 2026-09-09 — that case would previously switch to main silently).
    current_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=deployer_repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if current_branch != "main":
        print(f"[{now_iso()}] {deployer_repo} is on branch '{current_branch}', not 'main' — not touching it. "
              f"--pull-deployer is for testing the latest official release: `git checkout main` yourself first "
              f"if that's what you want. Leaving '{current_branch}' as-is to test your local changes on it.")
        return

    print(f"[{now_iso()}] Fetching latest itential.deployer main...")
    subprocess.run(["git", "fetch", "origin", "main"], cwd=deployer_repo, check=True)
    behind = subprocess.run(
        ["git", "rev-list", "--count", "HEAD..origin/main"],
        cwd=deployer_repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if behind == "0":
        print(f"[{now_iso()}] Already up to date with origin/main.")
        return
    status = subprocess.run(["git", "status", "--short"], cwd=deployer_repo, capture_output=True, text=True)
    if status.stdout.strip():
        print(f"[{now_iso()}] WARNING: {deployer_repo} has local changes — not pulling. Resolve manually.")
        return
    subprocess.run(["git", "pull", "origin", "main"], cwd=deployer_repo, check=True)
    rev = subprocess.run(["git", "log", "-1", "--oneline"], cwd=deployer_repo, capture_output=True, text=True).stdout.strip()
    print(f"[{now_iso()}] Pulled {behind} commit(s). Now at: {rev}")


def ensure_workspaces_exist(themis_root, archs):
    """
    Creates any missing tofu workspaces sequentially, one at a time, before any
    concurrent pipeline touches the shared vms/aws directory. This must happen here
    (not inside run_environment_pipeline.py) because `tofu workspace new/select`
    mutates the shared .terraform/environment marker file — running it from multiple
    concurrent processes races (confirmed live 2026-08-12). Once all workspaces exist,
    each pipeline selects its own via the TF_WORKSPACE env var instead, which is
    race-free since it never touches that shared file.
    """
    vms_dir = f"{themis_root}/vms/aws"
    existing = subprocess.run(
        ["tofu", "workspace", "list"], cwd=vms_dir, capture_output=True, text=True, check=True
    ).stdout
    existing_names = {line.strip().lstrip("* ").strip() for line in existing.splitlines() if line.strip()}
    for arch in archs:
        if arch in existing_names:
            continue
        print(f"[{now_iso()}] Creating missing tofu workspace: {arch}")
        subprocess.run(["tofu", "workspace", "new", arch], cwd=vms_dir, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-dir", required=True)
    parser.add_argument("--run-vars", required=True)
    parser.add_argument(
        "--pull-deployer", action="store_true",
        help="Update deployer_repo to the latest origin/main before testing. Only acts when "
             "deployer_repo is already on main — never switches branches, so testing local "
             "changes on any other branch (or uncommitted changes on main) is always safe "
             "without this flag.",
    )
    parser.add_argument("--archs", default=",".join(ALL_ARCHS))
    args = parser.parse_args()

    skill_dir = Path(args.skill_dir)
    archs = args.archs.split(",")

    import yaml
    with open(args.run_vars) as f:
        run_vars = yaml.safe_load(f)

    if args.pull_deployer:
        pull_deployer(run_vars["deployer_repo"])

    ensure_workspaces_exist(run_vars["themis_root"], archs)

    driver = skill_dir / "scripts" / "run_environment_pipeline.py"
    python_bin = skill_dir / ".venv" / "bin" / "python3"

    print(f"[{now_iso()}] Launching {len(archs)} environment(s) in parallel: {', '.join(archs)}")
    procs = {}
    for arch in archs:
        env_dir = skill_dir / "environments" / arch
        env_dir.mkdir(parents=True, exist_ok=True)
        log_path = env_dir / "orchestrator_launch.log"
        with open(log_path, "w") as log_fh:
            proc = subprocess.Popen(
                [str(python_bin), str(driver), "--architecture", arch,
                 "--skill-dir", str(skill_dir), "--run-vars", args.run_vars],
                stdout=log_fh, stderr=subprocess.STDOUT,
            )
        procs[arch] = proc
        print(f"[{now_iso()}]   {arch}: PID {proc.pid}, log at {env_dir}/pipeline.log")

    print(f"[{now_iso()}] Waiting for all {len(archs)} environments to finish...")
    for arch, proc in procs.items():
        proc.wait()
        print(f"[{now_iso()}]   {arch}: process exited (rc={proc.returncode})")

    print(f"\n[{now_iso()}] ===== RESULTS =====")
    all_passed = True
    for arch in archs:
        status_path = skill_dir / "environments" / arch / "status.json"
        if not status_path.exists():
            print(f"{arch}: NO STATUS FILE (crashed before first phase?)")
            all_passed = False
            continue
        status = json.loads(status_path.read_text())
        overall = status.get("overall", "UNKNOWN")
        all_passed = all_passed and overall == "PASSED"
        print(f"\n{arch}: {overall}")
        for phase, result in status.get("phases", {}).items():
            marker = "✓" if result == "PASSED" else "✗"
            print(f"  {marker} {phase}: {result}")
        if status.get("error"):
            print(f"  error: {status['error']}")
        if status.get("reports"):
            print("  reports:")
            for report_path in status["reports"]:
                print(f"    {report_path}")

    print(f"\n[{now_iso()}] Overall: {'ALL PASSED' if all_passed else 'AT LEAST ONE FAILURE'}")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
