---
name: themis-aws-deploy
description: >
  Provisions AWS VMs and deploys Itential Automation Platform (IAP) end-to-end using the Themis
  project (OpenTofu + Ansible). Use this skill whenever the user wants to: spin up VMs and install
  IAP on AWS, provision a validated architecture (aio, minimal, ha2, asa), deploy Itential Platform
  with Redis and MongoDB, run tofu apply followed by ansible deploy, or stand up an Itential
  environment from scratch. Trigger even for phrases like "spin up IAP", "deploy itential on AWS",
  "provision an ha2 environment", "stand up VMs for IAP", or "create an itential cluster".
---

# Themis — AWS Provision + Deploy Skill

Full pipeline: **Provision (OpenTofu) → Inventory → TLS → Deploy IAP → Certify**

Read `references/architectures.md` for host groups per design.
Read `references/preflight.md` if any pre-flight check fails.
Read `references/troubleshooting.md` if deployment errors occur.

All runtime state lives under the skill directory. Nothing in `/tmp`, nothing in memory.

**Path resolution:** Claude — derive `SKILL_DIR` from the path of this file. Use the literal resolved path in every bash command. Do not use `$SKILL_DIR` as a shell variable — shell state does not persist between tool calls. Substitute the actual absolute path everywhere `<SKILL_DIR>` appears below.

**Per-environment isolation:** `<ENV_DIR>` = `<SKILL_DIR>/environments/<architecture>/`. Every architecture's `inventory/`, `pki/`, and `reports/` live under its own `<ENV_DIR>` — never in a bare shared `<SKILL_DIR>/inventory` etc. This is what makes it safe to run more than one architecture at a time (see "Running Multiple Environments in Parallel" below): each one only ever touches its own directory, its own tofu workspace, and its own AWS instances.

---

## Execution Mode

**Run fully autonomously.** Once Step 1's required-field check passes, execute every subsequent step through Step 6 end-to-end without pausing to ask the user for confirmation — this includes `tofu apply`, provisioning, deploying, and certifying. Invoking this skill on a validated `run-vars.yml` is itself the user's authorization to take these actions; do not use AskUserQuestion for routine execution.

The known decision points below (stale tofu state, missing `gateway` groups, `mongodb_replica` TLS wiring, Sentinel report location, concurrent-architecture workspace isolation) already have a specified default — apply it automatically, do not ask.

Only stop and surface to the user for:
- A required field missing/empty in Step 1 (a config problem, not a confirmation)
- An actual task failure during deploy/certify (per the "On failure" instructions in Steps 5/6)
- A genuine ambiguity with no safe default that isn't already covered below

### Expected duration (no approval pauses)

Measured 2026-08-11 end-to-end (provision → certify), Rocky 9, `t3.medium`/`t3.large`, `pe-team-sbx`:

| Architecture | Hosts | Provision → certify |
|---|---|---|
| `aio` | 1 | ~45–70 min |
| `ha2` | 9 | ~44 min |
| `minimal` | 4 | ~57 min |
| `asa` | 18 | ~68 min |

This is real AWS + Ansible latency (cloud-init boot, package installs, TLS cert generation, `npm install` across the configured `platform_adapters`, MongoDB replica-set formation) — not approval waiting. With the defaults above in place there are zero decision pauses in this window, so total run time is essentially this table. Host count doesn't scale duration linearly since most Ansible tasks fan out in parallel across hosts.

---

## Running Multiple Environments in Parallel

Use this when the goal is testing a change (e.g. a new `itential.deployer` release) against **more than one architecture at once** — most commonly all 4. For a single one-off architecture request ("deploy ha2"), Steps 1–6 below (walked manually, one architecture at a time) are simpler and still the right tool.

**Why this is safe:** each architecture already gets its own tofu workspace (state isolation) and, per the `<ENV_DIR>` convention above, its own `inventory/`/`pki/`/`reports/` (filesystem isolation). Nothing shared gets written to except the read-only `collections/` symlinks and the Python venv, neither of which any environment mutates at runtime. Four environments' `tofu apply`/`ansible-playbook` processes running concurrently is not a real resource problem at this scale (~1–18 hosts each).

