# Pre-flight Checks Reference

Run all checks before provisioning. Fix failures before proceeding.

---

## 1. OpenTofu / Terraform

```bash
which tofu || which terraform
tofu version   # requires >= 1.6
```

**If missing:** Install OpenTofu from https://opentofu.org/docs/intro/install/
Use `terraform` as fallback if `tofu` is not available — commands are identical.

---

## 2. Ansible

```bash
which ansible-playbook
ansible --version   # requires >= 2.11, recommend 2.13+
```

**If missing:** `pip install ansible`

---

## 3. Python

```bash
which python3
python3 --version   # requires >= 3.8
```

**If missing:** Install via system package manager or https://python.org

---

## 4. AWS Credentials

```bash
aws sts get-caller-identity --profile pe-team-sbx
```

**If expired/missing:**
```bash
aws sso login --profile pe-team-sbx
```

Verify `~/.aws/credentials` or `~/.aws/config` has the `pe-team-sbx` profile defined.

---

## 5. SSH Key

```bash
ls -la ~/.ssh/pet-east1.open.pem
```

**If missing:** Obtain `pet-east1.open.pem` from your team and place it in `~/.ssh/`:
```bash
chmod 400 ~/.ssh/pet-east1.open.pem
```

Without this key, Ansible cannot SSH into the provisioned EC2 instances.

---

## 6. Itential Deployer Collection

```bash
ansible-galaxy collection list | grep itential.deployer
```

**If missing:**
```bash
ansible-galaxy collection install git+https://github.com/itential/itential.deployer.git,main
```

---

## 6a. Required Local Repo Checkouts

`themis_root`, `deployer_repo`, and `tls_repo` in `run-vars.yml` must point at real, valid checkouts of these three repos. This is not covered by any other check above — item 6's `ansible-galaxy collection list` only tells you whether a *globally-installed* copy of `itential.deployer` exists, which is irrelevant here, since this skill always uses the locally symlinked `deployer_repo`/`tls_repo` checkout instead (Local Collections Setup takes precedence over anything in `~/.ansible/collections`).

```bash
git clone git@gitlab.com:itential/platform-engineering/themis.git
git clone git@github.com:itential/itential.deployer.git
git clone git@gitlab.com:itential/platform-engineering/ansible/collections/itential.tls.git
```

**Why this matters more than a typical missing-dependency check:** a bad `deployer_repo` or `tls_repo` path does not fail immediately. `deployer_repo` is only ever referenced through a symlink, which succeeds even when it points at nothing — the first real failure is Step 5's `ansible-playbook itential.deployer.*` call, by which point provisioning, SSH wait, inventory generation, and TLS cert generation have already run against real AWS instances. A bad `tls_repo` fails one step earlier (Step 4a), still after provisioning. Verify all three *before* Step 3, not after a failure:

```bash
[ -f <themis_root>/scripts/generate_inventory.py ] && [ -d <themis_root>/vms/aws ] \
  && echo "themis_root OK" || echo "themis_root MISSING/WRONG"

[ -d <deployer_repo>/roles/platform ] && [ -d <deployer_repo>/roles/gateway ] \
  && echo "deployer_repo OK" || echo "deployer_repo MISSING/WRONG"

[ -f <tls_repo>/playbooks/gen_ca_cert.yml ] && [ -f <tls_repo>/playbooks/gen_certs.yml ] \
  && echo "tls_repo OK" || echo "tls_repo MISSING/WRONG"
```

---

## 7. Python Dependencies (scripts/)

```bash
cd <themis-root>
pip install -r scripts/requirements.txt
```

Packages: `pymongo~=4.15.1`, `redis~=6.4.0`, `requests~=2.32.5`, `urllib3~=2.5.0`

Only required if running `generate_inventory.py` or `validate.py` directly.

---

## 8. Platform Release (group_vars)

Before deploying, confirm `platform_release` is set in the design's group_vars:

```bash
cat <themis-root>/inventories/<design>/group_vars/platform.yml | grep platform_release
```

**If missing:** Set the appropriate release version in that file before running deploy.