**Failure independence:** unlike a single manual run (which stops immediately on the first failure — see Steps 5/6), each environment here is its own OS process with its own log and status file. One architecture failing does not pause, slow down, or affect the others — the point of this mode is a complete pass/fail matrix across all of them, not a single earliest failure.

### Scripts

| Script | Purpose |
|---|---|
| `scripts/fix_gateway_group.py` | Deterministic version of the "known gap" manual fix in Step 4 — adds the `gateway` inventory group generically for any architecture (dedicated gateway instance(s) if present, else reuses the single aio host). Idempotent. |
| `scripts/run_environment_pipeline.py` | Runs the entire Step 3 → Step 6 pipeline for one `--architecture`, entirely under its `<ENV_DIR>`. Writes `<ENV_DIR>/pipeline.log` (full output) and `<ENV_DIR>/status.json` (phase-by-phase PASSED/FAILED). Bakes in every known-gap fix from this file automatically (gateway group, `mongodb_replica`/`mongodb_arbiter` TLS wiring, explicit SSH key on TLS gen, `wait_for boot-finished` with a connectivity-retry wrapper, forks/pipelining, stale-state cleanup, `TF_WORKSPACE`-based workspace selection so concurrent architectures never race on the shared `.terraform/environment` file). |
| `scripts/test_all_environments.py` | Orchestrator. Optionally pulls latest `itential.deployer` main first, then launches `run_environment_pipeline.py` for each requested architecture as an independent subprocess, waits for all of them, and prints a consolidated pass/fail matrix. |

### Invocation

```bash
# Pull the latest deployer release and test it against all 4 architectures:
<SKILL_DIR>/.venv/bin/python3 <SKILL_DIR>/scripts/test_all_environments.py \
  --skill-dir <SKILL_DIR> \
  --run-vars <SKILL_DIR>/run-vars.yml \
  --pull-deployer

# Test against a subset only:
<SKILL_DIR>/.venv/bin/python3 <SKILL_DIR>/scripts/test_all_environments.py \
  --skill-dir <SKILL_DIR> \
  --run-vars <SKILL_DIR>/run-vars.yml \
  --archs ha2,asa
```

`run-vars.yml` is shared across all architectures in this mode — its `architecture:` field is ignored (each subprocess's `--architecture` argument is authoritative). Everything else in it (credentials, `gateway_release`/`gateway_whl_file`, `platform_adapters`, TLS overrides, `deployment_scope`, `certify_scope`) applies identically to every environment tested.

**This does not block the conversation** — launch it, then check progress by reading `<ENV_DIR>/status.json` / tailing `<ENV_DIR>/pipeline.log` for any architecture, or just wait for the orchestrator process to exit and print its final matrix. Running a single architecture through `run_environment_pipeline.py` directly (omitting the orchestrator) is also valid when only one needs the scripted/unattended treatment.

---

## Step 1 — Read run-vars.yml

```bash
sed 's/\(repository_password:.*\)/repository_password: [REDACTED]/' <SKILL_DIR>/run-vars.yml
```

**Required fields — stop and tell the user if any are empty:**
`architecture`, `os`, `themis_root`, `owner`, `deployer_repo`, `tls_repo`, `aws_profile`, `ssh_key_path`, `repository_username`, `repository_password`

| Key | Used in |
|-----|---------|
| `architecture` | tofu tfvars, inventory generation |
| `os` | tofu tfvars (`al2023` maps to `amazon2023.tfvars`, all others `<os>.tfvars`) |
| `themis_root` | all `cd` targets |
| `owner` | tofu `-var owner=` |
| `deployer_repo` | collections symlink |
| `tls_repo` | collections symlink |
| `aws_profile` | AWS CLI `--profile` flag |
| `ssh_key_path` | SSH key for Ansible + injected as `ansible_ssh_private_key_file` |
| `deployment_scope` | Step 5 |
| `certify_scope` | Step 6 |
| `artifacts_retention_days` | Artifact Setup |

All other vars are Ansible deployer vars — distributed to `group_vars/` in Step 4c.

---

## Step 2 — Pre-flight Checks

```bash
which tofu || which terraform
which ansible-playbook
which python3
aws sts get-caller-identity --profile <aws_profile>
ls -la <ssh_key_path>
ansible-galaxy collection list | grep itential.deployer
ansible-galaxy collection list | grep itential.tls
ansible-galaxy collection list | grep community.crypto
```

**Also verify `themis_root`, `deployer_repo`, and `tls_repo` are real, correct checkouts — do this before Local Collections Setup, and always before Step 3.** None of the checks above catch a missing or wrong path in these three, and unlike the others, a bad `deployer_repo`/`tls_repo` does not fail fast: `deployer_repo` is only ever referenced through a symlink (Local Collections Setup), which succeeds even when dangling, so a bad path silently passes every step until Step 5's first `ansible-playbook itential.deployer.*` call — by which point Step 3 (provision), Step 3a (wait for SSH), and Step 4/4a (inventory + TLS cert generation against the live hosts) have already run. A bad `tls_repo` fails one step earlier, at Step 4a's `cd <tls_repo>` — still after Step 3 has provisioned real AWS instances. Confirmed live 2026-09-09.

```bash
[ -f <themis_root>/scripts/generate_inventory.py ] && [ -d <themis_root>/vms/aws ] \
  && echo "themis_root OK" || echo "themis_root MISSING/WRONG"

[ -d <deployer_repo>/roles/platform ] && [ -d <deployer_repo>/roles/gateway ] \
  && echo "deployer_repo OK" || echo "deployer_repo MISSING/WRONG"

[ -f <tls_repo>/playbooks/gen_ca_cert.yml ] && [ -f <tls_repo>/playbooks/gen_certs.yml ] \
  && echo "tls_repo OK" || echo "tls_repo MISSING/WRONG"
```

If anything fails, refer to `references/preflight.md`.

---

## Local Collections Setup

```bash
mkdir -p <SKILL_DIR>/collections/ansible_collections/itential
ln -sfn <deployer_repo> <SKILL_DIR>/collections/ansible_collections/itential/deployer
ln -sfn <tls_repo>      <SKILL_DIR>/collections/ansible_collections/itential/tls
ls -la <SKILL_DIR>/collections/ansible_collections/itential/
```

All ansible-playbook calls in this skill must be prefixed with:
```
ANSIBLE_COLLECTIONS_PATH=<SKILL_DIR>/collections:~/.ansible/collections ANSIBLE_FORKS=20 ANSIBLE_PIPELINING=True
```
This is the only way to ensure the local collection branches are used and performance settings apply — env vars set via `export` do not persist between tool calls.

**Performance:** this skill runs on Ansible's bare defaults otherwise (`forks=5`, `pipelining=False`) — with 5 forks, an 18-host `asa` play processes hosts in 4 sequential waves instead of 1. `ANSIBLE_FORKS=20` covers every architecture's host count in a single wave; `ANSIBLE_PIPELINING=True` cuts SSH round-trips per task. Both are safe performance-only changes with no effect on correctness or failure reporting.

---

## Python Venv Setup

macOS Homebrew Python blocks system-wide pip installs. Use a venv in the skill dir for all Python commands.

```bash
[ -d <SKILL_DIR>/.venv ] || python3 -m venv <SKILL_DIR>/.venv
<SKILL_DIR>/.venv/bin/pip install -r <themis_root>/scripts/requirements.txt pyyaml -q
```

Use `<SKILL_DIR>/.venv/bin/python3` for all Python calls in this skill.

---

## Artifact Setup

Namespaced by architecture (`<SKILL_DIR>/artifacts/<architecture>/<timestamp>/`) so concurrent environments — and repeated runs of the same one, e.g. testing successive `itential.deployer` releases — never collide.

```bash
mkdir -p <SKILL_DIR>/artifacts/<architecture>
find <SKILL_DIR>/artifacts/<architecture> -maxdepth 1 -mindepth 1 -type d -mtime +<artifacts_retention_days> -exec rm -rf {} +
```

Run this to get the artifact dir timestamp:
```bash
date +%Y-%m-%d_%H%M%S
```

Record the output. Use the literal value in all subsequent artifact paths:
```bash
mkdir -p <SKILL_DIR>/artifacts/<architecture>/<timestamp>/inventory <SKILL_DIR>/artifacts/<architecture>/<timestamp>/pki <SKILL_DIR>/artifacts/<architecture>/<timestamp>/reports
echo "Artifact dir: <SKILL_DIR>/artifacts/<architecture>/<timestamp>"
```

---

## Step 3 — Provision AWS VMs

**Running a new architecture alongside one that must stay alive?** Use a separate tofu workspace so state is fully isolated — the `vms/aws` config has no backend/workspace split by default (single `default` workspace, `aws_instance.vm` is `for_each`-keyed by instance name from tfvars), so applying a different architecture's tfvars in the same workspace would destroy instances not in the new list.
```bash
tofu -chdir=<themis_root>/vms/aws workspace select <architecture> 2>/dev/null || \
  tofu -chdir=<themis_root>/vms/aws workspace new <architecture>
```
Do this automatically whenever an existing deployment should be preserved — no need to ask. Workspace selection persists on disk (`.terraform/environment`), so it carries across separate tool calls as long as the working dir doesn't change.

**`minimal`/`ha2`/`asa` have no gateway VM by default** — see `references/architectures.md`'s "Gateway gap" section for exact instance counts. If the deployment needs Gateway/IAG (check `deployment_scope` — `full` or `gateway`), add `<GATEWAY_TFVARS>` below — a `-var-file` loaded *after* the design's own tfvars, already prepared, do not ask:

| Architecture | `<GATEWAY_TFVARS>` |
|---|---|
| `aio` | *(none — single host already covers gateway, see Step 4's gateway-group fix)* |
| `minimal` | `-var-file=<SKILL_DIR>/tfvars-overrides/minimal-with-gateway.tfvars` |
| `ha2` | `-var-file=<SKILL_DIR>/tfvars-overrides/ha2-with-gateway.tfvars` |
| `asa` | `-var-file=<SKILL_DIR>/tfvars-overrides/asa-with-gateway.tfvars` (adds 2 gateway VMs, one per site) |

```bash
cd <themis_root>/vms/aws

tofu init

tofu plan \
  -var-file=tfvars/<architecture>.tfvars \
  <GATEWAY_TFVARS> \
  -var-file=tfvars/<os_tfvars>.tfvars \
  -var owner=<owner>
```

**Before applying, check for orphaned state:** for every `aws_instance.vm[...]` in `tofu state list`, verify the instance still exists in AWS (`aws ec2 describe-instances --instance-ids <id> --profile <aws_profile>`). If AWS returns `InvalidInstanceID.NotFound`, the state entry is stale (e.g. the instance was terminated outside of tofu) — run `tofu state rm 'aws_instance.vm["<key>"]'` automatically before applying. This is always safe (the resource is already gone) and does not need confirmation.

`-parallelism=20` covers every architecture's instance count in one batch instead of tofu's default of 10 (two batches for `asa`'s 18 hosts):

```bash
tofu apply \
  -var-file=tfvars/<architecture>.tfvars \
  <GATEWAY_TFVARS> \
  -var-file=tfvars/<os_tfvars>.tfvars \
  -var owner=<owner> \
  -parallelism=20 \
  -auto-approve
```

> ⚠️ If provisioning fails mid-way, run `tofu destroy` before retrying to avoid orphaned AWS resources.

Note the public IP from the apply output — needed for the SSH wait below.

---

## Step 3a — Wait for SSH

After provisioning, wait for every VM to finish booting before proceeding.

**Do not poll `cloud-init status` over SSH** — its output format varies by OS/distro version, and a single flaky check can produce a false timeout even when the host is actually ready (this happened live during testing — a host reported `running` for 7.5 minutes of polling and was confirmed `done` seconds after the timeout fired). Instead, watch for `/var/lib/cloud/instance/boot-finished` — the canonical cross-distro signal that cloud-init has fully completed on every supported distro, checked directly by Ansible's own `wait_for` module rather than by parsing command output:

**Retry the connection itself, separately from the boot-finished wait:** the 900s `wait_for` timeout only applies *after* Ansible successfully connects — if sshd isn't up yet (common in the first ~30s right after `tofu apply` returns, before the instance has booted far enough), the whole command fails fast with `UNREACHABLE` instead of waiting. Wrap it in a short retry loop for that initial connectivity window:

```bash
cd <themis_root>/vms/aws

PUBLIC_IPS=$(tofu output -json public_ips | <SKILL_DIR>/.venv/bin/python3 -c "import json,sys; print(','.join(json.load(sys.stdin).values()))")

for i in $(seq 1 10); do
  ANSIBLE_HOST_KEY_CHECKING=False ansible all \
    -i "${PUBLIC_IPS}," \
    -u rocky \
    --private-key <ssh_key_path> \
    -m wait_for \
    -a "path=/var/lib/cloud/instance/boot-finished timeout=900" \
    && break
  echo "[$i] not all hosts reachable yet, retrying in 15s"
  sleep 15
done
```

The trailing comma in `-i "${PUBLIC_IPS},"` tells Ansible to treat this as an inline comma-separated host list rather than a file path — no inventory file exists yet at this point in the pipeline (that's Step 4), and this checks every host in parallel in one command instead of polling them one at a time.

If any host times out, check `/var/log/cloud-init-output.log` on that instance. Do not proceed to Step 4 until this command exits successfully.

---

## Step 4 — Generate Ansible Inventory

```bash
cd <themis_root>

<SKILL_DIR>/.venv/bin/python3 scripts/generate_inventory.py --output-dir <ENV_DIR>/inventory
```

Verify groups were created:
```bash
<SKILL_DIR>/.venv/bin/python3 -c "import json; d=json.load(open('<ENV_DIR>/inventory/hosts')); print(list(d['all']['children'].keys()))"
```

**Known gap: `generate_inventory.py` never creates a `gateway` group for any of the 4 designs** — see `references/architectures.md`'s "Gateway gap" section. If the run needs Gateway (`deployment_scope` is `full` or `gateway`) and no `gateway` group is present, fix it automatically with `scripts/fix_gateway_group.py` — do not ask, and do not hand-edit the inventory JSON:

```bash
<SKILL_DIR>/.venv/bin/python3 <SKILL_DIR>/scripts/fix_gateway_group.py \
  --inventory-dir <ENV_DIR>/inventory \
  --tofu-working-dir <themis_root>/vms/aws
```

Generic across all 4 architectures: adds any tofu instance whose name contains "gateway" to a flat `gateway` group (covers `minimal`/`ha2`'s single gateway VM and `asa`'s two, `dc1-gateway`/`dc2-gateway`), or reuses the single existing host if none exist (`aio`). Idempotent — safe to re-run. For `minimal`/`ha2`/`asa` this requires the extra gateway VM to already exist from Step 3's tfvars override (see below); `aio` needs no extra VM.

---

## Step 4a — Generate TLS Certificates

**TLS gate:** Check whether `platform_webserver_https_enabled` is explicitly set to `false` in `run-vars.yml`.

```bash
<SKILL_DIR>/.venv/bin/python3 -c "
import yaml
v = yaml.safe_load(open('<SKILL_DIR>/run-vars.yml')).get('platform_webserver_https_enabled', True)
print('skip' if v is False else 'run')
"
```

- Output `skip` → skip Steps 4a and 4b entirely, proceed to Step 4c.
- Output `run` → proceed with the TLS steps below.

**Known gap: this step needs SSH connectivity, but `ansible_ssh_private_key_file` isn't injected into `group_vars/all` until Step 4c.** This only ever "worked" by accident when a prior run's `group_vars/all` was still sitting in `<ENV_DIR>/inventory` (the generator merges into `group_vars/` rather than replacing it) — a genuinely fresh inventory directory will fail here with `Permission denied (publickey,...)`. Always pass the SSH key explicitly on both calls below, do not rely on leftover state:

```bash
mkdir -p <ENV_DIR>/pki

cd <tls_repo>

ANSIBLE_COLLECTIONS_PATH=<SKILL_DIR>/collections:~/.ansible/collections ANSIBLE_FORKS=20 ANSIBLE_PIPELINING=True \
  ansible-playbook playbooks/gen_ca_cert.yml \
  -i <ENV_DIR>/inventory \
  -e "tls_pki_local_dir=<ENV_DIR>/pki" \
  -e "ansible_ssh_private_key_file=<ssh_key_path>"

ANSIBLE_COLLECTIONS_PATH=<SKILL_DIR>/collections:~/.ansible/collections ANSIBLE_FORKS=20 ANSIBLE_PIPELINING=True \
  ansible-playbook playbooks/gen_certs.yml \
  -i <ENV_DIR>/inventory \
  -e "tls_pki_local_dir=<ENV_DIR>/pki" \
  -e "ansible_ssh_private_key_file=<ssh_key_path>"

cp <ENV_DIR>/pki/ca.crt <ENV_DIR>/pki/ca-bundle.crt
```

Verify:
```bash
ls <ENV_DIR>/pki/
# Expected: ca.key ca.crt ca-bundle.crt <hostname>.key <hostname>.crt per host
```

---

## Step 4b — Wire TLS Vars into Group Vars

**Known gap fixed inline below: `mongodb_replica` and `mongodb_arbiter` are included in this loop.** `gen_ha2()` creates a `mongodb_replica` group and `gen_asa()` also creates `mongodb_arbiter` for secondary/arbiter MongoDB members — Ansible group_vars don't inherit across sibling groups, so without these, replica/arbiter nodes never get `mongodb_pki_src_dir` and their certs never get copied.

```bash
for group_dir in redis_master redis_replica redis_sentinel mongodb mongodb_primary mongodb_replica mongodb_arbiter platform platform_secondary gateway; do
  dir="<ENV_DIR>/inventory/group_vars/${group_dir}"
  [ -d "$dir" ] || continue
  case "$group_dir" in
    redis_master|redis_replica|redis_sentinel)
      printf -- "---\nredis_pki_src_dir: <ENV_DIR>/pki\n" > "${dir}/custom_tls.yml" ;;
    mongodb|mongodb_primary|mongodb_replica|mongodb_arbiter)
      printf -- "---\nmongodb_pki_src_dir: <ENV_DIR>/pki\n" > "${dir}/custom_tls.yml" ;;
    platform|platform_secondary)
      printf -- "---\nplatform_https_pki_src_dir: <ENV_DIR>/pki\nplatform_mongodb_pki_src_dir: <ENV_DIR>/pki\nplatform_redis_pki_src_dir: <ENV_DIR>/pki\n" > "${dir}/custom_tls.yml" ;;
    gateway)
      printf -- "---\ngateway_pki_src_dir: <ENV_DIR>/pki\n" > "${dir}/custom_tls.yml" ;;
  esac
  echo "Wrote ${dir}/custom_tls.yml"
done
```

---

## Step 4c — Apply Run Variables

`ssh_key_path` is automatically injected as `ansible_ssh_private_key_file` into `group_vars/all`. Credentials go to `group_vars/all/` — do not pass them as `-e` flags on playbook calls.

```bash
<SKILL_DIR>/.venv/bin/python3 <SKILL_DIR>/scripts/apply_run_vars.py \
  --vars-file <SKILL_DIR>/run-vars.yml \
  --inventory-dir <ENV_DIR>/inventory \
  --skill-dir <SKILL_DIR> \
  --architecture <architecture>
```

**Always pass `--architecture` explicitly** — do not rely on `run-vars.yml`'s own `architecture:` field. The script's AIO+TLS `platform_mongo_url` fix only applies correctly when it knows the real architecture being deployed; in the concurrent orchestrator all 4 runs share one `run-vars.yml`, so that field is stale for every architecture but one (confirmed live 2026-09-02: it silently broke MongoDB connectivity for `itential-platform` on all 4 architectures by injecting a bogus single-host mongo URL built from a `platform` node's own hostname).

**Testing a different Platform release:** `themis`'s own `inventories/common/platform_common.yml`/`platform_site_common.yml` pin `platform_release`/`platform_packages` to a fixed version for every architecture, independent of `run-vars.yml` — set both in `run-vars.yml` to override. This only works because `apply_run_vars.py` writes `group_vars/<group>/zz_run_vars.yml`, not `custom.yml`: `generate_inventory.py` resolves those `inventories/common/` symlinks into real files inside the generated `group_vars/platform/`, and Ansible loads `group_vars/<group>/*.yml` alphabetically with later files winning — a plain `custom.yml` sorts *before* `platform_common.yml`/`platform_site_common.yml` and gets silently overridden back to themis's hardcoded values (confirmed live 2026-09-10). `zz_` guarantees this always wins.

---

## Step 4d — Capture Inventory and PKI Artifacts

```bash
cp -r <ENV_DIR>/inventory/* <SKILL_DIR>/artifacts/<architecture>/<timestamp>/inventory/
cp -r <ENV_DIR>/pki/*       <SKILL_DIR>/artifacts/<architecture>/<timestamp>/pki/
```

---

## Step 5 — Deploy IAP

Run based on **deployment_scope** from `run-vars.yml`. Stop immediately if any step fails — do not proceed to the next component.

**On failure:** extract and show the user:
- The failing task name
- Every `fatal:` line from the output
- The host(s) affected

Do not guess at the cause — surface the raw error and stop.

### Full deploy (scope: `full`)

**Gateway has no safe default the way redis/mongodb/platform do** — its role asserts `gateway_release` is defined before doing anything else, so `deployment_scope: full` with no `gateway_release` set in `run-vars.yml` hard-fails deploy entirely (confirmed live 2026-09-10, identically across all 4 architectures). Check for it first and skip Gateway if unset — this is not a failure, it means this run isn't deploying Gateway:

```bash
<SKILL_DIR>/.venv/bin/python3 -c "
import yaml
v = yaml.safe_load(open('<SKILL_DIR>/run-vars.yml'))
print('run' if v.get('gateway_release') else 'skip')
"
```

```bash
cd <themis_root>

ANSIBLE_COLLECTIONS_PATH=<SKILL_DIR>/collections:~/.ansible/collections ANSIBLE_FORKS=20 ANSIBLE_PIPELINING=True \
  ansible-playbook itential.deployer.redis -i <ENV_DIR>/inventory

ANSIBLE_COLLECTIONS_PATH=<SKILL_DIR>/collections:~/.ansible/collections ANSIBLE_FORKS=20 ANSIBLE_PIPELINING=True \
  ansible-playbook itential.deployer.mongodb -i <ENV_DIR>/inventory

ANSIBLE_COLLECTIONS_PATH=<SKILL_DIR>/collections:~/.ansible/collections ANSIBLE_FORKS=20 ANSIBLE_PIPELINING=True \
  ansible-playbook itential.deployer.platform -i <ENV_DIR>/inventory
```

Only if the check above printed `run`:
```bash
ANSIBLE_COLLECTIONS_PATH=<SKILL_DIR>/collections:~/.ansible/collections ANSIBLE_FORKS=20 ANSIBLE_PIPELINING=True \
  ansible-playbook itential.deployer.gateway -i <ENV_DIR>/inventory
```

### Single component (scope: `redis` | `mongodb` | `platform` | `gateway`)

| Scope | Playbook |
|-------|----------|
| `redis` | `itential.deployer.redis` |
| `mongodb` | `itential.deployer.mongodb` |
| `platform` | `itential.deployer.platform` |
| `gateway` | `itential.deployer.gateway` |

```bash
cd <themis_root>
ANSIBLE_COLLECTIONS_PATH=<SKILL_DIR>/collections:~/.ansible/collections ANSIBLE_FORKS=20 ANSIBLE_PIPELINING=True \
  ansible-playbook itential.deployer.<scope> -i <ENV_DIR>/inventory
```

If errors occur, refer to `references/troubleshooting.md`.

---

## Step 6 — Certify

Run based on **certify_scope** from `run-vars.yml`. Skip entirely if `none`. No gateway certify playbook exists.

```bash
mkdir -p <ENV_DIR>/reports/redis <ENV_DIR>/reports/mongodb <ENV_DIR>/reports/platform
```

**On failure:** extract and show the user:
- The failing task name
- Every `fatal:` line from the output
- The host(s) affected

Do not proceed to the next certify playbook until the failure is surfaced and acknowledged.

**Known gap: Sentinel reports never reach `<ENV_DIR>/reports/` locally.** `apply_run_vars.py` only auto-injects `redis_certify_report_dir_local`, `mongodb_certify_report_dir_local`, `platform_certify_report_dir_local` — there's no local-report var for the Sentinel play inside `certify_redis`, so its report stays remote-only at `/var/tmp/itential-reports/sentinel/` on each host. If the inventory has a `redis_sentinel` group, fetch these automatically right after running `certify_redis` (before evaluating reports in Step 6a):
```bash
mkdir -p <ENV_DIR>/reports/sentinel
# for each host in the redis_sentinel group:
scp -i <ssh_key_path> -o StrictHostKeyChecking=no rocky@<ansible_host>:/var/tmp/itential-reports/sentinel/sentinel-report-*.md <ENV_DIR>/reports/sentinel/
```

### Full certify (scope: `full`)

```bash
cd <themis_root>

ANSIBLE_COLLECTIONS_PATH=<SKILL_DIR>/collections:~/.ansible/collections ANSIBLE_FORKS=20 ANSIBLE_PIPELINING=True \
  ansible-playbook itential.deployer.certify_redis -i <ENV_DIR>/inventory

ANSIBLE_COLLECTIONS_PATH=<SKILL_DIR>/collections:~/.ansible/collections ANSIBLE_FORKS=20 ANSIBLE_PIPELINING=True \
  ansible-playbook itential.deployer.certify_mongodb -i <ENV_DIR>/inventory

ANSIBLE_COLLECTIONS_PATH=<SKILL_DIR>/collections:~/.ansible/collections ANSIBLE_FORKS=20 ANSIBLE_PIPELINING=True \
  ansible-playbook itential.deployer.certify_platform -i <ENV_DIR>/inventory
```

### Single component (scope: `redis` | `mongodb` | `platform`)

| Scope | Playbook |
|-------|----------|
| `redis` | `itential.deployer.certify_redis` |
| `mongodb` | `itential.deployer.certify_mongodb` |
| `platform` | `itential.deployer.certify_platform` |

```bash
cd <themis_root>
ANSIBLE_COLLECTIONS_PATH=<SKILL_DIR>/collections:~/.ansible/collections ANSIBLE_FORKS=20 ANSIBLE_PIPELINING=True \
  ansible-playbook itential.deployer.certify_<scope> -i <ENV_DIR>/inventory
```

### Step 6a — Evaluate Reports

After each certify playbook, read the fetched reports immediately — do not wait for all components to finish.

```bash
ls <ENV_DIR>/reports/
```

Read every `.md` file in the relevant directory (one per host in HA architectures).

Evaluate against `references/healthy-certify.md`:
- State PASSED, FAILED, or WARNING per component
- Call out every deviation with hostname and section
- Stop and surface failures before running the next certify playbook

### Step 6b — Capture Certify Reports

```bash
cp -r <ENV_DIR>/reports/ <SKILL_DIR>/artifacts/<architecture>/<timestamp>/reports/
ls <SKILL_DIR>/artifacts/<architecture>/<timestamp>/reports/
```

### End-of-run summary

Once Step 6 finishes (or Step 5 if `certify_scope: none`), report back to the user with:
- **Deployment state** — PASSED/FAILED/SKIPPED per component (redis, mongodb, platform, gateway), matching what actually ran per `deployment_scope`
- **Certify verdict** — PASSED/FAILED/WARNING per component per Step 6a's evaluation, not just "certify ran"
- **Report file locations** — the full path of every `.md` file under `<ENV_DIR>/reports/` (both the live copy and the `<SKILL_DIR>/artifacts/<architecture>/<timestamp>/reports/` snapshot), so the user can open them directly without having to derive the path themselves

`scripts/run_environment_pipeline.py` does this automatically for the orchestrated flow — it records every report path into `<ENV_DIR>/status.json`'s `reports` list, and `test_all_environments.py`'s final matrix prints them per architecture alongside PASSED/FAILED phases.

### Certify only (deployment_scope: `none`)

Skip Steps 3–5. Run Step 6 directly. `<ENV_DIR>/inventory` must exist from a prior run.

---

## Destroy

Read `architecture`, `os`, `owner` from `run-vars.yml`. **Pass the same `<GATEWAY_TFVARS>` (see Step 3's table) that was used at apply time** — if it's omitted here and a gateway override VM exists in state, tofu's view of the desired instance set no longer matches what it applied, which is best avoided even though `destroy` targets what's actually in state either way.

```bash
cd <themis_root>/vms/aws

tofu destroy \
  -var-file=tfvars/<architecture>.tfvars \
  <GATEWAY_TFVARS> \
  -var-file=tfvars/<os_tfvars>.tfvars \
  -var owner=<owner> \
  -auto-approve
```

> ⚠️ Always pass `-var owner=<value>` explicitly.
