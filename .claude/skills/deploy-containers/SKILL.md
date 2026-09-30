---
name: deploy-containers
description: Provision a containerized Itential Platform environment for reproduction or testing. Supports Docker local (macOS/Windows), Docker on a VM (SSH), and Kubernetes via Helm charts. Handles ECR authentication, dev stack setup, secret creation, and health verification. Integrates with /troubleshoot Phase 3 reproduction workflow.
argument-hint: "[docker-local | docker-vm | k8s | --check-prereqs]"
---

# /deploy-containers — Containerized Environment Provisioning

Provisions an Itential Platform reproduction environment using Docker Compose (local or VM) or Kubernetes (Helm charts). Use this skill when a support engineer needs an isolated environment that matches a customer's platform version — faster and cheaper than full VM provisioning via Themis.

---

## CRITICAL SAFETY RULES

- **Never create a billable/compute resource without a final confirmation showing method + CPU/memory** — before the first EC2 instance, EKS cluster, DocumentDB cluster, ElastiCache cluster, or Docker container is created, show the engineer which method is being used and the exact CPU/RAM each component will get (see Step 3d for Docker paths, Step 5c.5 for the consolidated K8s confirmation, and the per-component confirmations in Step 2.k8s.1/2.k8s.2/2.k8s.3). Individual infrastructure-creation confirmations do not substitute for this — each one must independently state CPU/RAM, not just "yes to proceed"
- **Never run `make clean` without explicit engineer approval** — it destroys MongoDB data volumes and is irreversible
- **Never run `kubectl delete namespace` without explicit approval** — removes all secrets, PVCs, and running workloads
- **Never store ECR credentials in any tracked file** — use CRED_MODE pattern (profile or env vars); credentials stay in session variables only
- **Dry-run all Helm installs first** — show rendered output to engineer; require explicit "yes" before applying
- **Always show K8s secret manifests before applying** — use `--dry-run=client -o yaml | kubectl apply -f -` so the engineer sees what will be created
- **Never hardcode credentials** — read from `.env` or prompt engineer; use `<PLACEHOLDER>` for fields to fill in IAP UI
- **Confirm cleanup scope** — distinguish `make down` (safe, preserves volumes) from `make clean` (destructive); always confirm before the destructive variant

---

## Step 0 — Select Deployment Type

**If invoked standalone (not via `/troubleshoot` Phase 3, so no confirmed root cause is available yet), ask these questions before presenting the menu** — when invoked from `/troubleshoot`, the orchestrator's Phase 3 decision guidance already supplies a recommendation, so skip straight to the menu with that recommendation stated first:

- Is the issue Kubernetes/Helm-specific (pod scheduling, StorageClass, ingress, chart values schema), or does the customer's deployment orchestration fidelity matter? → **Kubernetes**
- Does reproduction need OS-level fidelity — systemd/init scripts, RPM install/upgrade paths, MongoDB replica-set or Redis Sentinel HA behavior, multi-host network topology, or matching a specific architecture (aio/minimal/ha2/asa)? → **VMs on AWS (Themis)**
- Otherwise, is network isolation from this machine needed (something must be reachable from elsewhere, or this machine can't run Docker)? → **Docker on VM**
- Otherwise → **Docker local** (default — generic application-level bugs: workflow/adapter/JST/API/UI logic)

Present the menu:

```
╔══════════════════════════════════════════════════════════════╗
║  /deploy-containers — Select Deployment Type                ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  1) Docker local    — this machine (macOS/Windows/Linux)     ║
║                       Fastest; dev/test only; ports on       ║
║                       localhost only                         ║
║                                                              ║
║  2) Docker on VM    — SSH to an existing Linux VM            ║
║                       Isolated from your machine;            ║
║                       Rocky Linux 9 / Ubuntu 22.04           ║
║                                                              ║
║  3) Kubernetes      — Helm charts on an existing cluster     ║
║                       Closest to production containers;      ║
║                       Requires kubectl + Helm 3.15+          ║
║                                                              ║
║  4) VMs on AWS      — Full VM topology via Themis            ║
║                       Use /themis-aws-deploy instead         ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝

Choice [1-4]:
```

Set `DEPLOY_TYPE`:
- `1` → `docker-local`
- `2` → `docker-vm`
- `3` → `k8s`
- `4` → stop here, redirect to `/themis-aws-deploy`

If invoked with an argument (`docker-local`, `docker-vm`, `k8s`), skip the menu and set `DEPLOY_TYPE` directly.

---

## Step 1 — AWS Credentials for ECR

All paths (Docker and K8s) need AWS credentials to pull from ECR (`497639811223.dkr.ecr.us-east-2.amazonaws.com`).

**Step 1a — Discover available credential sources:**

```python
import configparser, os, glob

# 1. Named profiles from ~/.aws/config and ~/.aws/credentials
aws_config = os.path.expanduser("~/.aws/config")
aws_creds  = os.path.expanduser("~/.aws/credentials")
profiles = {}

for path in [aws_config, aws_creds]:
    if os.path.exists(path):
        cp = configparser.ConfigParser()
        cp.read(path)
        for section in cp.sections():
            name = section.replace("profile ", "").strip()
            p = dict(cp[section])
            if "sso_start_url" in p:
                kind = "SSO"
            elif "aws_access_key_id" in p:
                kind = "static-key"
            else:
                kind = "other"
            profiles[name] = {"kind": kind, "source": path}

# 2. .env files in repo with AWS creds
env_files = {}
for ef in glob.glob(".env*"):
    if os.path.isfile(ef):
        content = open(ef).read()
        if "AWS_ACCESS_KEY_ID" in content:
            env_files[ef] = ef

print("=== AWS profiles ===")
for i, (name, meta) in enumerate(profiles.items(), 1):
    print(f"  [{i}] {name}  ({meta['kind']})")
print(f"\n=== .env files with AWS creds ===")
for i, ef in enumerate(env_files.keys(), len(profiles)+1):
    print(f"  [{i}] {ef}")
```

**Step 1b — Present numbered menu and collect selection:**

```
Available credential sources:

  Profiles from ~/.aws:
    [1] pe-team-sbx  (SSO)
    [2] mohan-env-sts  (static-key)

  .env files with AWS credentials:
    [3] .env  (AWS_ACCESS_KEY_ID found)

Select credential source [number]:
```

**Step 1c — Process selection:**

- **Profile (SSO):** set `CRED_MODE=profile`, `SESSION_AWS_PROFILE=<name>`. If SSO, run `aws sso login --profile ${SESSION_AWS_PROFILE}` if session is expired.
- **Profile (static-key):** set `CRED_MODE=profile`, `SESSION_AWS_PROFILE=<name>`.
- **.env file:** grep `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` from file; export them; set `CRED_MODE=static-env-vars`.

**Step 1d — Verify identity and ECR login:**

```bash
# Verify identity
if [ "${CRED_MODE}" = "static-env-vars" ]; then
    aws sts get-caller-identity
else
    aws sts get-caller-identity --profile "${SESSION_AWS_PROFILE}"
fi

# ECR login
ECR_REGISTRY=497639811223.dkr.ecr.us-east-2.amazonaws.com

if [ "${CRED_MODE}" = "static-env-vars" ]; then
    aws ecr get-login-password --region us-east-2 \
      | docker login --username AWS --password-stdin "${ECR_REGISTRY}"
else
    aws ecr get-login-password --region us-east-2 --profile "${SESSION_AWS_PROFILE}" \
      | docker login --username AWS --password-stdin "${ECR_REGISTRY}"
fi
```

Print: `✅ ECR login successful — {ECR_REGISTRY}`

---

## Step 1e — Check for Reusable MongoDB / Redis Dependencies

**Purpose:** avoid silently pointing a new deployment at a MongoDB/Redis instance that isn't network-reachable from it, and avoid spinning up (and paying for) duplicate infrastructure when a compatible instance already exists.

This step runs for every `DEPLOY_TYPE` — the previous environment might be any of the three, and the new one might be any of the three.

### Step 1e.1 — Detect existing dependencies

Scan for evidence of a MongoDB/Redis instance already in use by a prior `/deploy-containers` (or Themis) session:

```bash
echo "=== Scanning for existing reproduction dependencies ==="

# 1. Prior repro .env files (any ticket)
find repro -maxdepth 2 -name ".env" 2>/dev/null | while read -r f; do
    grep -qE '^(MONGO_URL|REDIS_HOST)=' "$f" && echo "Found: $f"
done

# 2. environments/*.env and project-root .env*
for f in environments/*.env .env .env.*; do
    [ -f "$f" ] && grep -qE '^(MONGO_URL|REDIS_HOST)=' "$f" 2>/dev/null && echo "Found: $f"
done

# 3. A currently running Docker Compose dev-stack (local or previously deployed to a VM you're re-checking)
DEVSTACK_DIR="${DEVSTACK_DIR:-$HOME/itential-dev-stack}"
if [ -d "$DEVSTACK_DIR" ]; then
    (cd "$DEVSTACK_DIR" && docker compose ps --format json 2>/dev/null) | \
        python3 -c "
import sys, json
for line in sys.stdin:
    try:
        s = json.loads(line)
        if 'mongo' in s.get('Service','').lower() or 'redis' in s.get('Service','').lower():
            print(f\"Running: {s['Service']} — {s.get('Status','?')} — ports: {s.get('Publishers','?')}\")
    except Exception:
        pass
"
fi

# 4. An existing K8s secret (if a cluster context is already configured)
if kubectl cluster-info > /dev/null 2>&1; then
    for ns in $(kubectl get namespaces -o name 2>/dev/null | sed 's|namespace/||'); do
        kubectl get secret itential-platform-secrets -n "$ns" > /dev/null 2>&1 && \
            echo "Found: K8s secret itential-platform-secrets in namespace $ns"
    done
fi
```

### Step 1e.2 — Reachability judgment

If nothing is found, skip straight to Step 2. If one or more sources are found, classify each against the **new** `DEPLOY_TYPE` being requested using this matrix:

| Existing source | New DEPLOY_TYPE | Reachable? | Default action |
|---|---|---|---|
| Docker local (bound to `127.0.0.1`) | Kubernetes (EKS) | ❌ No — container loopback network, not routable from EKS pod network | Provision new — do not offer reuse |
| Docker local (bound to `127.0.0.1`) | Docker on VM | ❌ No — different host entirely | Provision new — do not offer reuse |
| Docker on VM (bound to VM's IP) | Kubernetes (EKS) | ⚠️ Maybe — only if the VM sits in a network reachable from the EKS VPC/nodes and its SG allows the Mongo/Redis ports from the cluster's node/pod CIDR | Ask engineer to confirm the network path; default to provisioning new unless confirmed |
| Docker on VM (bound to VM's IP) | Docker local | ⚠️ Maybe — only if the VM is reachable from this machine and its SG allows the ports from your IP | Ask engineer to confirm |
| Kubernetes external (Atlas / DocumentDB / ElastiCache) | Docker local or Docker on VM | ✅ Usually yes — these are standalone network endpoints, not container-local | Offer reuse; still confirm SG/firewall allows the new host's IP |
| Same `DEPLOY_TYPE`, same ticket, already configured this session | Same `DEPLOY_TYPE` | ✅ Yes | Reuse by default — skip re-provisioning silently, just confirm briefly |

### Step 1e.3 — Prompt the engineer

For any ⚠️ or ✅ match, show the specifics and require an explicit choice — never assume reuse or assume re-provisioning silently when a prior instance exists.

```
⚠️  Existing dependency detected

A prior reproduction session ({SOURCE_DEPLOY_TYPE}, started {date/ticket if known}) is using:
  MongoDB: {detected MONGO_URL, host:port only — no credentials shown}
  Redis:   {detected REDIS_HOST}:{REDIS_PORT}

You're now setting up: {NEW_DEPLOY_TYPE}

{reachability warning from the matrix above, filled in for this specific pairing}

Options:
  1) Provision new MongoDB/Redis for this environment (recommended when reachability is ❌ or unconfirmed)
  2) Reuse the existing instance — I've confirmed network reachability myself
  3) Let me answer MongoDB/Redis configuration manually

Choice [1/2/3]:
```

**On choice 1:** proceed to the normal per-`DEPLOY_TYPE` provisioning path (Step 2.k8s.2/2.k8s.3 for K8s; dev-stack's own bundled containers for Docker).

**On choice 2:** before trusting it, run a lightweight reachability probe rather than taking the engineer's word alone:
```bash
# From the new deployment's vantage point (e.g. inside a temporary debug pod for K8s,
# or the target VM via SSH for docker-vm, or directly for docker-local)
timeout 5 bash -c "cat < /dev/null > /dev/tcp/${MONGO_HOST}/${MONGO_PORT:-27017}" \
    && echo "✅ MongoDB port reachable" || echo "❌ MongoDB port NOT reachable — reconsider"
timeout 5 bash -c "cat < /dev/null > /dev/tcp/${REDIS_HOST}/${REDIS_PORT:-6379}" \
    && echo "✅ Redis port reachable" || echo "❌ Redis port NOT reachable — reconsider"
```
For K8s specifically, run this probe from inside a throwaway pod on the target cluster (`kubectl run repro-netcheck --rm -it --image=busybox --restart=Never -- sh`), since reachability from your local machine does not prove reachability from the cluster's pod network. If the probe fails, warn clearly and re-offer the choice — do not silently fall through to provisioning new without telling the engineer why their reuse attempt failed.

On confirmed reachability: set `MONGO_URL` / `REDIS_HOST` / `REDIS_PORT` / passwords from the detected values and skip the corresponding provisioning steps (2.k8s.2/2.k8s.3 for K8s).

**On choice 3:** proceed to the normal manual configuration steps, ignoring the detected instance.

**Special case — Docker local/VM reusing a K8s-external database:** the dev-stack's Docker Compose file bundles its own MongoDB/Redis containers by default. Before offering reuse of an external Atlas/DocumentDB/ElastiCache instance here, check `${DEVSTACK_DIR}/.env.example` for `MONGO_URL` / `REDIS_HOST` override variables — if the dev-stack compose file doesn't support pointing at an external database, reuse isn't possible regardless of network reachability, and the skill should say so rather than attempting an unsupported override.

---

## Step 2 — Prerequisite Check

Run the checks appropriate to `DEPLOY_TYPE` and print a preflight table.

### Docker local (macOS/Windows/Linux)

```bash
echo "=== Docker local preflight ==="

# Docker running
docker info > /dev/null 2>&1 && echo "✅ Docker running" || echo "❌ Docker not running — start Docker Desktop"

# docker compose v2
docker compose version > /dev/null 2>&1 && echo "✅ docker compose v2 available" \
  || echo "❌ docker compose plugin not found — upgrade Docker Desktop to 4.x+"

# AWS CLI
aws --version > /dev/null 2>&1 && echo "✅ AWS CLI available" \
  || echo "❌ AWS CLI not found — brew install awscli"

# git
git --version > /dev/null 2>&1 && echo "✅ git available" \
  || echo "❌ git not found — brew install git"

# Disk space (20 GB free required)
python3 -c "
import shutil
free = shutil.disk_usage('/').free // (1024**3)
if free >= 20:
    print(f'✅ Disk space: {free} GB free')
else:
    print(f'⚠️  Disk space: {free} GB free — 20 GB recommended')
"
```

**Windows note:** WSL2 backend must be enabled in Docker Desktop → Settings → General → "Use WSL 2 based engine".

### Docker on VM

**Step 2.vm.0 — VM source selection**

Ask the engineer:

```
Do you have an existing Linux VM to SSH into, or should I provision a bare EC2 instance?

  1) Existing VM  — read SSH_HOST_1 / SSH_USER_1 / SSH_KEY_PATH_1 from .env
  2) New EC2      — provision a bare Rocky Linux 9 instance on AWS
                    (uses the AWS profile selected in Step 1)

Choice [1/2]:
```

---

**If choice 2 — Provision bare EC2:**

**Step 2.vm.1 — Collect EC2 parameters**

Read from `.env` where already set; prompt for anything missing:

```bash
# Key pair (for SSH access to the new instance)
KEY_NAME="${AWS_KEY_NAME:-}"
if [ -z "${KEY_NAME}" ]; then
    echo "EC2 key pair name (must exist in AWS account): "
    # read KEY_NAME
fi

# Security group — must allow SSH (22) inbound
SG_IDS="${AWS_SECURITY_GROUP_IDS:-}"
if [ -z "${SG_IDS}" ]; then
    echo "Security group ID(s) that allow SSH inbound (e.g. sg-0abc1234): "
    # read SG_IDS
fi

# Subnet
SUBNET_ID="${AWS_SUBNET_IDS:-}"
if [ -z "${SUBNET_ID}" ]; then
    echo "Subnet ID for the instance (e.g. subnet-0abc1234): "
    # read SUBNET_ID — use first value if comma-separated
    SUBNET_ID="${SUBNET_ID%%,*}"
fi
```

Present instance type menu:

```
Select instance type for the Docker VM:

  1) t3.large     — 2 vCPU / 8 GB RAM  (minimum for platform + gateway)
  2) t3.xlarge    — 4 vCPU / 16 GB RAM  (recommended — comfortable headroom)
  3) m5.xlarge    — 4 vCPU / 16 GB RAM  (better network; use if t3 unavailable)
  4) Custom       — enter your own type

Choice [1-4]:
```

**Step 2.vm.2 — Find Rocky Linux 9 AMI**

Look up the latest Rocky Linux 9 AMI in the active region:

```bash
REGION="${AWS_REGION:-us-east-1}"

CRED_FLAGS=""
[ "${CRED_MODE}" = "profile" ] && CRED_FLAGS="--profile ${SESSION_AWS_PROFILE}"

ROCKY9_AMI=$(aws ec2 describe-images \
    ${CRED_FLAGS} \
    --region "${REGION}" \
    --owners 679593333241 \
    --filters \
        "Name=name,Values=Rocky-9-EC2-Base-9.*-x86_64*" \
        "Name=state,Values=available" \
        "Name=architecture,Values=x86_64" \
    --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
    --output text 2>/dev/null)

if [ -z "${ROCKY9_AMI}" ] || [ "${ROCKY9_AMI}" = "None" ]; then
    echo "Could not auto-detect Rocky Linux 9 AMI in ${REGION}."
    echo "Enter AMI ID manually (find at console.aws.amazon.com → EC2 → AMIs, owner 679593333241): "
    # read ROCKY9_AMI
else
    echo "✅ Rocky Linux 9 AMI: ${ROCKY9_AMI} (${REGION})"
fi
```

**Step 2.vm.3 — Show and confirm the run-instances command**

Show the full command before running:

```
══════════════════════════════════════════════════════════════
  FINAL CONFIRMATION — Docker on VM (new EC2 instance)
══════════════════════════════════════════════════════════════
  Method:           Docker Compose on a newly provisioned EC2 instance
  Region:           {REGION}
  AMI:              {ROCKY9_AMI}  (Rocky Linux 9, x86_64)
  Instance type:    {INSTANCE_TYPE}  ({vCPU from the Step 2.vm.1 menu} vCPU / {RAM} GB RAM)
  Key pair:         {KEY_NAME}
  Security groups:  {SG_IDS}
  Subnet:           {SUBNET_ID}
  Root volume:      60 GiB gp3
  Name tag:         itential-docker-repro

  Platform, MongoDB, Redis (and Gateway if enabled) will share this
  single VM's {vCPU} vCPU / {RAM} GB — Docker Compose does not set
  explicit per-container CPU/memory limits by default, so all
  containers draw from the same pool. See Step 4-pre for the option
  to constrain individual containers before starting the stack.

  aws ec2 run-instances \
    --image-id {ROCKY9_AMI} \
    --instance-type {INSTANCE_TYPE} \
    --key-name {KEY_NAME} \
    --security-group-ids {SG_IDS} \
    --subnet-id {SUBNET_ID} \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":60,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=itential-docker-repro}]' \
    --associate-public-ip-address \
    --output json

Provision this instance? [yes / abort]:
══════════════════════════════════════════════════════════════
```

On approval:

```bash
INSTANCE_JSON=$(aws ec2 run-instances \
    ${CRED_FLAGS} \
    --region "${REGION}" \
    --image-id "${ROCKY9_AMI}" \
    --instance-type "${INSTANCE_TYPE}" \
    --key-name "${KEY_NAME}" \
    --security-group-ids ${SG_IDS} \
    --subnet-id "${SUBNET_ID}" \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":60,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=itential-docker-repro}]' \
    --associate-public-ip-address \
    --output json)

INSTANCE_ID=$(echo "${INSTANCE_JSON}" | python3 -c "import sys,json; print(json.load(sys.stdin)['Instances'][0]['InstanceId'])")
echo "✅ Instance launched: ${INSTANCE_ID}"
echo "Waiting for instance to reach running state..."
```

**Step 2.vm.4 — Wait for running and get public IP**

```bash
aws ec2 wait instance-running \
    ${CRED_FLAGS} \
    --region "${REGION}" \
    --instance-ids "${INSTANCE_ID}"

VM_PUBLIC_IP=$(aws ec2 describe-instances \
    ${CRED_FLAGS} \
    --region "${REGION}" \
    --instance-ids "${INSTANCE_ID}" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text)

echo "✅ Instance running — Public IP: ${VM_PUBLIC_IP}"

# Set SSH vars for subsequent steps
SSH_HOST="${VM_PUBLIC_IP}"
SSH_USER="rocky"        # default user for Rocky Linux 9 on EC2
SSH_KEY="${KEY_NAME}"   # path to the local .pem file

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  EC2 instance ready                                      ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Instance ID:  ${INSTANCE_ID}                           ║"
echo "║  Public IP:    ${VM_PUBLIC_IP}                           ║"
echo "║  SSH user:     rocky                                     ║"
echo "║  Key pair:     ${KEY_NAME}                               ║"
echo "╚══════════════════════════════════════════════════════════╝"
```

Ask: "What is the local path to the `.pem` file for key pair `{KEY_NAME}`?"

```bash
# read KEY_PATH from engineer (e.g. ~/.ssh/my-key.pem)
SSH_KEY="${KEY_PATH}"

# Brief pause for SSH daemon to start
echo "Waiting 30s for SSH daemon to initialize..."
sleep 30
```

Offer to persist to `.env`:
```
Persist these SSH vars to .env so future steps can read them automatically?
  SSH_HOST_1={VM_PUBLIC_IP}
  SSH_USER_1=rocky
  SSH_KEY_PATH_1={KEY_PATH}
[yes / no]:
```

If yes, append to `.env`.

---

**If choice 1 — Existing VM (or after new EC2 is provisioned above):**

```bash
# Read SSH vars — either set above (new EC2) or from .env (existing VM)
SSH_HOST="${SSH_HOST:-$(grep "^SSH_HOST_1=" .env 2>/dev/null | cut -d= -f2)}"
SSH_USER="${SSH_USER:-$(grep "^SSH_USER_1=" .env 2>/dev/null | cut -d= -f2)}"
SSH_KEY="${SSH_KEY:-$(grep "^SSH_KEY_PATH_1=" .env 2>/dev/null | cut -d= -f2)}"

if [ -z "${SSH_HOST}" ] || [ -z "${SSH_USER}" ] || [ -z "${SSH_KEY}" ]; then
    echo "❌ SSH vars not set. Add SSH_HOST_1, SSH_USER_1, SSH_KEY_PATH_1 to .env and retry."
    exit 1
fi

echo "=== Docker VM preflight (${SSH_HOST}) ==="

# SSH connectivity
ssh -i "${SSH_KEY}" -o ConnectTimeout=10 "${SSH_USER}@${SSH_HOST}" "echo '✅ SSH connection OK'" \
  || echo "❌ SSH failed — check host, user, and key path"

# Docker + compose on VM
ssh -i "${SSH_KEY}" "${SSH_USER}@${SSH_HOST}" "
  docker info > /dev/null 2>&1 && echo '✅ Docker running' || echo '❌ Docker not running on VM'
  docker compose version > /dev/null 2>&1 && echo '✅ docker compose v2 available' \
    || echo '❌ docker compose plugin missing — sudo dnf install docker-compose-plugin'
  aws --version > /dev/null 2>&1 && echo '✅ AWS CLI available' \
    || echo '⚠️  AWS CLI not found on VM — will transfer ECR token from local machine'
  df -BG / | tail -1 | awk '{print \"Disk free: \" \$4}'
"
```

**Note for freshly provisioned EC2:** Docker Engine is not pre-installed on Rocky Linux 9. The preflight will show `❌ Docker not running`. Step 3 will install Docker automatically before transferring the dev stack.

**VM resource minimum:** 4 vCPU / 16 GB RAM / 50 GB disk (Step 2.vm.3 provisions 60 GiB by default).

### Kubernetes

The Kubernetes path supports two scenarios:
- **Existing cluster** (EKS or AKS) — skip provisioning, go straight to prerequisites check
- **New EKS cluster** — provision it from scratch with the right node sizing per grade

Work through Steps 2.k8s.0 through 2.k8s.4 before proceeding to Step 5.

---

#### Step 2.k8s.0 — Cluster Grade Selection

Present the sizing options (from docs.itential.com):

```
╔══════════════════════════════════════════════════════════════╗
║  Kubernetes Cluster Grade                                    ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  1) Minimum  (dev/test/reproduction)                         ║
║     4 vCPU / 16 GB RAM per node                             ║
║     AWS: m5a.xlarge  |  Azure: Standard_D4as_v5              ║
║     Pod distribution: 1–2 nodes, 1 AZ acceptable            ║
║                                                              ║
║  2) Production                                               ║
║     16 vCPU / 32 GB RAM per node                            ║
║     AWS: c6a.4xlarge  |  Azure: Standard_F16as_v5            ║
║     Pod distribution: 1 Platform pod per node, 2–3 AZs      ║
║     StatefulSet containers on dedicated nodes               ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝

Note: Development environments often have similar resource needs
to production. Avoid undersizing — engineers experiment with real
automations in development.

Grade [1/2]:
```

Set:
```bash
K8S_CLUSTER_GRADE="${K8S_CLUSTER_GRADE:-}"    # read from .env if set
if [ "${GRADE}" = "1" ]; then
    K8S_CLUSTER_GRADE=minimum
    EKS_NODE_TYPE="${EKS_NODE_TYPE:-m5a.xlarge}"
    EKS_NODE_COUNT="${EKS_NODE_COUNT:-2}"
else
    K8S_CLUSTER_GRADE=production
    EKS_NODE_TYPE="${EKS_NODE_TYPE:-c6a.4xlarge}"
    EKS_NODE_COUNT="${EKS_NODE_COUNT:-3}"
fi
```

---

#### Step 2.k8s.1 — Cluster Availability or EKS Provisioning

Check if an existing cluster is configured:

```bash
KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
K8S_CONTEXT="${K8S_CONTEXT:-}"    # from .env

if [ -n "${K8S_CONTEXT}" ]; then
    kubectl config use-context "${K8S_CONTEXT}" 2>/dev/null
fi

kubectl cluster-info 2>/dev/null && CLUSTER_EXISTS=true || CLUSTER_EXISTS=false
```

**If `CLUSTER_EXISTS=true`:** Print cluster info and proceed to Step 2.k8s.2. This also covers a pre-existing local `kind` cluster (`kubectl config current-context` showing `kind-<name>`) — the rest of this skill (Helm installs, secrets, probes) applies identically to `kind` as to EKS; only image-pull mechanics differ (see the arm64 gotcha below).

**If `CLUSTER_EXISTS=false`:** Offer EKS provisioning:

```
No cluster is reachable (check KUBECONFIG / VPN, or K8S_CONTEXT in .env).

Options:
  1) Provision a new AWS EKS cluster  (uses the AWS profile from Step 1)
  2) Connect to an existing cluster   (I'll wait while you run aws eks update-kubeconfig,
                                        or switch context to an existing local `kind` cluster)

Choice [1/2]:
```

**Gotcha — Apple Silicon (arm64) `kind` nodes cannot pull amd64-only images the normal way.** Unlike `docker run`, which transparently emulates amd64 via QEMU, containerd inside a `kind` node performs strict CRI platform matching and will fail (or silently pick the wrong manifest) when pulling an amd64-only image like the IAG5 image (`automation-gateway5:5.5.2-amd64`) on an arm64 node. Fix: pull the image on the host first (where Docker Desktop's emulation works), then side-load it directly into the `kind` node's containerd, bypassing the platform-matched registry pull:
```bash
docker pull --platform=linux/amd64 497639811223.dkr.ecr.us-east-2.amazonaws.com/automation-gateway5:5.5.2-amd64
kind load docker-image 497639811223.dkr.ecr.us-east-2.amazonaws.com/automation-gateway5:5.5.2-amd64 --name <kind-cluster-name>
```
Apply this whenever `kubectl describe pod` shows `ErrImagePull`/`ImagePullBackOff` on an amd64-tagged image on a local `kind` cluster on Apple Silicon.

**If choice 2:** Pause and show:
```bash
# Run this in another terminal to configure kubectl:
aws eks update-kubeconfig \
    --region "${EKS_CLUSTER_REGION:-us-east-2}" \
    --name "${EKS_CLUSTER_NAME}" \
    ${CRED_FLAGS}

# Then press Enter here to re-check cluster connectivity.
```

---

**If choice 1 — Provision EKS cluster:**

Collect parameters from `.env` or prompt:

```bash
EKS_CLUSTER_NAME="${EKS_CLUSTER_NAME:-itential-repro}"
EKS_CLUSTER_REGION="${EKS_CLUSTER_REGION:-us-east-2}"
EKS_K8S_VERSION="${EKS_K8S_VERSION:-1.31}"
```

Ask if not set:
- "EKS cluster name [default: itential-repro]:"
- "AWS region [default: us-east-2]:"
- "Kubernetes version [default: 1.31]:"

Check if `eksctl` is available:
```bash
eksctl version > /dev/null 2>&1 && EKS_TOOL=eksctl || EKS_TOOL=awscli
```

Write the generated ClusterConfig to `environments/eks/{EKS_CLUSTER_NAME}.yaml` (repo-relative, not `/tmp`) so it survives past this session for later inspection or re-use — this path is gitignored (`environments/eks/`) since cluster naming/region is per-engineer, not a shared template:

```bash
mkdir -p environments/eks
EKS_CONFIG_PATH="environments/eks/${EKS_CLUSTER_NAME}.yaml"
```

**Using eksctl (preferred):** Show config and confirm before running:

```yaml
# eksctl cluster config — review before applying
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig

metadata:
  name: {EKS_CLUSTER_NAME}
  region: {EKS_CLUSTER_REGION}
  version: "{EKS_K8S_VERSION}"

managedNodeGroups:
  - name: itential-nodes
    instanceType: {EKS_NODE_TYPE}
    minSize: 1
    maxSize: {EKS_NODE_COUNT + 1}
    desiredCapacity: {EKS_NODE_COUNT}
    volumeSize: 100
    # One pod per node in separate AZs (production grade)
    availabilityZones:
      - {EKS_CLUSTER_REGION}a
      - {EKS_CLUSTER_REGION}b
      - {EKS_CLUSTER_REGION}c     # omit for minimum grade (2 AZs)
    tags:
      Name: itential-repro-node
      managed-by: deploy-containers-skill

addons:
  - name: aws-ebs-csi-driver    # required for StorageClass
  - name: vpc-cni
  - name: coredns
  - name: kube-proxy
```

Compute the per-node and total cluster specs from a static instance-type lookup table (no AWS API call needed — these are published EC2 specs):

```python
EC2_SPECS = {
    "t3.large":    (2, 8),    "t3.xlarge":   (4, 16),
    "m5.xlarge":   (4, 16),   "m5a.xlarge":  (4, 16),
    "c6a.4xlarge": (16, 32),
}
vcpu, ram_gb = EC2_SPECS.get(EKS_NODE_TYPE, ("?", "?"))
total_vcpu = vcpu * EKS_NODE_COUNT if vcpu != "?" else "?"
total_ram = ram_gb * EKS_NODE_COUNT if ram_gb != "?" else "?"
```

```
══════════════════════════════════════════════════════════════
  FINAL CONFIRMATION — Kubernetes (EKS) Provisioning
══════════════════════════════════════════════════════════════
  Method:        Provision new EKS cluster (eksctl)
  Cluster:       {EKS_CLUSTER_NAME} in {EKS_CLUSTER_REGION}
  Kubernetes:    {EKS_K8S_VERSION}
  Grade:         {K8S_CLUSTER_GRADE}

  Node type:     {EKS_NODE_TYPE}  ({vcpu} vCPU / {ram_gb} GB RAM per node)
  Node count:    {EKS_NODE_COUNT}
  Cluster total: {total_vcpu} vCPU / {total_ram} GB RAM

  Add-ons:       aws-ebs-csi-driver, vpc-cni, coredns, kube-proxy

  MongoDB / Redis / Platform / Gateway compute is NOT yet included in
  this total — each is confirmed separately as it's provisioned
  (Steps 2.k8s.2, 2.k8s.3, 5d) and a final combined summary is shown
  again before the first Helm install (Step 5d).

This will take 15–20 minutes and incur AWS costs for as long as the
cluster and its nodes exist.
Type 'yes create cluster' to confirm:
══════════════════════════════════════════════════════════════
```

On confirmation:
```bash
eksctl create cluster -f "${EKS_CONFIG_PATH}" \
    ${CRED_FLAGS}

# Configure kubectl
aws eks update-kubeconfig \
    --region "${EKS_CLUSTER_REGION}" \
    --name "${EKS_CLUSTER_NAME}" \
    ${CRED_FLAGS}

echo "✅ EKS cluster ready — kubectl configured"
kubectl get nodes
```

After the cluster is ready, extract VPC context for downstream provisioning (ElastiCache, security groups):

```bash
EKS_VPC_ID=$(aws eks describe-cluster \
    --name "${EKS_CLUSTER_NAME}" \
    --region "${EKS_CLUSTER_REGION}" \
    ${CRED_FLAGS} \
    --query 'cluster.resourcesVpcConfig.vpcId' \
    --output text)

EKS_SUBNET_IDS=$(aws eks describe-cluster \
    --name "${EKS_CLUSTER_NAME}" \
    --region "${EKS_CLUSTER_REGION}" \
    ${CRED_FLAGS} \
    --query 'cluster.resourcesVpcConfig.subnetIds' \
    --output json | python3 -c "import sys,json; print(' '.join(json.load(sys.stdin)))")

EKS_NODE_SG=$(aws eks describe-cluster \
    --name "${EKS_CLUSTER_NAME}" \
    --region "${EKS_CLUSTER_REGION}" \
    ${CRED_FLAGS} \
    --query 'cluster.resourcesVpcConfig.clusterSecurityGroupId' \
    --output text)

echo "✅ VPC: ${EKS_VPC_ID}"
echo "   Subnets: ${EKS_SUBNET_IDS}"
echo "   Node SG: ${EKS_NODE_SG}"
```

Save `EKS_VPC_ID`, `EKS_SUBNET_IDS`, and `EKS_NODE_SG` as session variables — Steps 2.k8s.1b and 2.k8s.3 reference them.

**Using AWS CLI (if eksctl not available):** Show the equivalent `aws eks create-cluster` command (verbose, confirm before running). After cluster creation, create a managed node group with `aws eks create-nodegroup` and install the EBS CSI driver add-on.

---

#### Step 2.k8s.1b — IAM Role for AWS Load Balancer Controller (IRSA)

*Run this step when a new EKS cluster was just provisioned AND `EKS_LBC_ROLE_ARN` is not set in `.env`.*

```
AWS Load Balancer Controller needs an IAM role (IRSA) to call the ELB API.
  EKS_LBC_ROLE_ARN not found in .env.

  1) Create it now  (downloads AWS LBC IAM policy, creates IRSA role via eksctl, installs LBC)
  2) Skip           (port-forward access only — no ALB ingress; set EKS_LBC_ROLE_ARN later)

  Choice [1/2]:
```

**On choice 1 — show each command and confirm before running:**

```bash
# Phase A — Download the IAM policy
curl -o /tmp/aws-lbc-iam-policy.json \
    https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/main/docs/install/iam_policy.json
```

```bash
# Phase B — Create the IAM policy (idempotent: catches AlreadyExists)
POLICY_ARN=$(aws iam create-policy \
    --policy-name AWSLoadBalancerControllerIAMPolicy \
    --policy-document file:///tmp/aws-lbc-iam-policy.json \
    ${CRED_FLAGS} \
    --query 'Policy.Arn' --output text 2>/dev/null) \
  || POLICY_ARN=$(aws iam list-policies \
    --query "Policies[?PolicyName=='AWSLoadBalancerControllerIAMPolicy'].Arn | [0]" \
    --output text ${CRED_FLAGS})
echo "Policy ARN: ${POLICY_ARN}"
```

```bash
# Phase C — Create IRSA service account (attaches policy, generates IAM role with OIDC trust)
eksctl create iamserviceaccount \
    --cluster "${EKS_CLUSTER_NAME}" \
    --region "${EKS_CLUSTER_REGION}" \
    --namespace kube-system \
    --name aws-load-balancer-controller \
    --attach-policy-arn "${POLICY_ARN}" \
    --override-existing-serviceaccounts \
    --approve \
    ${CRED_FLAGS}
```

```bash
# Phase D — Extract role ARN and save to session + offer to persist to .env
EKS_LBC_ROLE_ARN=$(eksctl get iamserviceaccount \
    --cluster "${EKS_CLUSTER_NAME}" --region "${EKS_CLUSTER_REGION}" \
    --name aws-load-balancer-controller --namespace kube-system \
    ${CRED_FLAGS} -o json 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['status']['roleARN'])")
echo "✅ EKS_LBC_ROLE_ARN=${EKS_LBC_ROLE_ARN}"
```

Offer to append `EKS_LBC_ROLE_ARN` to `.env` — credentials are never written to tracked files.

```bash
# Phase E — Install AWS Load Balancer Controller via Helm
helm repo add eks https://aws.github.io/eks-charts 2>/dev/null || true
helm repo update eks
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
    -n kube-system \
    --set clusterName="${EKS_CLUSTER_NAME}" \
    --set serviceAccount.create=false \
    --set serviceAccount.name=aws-load-balancer-controller \
    --set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"="${EKS_LBC_ROLE_ARN}"
echo "✅ AWS Load Balancer Controller installed"
```

**On choice 2:** Note that `kubectl port-forward svc/iap 3443:3443 -n itential` remains available for
port-forward access to IAP without an ingress controller.

---

#### Step 2.k8s.2 — MongoDB Configuration

*Skip this step if Step 1e already resolved MongoDB via confirmed reuse of an existing instance.*

The Itential Helm charts do NOT include MongoDB — an external instance is required.

```
External MongoDB is required. Select the source:

  1) MongoDB Atlas (SaaS)    — paste connection string (mongodb+srv://...)
  2) AWS DocumentDB          — provide cluster endpoint
  3) Existing on-prem / VM  — provide host:port URL
  4) Provision AWS DocumentDB — I'll create a new cluster (uses AWS profile from Step 1)

Choice [1-4]:
```

**Choice 1 — MongoDB Atlas:**
```
MongoDB Atlas connection string (from Atlas UI → Connect → Drivers):
  Format: mongodb+srv://<user>:<password>@<cluster>.mongodb.net/<dbname>?retryWrites=true&w=majority

Paste connection string (credentials will not be echoed to terminal):
```
Set:
```bash
MONGO_URL="${ATLAS_CONNECTION_STRING}"
MONGO_PASSWORD="${password-extracted-from-string}"
```
Note: store MONGO_URL in `.env` as `MONGO_URL=<value>` and `ITENTIAL_MONGO_PASSWORD=<password>`. Credentials are never written to tracked files.

**Choice 2 / 3 — Existing endpoint:**
```
MongoDB host (e.g. my-docdb.cluster-xyz.us-east-2.docdb.amazonaws.com):
MongoDB port [27017]:
Database name [itential]:
MongoDB username:
MongoDB password (not echoed):
```
Build: `MONGO_URL="mongodb://${MONGO_USER}:${MONGO_PASS}@${MONGO_HOST}:${MONGO_PORT}/${MONGO_DB}?tls=true&tlsCAFile=/tmp/rds-combined-ca-bundle.pem"` (for DocumentDB TLS; adjust for other sources).

**Choice 4 — Provision DocumentDB:**
```
AWS DocumentDB cluster name [itential-mongo]:
Instance class [db.r6g.large] (minimum) or [db.r6g.2xlarge] (production):
Number of instances [2]:
```

Show an explicit confirmation with CPU/memory before running (`db.r6g.large` = 2 vCPU / 16 GB per instance; `db.r6g.2xlarge` = 8 vCPU / 64 GB per instance):

```
══════════════════════════════════════════════════════════════
  FINAL CONFIRMATION — Provision AWS DocumentDB (MongoDB)
══════════════════════════════════════════════════════════════
  Method:          Provision new DocumentDB cluster
  Cluster name:    {cluster name}
  Instance class:  {instance class}  ({vCPU} vCPU / {RAM} GB per instance)
  Instances:       {N}  → total {N × vCPU} vCPU / {N × RAM} GB

This will incur AWS costs for as long as the cluster exists.
Type 'yes provision documentdb' to confirm:
══════════════════════════════════════════════════════════════
```

Then show and confirm the `aws docdb create-db-cluster` + `create-db-instance` commands before running. After provisioning, retrieve the endpoint and set `MONGO_URL`.

Offer to persist to `.env`:
```bash
MONGO_URL=mongodb+srv://...
ITENTIAL_MONGO_PASSWORD=...
```

---

#### Step 2.k8s.3 — Redis Configuration

*Skip this step if Step 1e already resolved Redis via confirmed reuse of an existing instance.*

The Itential Helm charts do NOT include Redis — an external instance is required.

```
External Redis is required. Select the source:

  1) AWS ElastiCache (Valkey/Redis OSS) — provide primary endpoint
  2) Existing Redis host               — provide host:port
  3) Provision AWS ElastiCache         — I'll create a cluster (uses AWS profile from Step 1)

Choice [1-3]:
```

**Choice 1 / 2 — Existing endpoint:**
```
Redis host (e.g. my-cache.abc123.ng.0001.use2.cache.amazonaws.com):
Redis port [6379]:
Redis password (press Enter if no AUTH):
```
Set:
```bash
REDIS_HOST="${host}"
REDIS_PORT="${port:-6379}"
REDIS_PASSWORD="${redis_auth_token}"
ITENTIAL_REDIS_PASSWORD="${REDIS_PASSWORD}"
```

**Choice 3 — Provision ElastiCache Redis OSS (VPC-aware, grade-sized):**

If `EKS_VPC_ID` / `EKS_SUBNET_IDS` / `EKS_NODE_SG` are not already set (existing cluster path),
fetch them first:

```bash
EKS_VPC_ID=$(aws eks describe-cluster \
    --name "${EKS_CLUSTER_NAME}" --region "${EKS_CLUSTER_REGION}" ${CRED_FLAGS} \
    --query 'cluster.resourcesVpcConfig.vpcId' --output text)

EKS_SUBNET_IDS=$(aws eks describe-cluster \
    --name "${EKS_CLUSTER_NAME}" --region "${EKS_CLUSTER_REGION}" ${CRED_FLAGS} \
    --query 'cluster.resourcesVpcConfig.subnetIds' \
    --output json | python3 -c "import sys,json; print(' '.join(json.load(sys.stdin)))")

EKS_NODE_SG=$(aws eks describe-cluster \
    --name "${EKS_CLUSTER_NAME}" --region "${EKS_CLUSTER_REGION}" ${CRED_FLAGS} \
    --query 'cluster.resourcesVpcConfig.clusterSecurityGroupId' --output text)
```

Show and confirm each phase before running:

```bash
# Phase A — ElastiCache subnet group (reuses EKS VPC subnets)
ELASTICACHE_SUBNET_GROUP="itential-${EKS_CLUSTER_NAME}-subnet"

aws elasticache create-cache-subnet-group \
    --cache-subnet-group-name "${ELASTICACHE_SUBNET_GROUP}" \
    --cache-subnet-group-description "Itential repro — ${EKS_CLUSTER_NAME}" \
    --subnet-ids ${EKS_SUBNET_IDS} \
    --region "${EKS_CLUSTER_REGION}" \
    ${CRED_FLAGS}
echo "✅ Subnet group ${ELASTICACHE_SUBNET_GROUP} created"
```

```bash
# Phase B — Security group allowing EKS nodes → ElastiCache on 6379
ELASTICACHE_SG_ID=$(aws ec2 create-security-group \
    --group-name "itential-elasticache-${EKS_CLUSTER_NAME}" \
    --description "ElastiCache Redis — allow EKS nodes port 6379" \
    --vpc-id "${EKS_VPC_ID}" \
    --region "${EKS_CLUSTER_REGION}" \
    ${CRED_FLAGS} \
    --query 'GroupId' --output text)

aws ec2 authorize-security-group-ingress \
    --group-id "${ELASTICACHE_SG_ID}" \
    --protocol tcp --port 6379 \
    --source-group "${EKS_NODE_SG}" \
    --region "${EKS_CLUSTER_REGION}" \
    ${CRED_FLAGS}

echo "✅ ElastiCache SG ${ELASTICACHE_SG_ID} — allows ${EKS_NODE_SG} on 6379"
```

```bash
# Phase C — Redis OSS replication group (grade-aware sizing, AUTH enabled)
REDIS_AUTH_TOKEN=$(openssl rand -hex 32)

if [ "${K8S_CLUSTER_GRADE}" = "production" ]; then
    CACHE_NODE_TYPE="cache.r7g.xlarge"   # 4 vCPU / 26 GB
else
    CACHE_NODE_TYPE="cache.r7g.large"    # 2 vCPU / 13 GB
fi
```

```
══════════════════════════════════════════════════════════════
  FINAL CONFIRMATION — Provision AWS ElastiCache (Redis)
══════════════════════════════════════════════════════════════
  Method:          Provision new ElastiCache replication group
  Grade:           {K8S_CLUSTER_GRADE}
  Node type:       {CACHE_NODE_TYPE}  ({2 or 4} vCPU / {13 or 26} GB)
  Cache clusters:  1

This will incur AWS costs for as long as the replication group exists.
Type 'yes provision elasticache' to confirm:
══════════════════════════════════════════════════════════════
```

On confirmation:

```bash
aws elasticache create-replication-group \
    --replication-group-id "itential-${EKS_CLUSTER_NAME}-redis" \
    --description "Itential ${K8S_CLUSTER_GRADE} repro Redis" \
    --engine redis \
    --engine-version "7.1" \
    --cache-node-type "${CACHE_NODE_TYPE}" \
    --num-cache-clusters 1 \
    --auth-token "${REDIS_AUTH_TOKEN}" \
    --at-rest-encryption-enabled \
    --transit-encryption-enabled \
    --cache-subnet-group-name "${ELASTICACHE_SUBNET_GROUP}" \
    --security-group-ids "${ELASTICACHE_SG_ID}" \
    --region "${EKS_CLUSTER_REGION}" \
    ${CRED_FLAGS}

echo "⏳ ElastiCache provisioning — typical wait: 5–8 minutes"
```

```bash
# Phase D — Wait for available and extract endpoint (standard retry policy)
MAX_RETRIES=5; DELAY=5
for i in $(seq 1 $MAX_RETRIES); do
    STATUS=$(aws elasticache describe-replication-groups \
        --replication-group-id "itential-${EKS_CLUSTER_NAME}-redis" \
        --region "${EKS_CLUSTER_REGION}" ${CRED_FLAGS} \
        --query 'ReplicationGroups[0].Status' --output text 2>/dev/null)
    [ "${STATUS}" = "available" ] && break
    echo "[$i/$MAX_RETRIES] Status: ${STATUS} — retrying in ${DELAY}s"
    sleep $DELAY
done

REDIS_HOST=$(aws elasticache describe-replication-groups \
    --replication-group-id "itential-${EKS_CLUSTER_NAME}-redis" \
    --region "${EKS_CLUSTER_REGION}" ${CRED_FLAGS} \
    --query 'ReplicationGroups[0].NodeGroups[0].PrimaryEndpoint.Address' \
    --output text)

REDIS_PORT=6379
ITENTIAL_REDIS_PASSWORD="${REDIS_AUTH_TOKEN}"

echo "✅ Redis ready at ${REDIS_HOST}:${REDIS_PORT}"
echo "   AUTH token: ${REDIS_AUTH_TOKEN:0:8}...  (full value offered for .env save below)"
```

Offer to persist (display AUTH token masked; write full value to `.env`):
```bash
REDIS_HOST=<extracted-endpoint>
REDIS_PORT=6379
ITENTIAL_REDIS_PASSWORD=<auth-token>
```

---

#### Step 2.k8s.4 — EKS Prerequisites: Required K8s Components

Check and install the required Kubernetes components for Itential on EKS.

**macOS control-plane prereq check (run when uname is Darwin):**

```bash
if [[ "$(uname)" == "Darwin" ]]; then
    echo "=== macOS Control-Plane Prerequisites ==="

    brew --version > /dev/null 2>&1 \
        && echo "✅ Homebrew available" \
        || echo "❌ Homebrew not found — install from https://brew.sh"

    aws --version 2>/dev/null | grep -q "aws-cli/2" \
        && echo "✅ AWS CLI v2" \
        || echo "❌ AWS CLI v2 not found — brew install awscli"

    eksctl version > /dev/null 2>&1 \
        && echo "✅ eksctl $(eksctl version)" \
        || echo "⚠️  eksctl not found — brew install eksctl  (required for EKS provisioning)"

    kubectl version --client > /dev/null 2>&1 \
        && echo "✅ kubectl available" \
        || echo "❌ kubectl not found — brew install kubectl"

    HELM_VER_RAW=$(helm version --short 2>/dev/null)
    [ -n "${HELM_VER_RAW}" ] \
        && echo "✅ Helm ${HELM_VER_RAW}" \
        || echo "❌ Helm not found — brew install helm"

    docker info > /dev/null 2>&1 \
        && echo "✅ Docker running (needed for ECR docker login)" \
        || echo "⚠️  Docker not running — start Docker Desktop or OrbStack"

    echo ""
    echo "ℹ️  EKS worker nodes run in AWS — local RAM/CPU are not a constraint."
    echo "   A MacBook with 8 GB RAM can provision and manage production-grade EKS."
fi
```

**Check tool availability:**
```bash
# kubectl
kubectl version --client > /dev/null 2>&1 && echo "✅ kubectl available" \
  || echo "❌ kubectl not found — brew install kubectl"

# Helm version
HELM_VER=$(helm version --short 2>/dev/null | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+')
python3 -c "
ver = '${HELM_VER}'.lstrip('v').split('.')
req = [3, 15, 0]
ok = list(map(int, ver)) >= req
print(f'{'✅' if ok else '❌'} Helm {\"${HELM_VER}\"} — {'meets' if ok else 'need'} 3.15.0+')
"
```

**Check EKS prerequisites:**

```bash
echo "=== EKS Kubernetes preflight ==="

# Node resources
echo "--- Nodes ---"
kubectl get nodes -o custom-columns=\
"NAME:.metadata.name,STATUS:.status.conditions[-1].type,CPU:.status.capacity.cpu,MEM:.status.capacity.memory,VERSION:.status.nodeInfo.kubeletVersion"

# cert-manager
if kubectl get pods -n cert-manager --no-headers 2>/dev/null | grep -q Running; then
    echo "✅ cert-manager running"
else
    echo "⚠️  cert-manager not found — offer to install below"
    INSTALL_CERTMANAGER=true
fi

# AWS Load Balancer Controller
if kubectl get deployment aws-load-balancer-controller -n kube-system > /dev/null 2>&1; then
    echo "✅ AWS Load Balancer Controller installed"
else
    echo "⚠️  AWS Load Balancer Controller not found — offer to install below"
    INSTALL_AWSLBC=true
fi

# EBS CSI driver
if kubectl get daemonset ebs-csi-node -n kube-system > /dev/null 2>&1; then
    echo "✅ EBS CSI driver running"
else
    echo "⚠️  EBS CSI driver not found — required for StorageClass; offer to install"
    INSTALL_EBSCSI=true
fi

# StorageClass
kubectl get storageclass iap-ebs-gp3 > /dev/null 2>&1 \
  && echo "✅ StorageClass iap-ebs-gp3 exists" \
  || echo "⚠️  StorageClass iap-ebs-gp3 not found — Step 5c will create it"
```

**Gotcha — install cert-manager cluster-wide, never as a per-chart subchart dependency.** Setting `certManager.enabled: true` inside the `iap`/`iag5` Helm values causes a chicken-and-egg problem: the chart's `--dry-run` (and often the real install) needs cert-manager's CRDs (`Issuer`, `ClusterIssuer`, `Certificate`) to already exist. Always install cert-manager once, cluster-wide (block below), then set `certManager.enabled: false` in every chart's values file — the chart still creates its own `Issuer`/`Certificate` objects, it just doesn't try to install the controller itself.

**Gotcha — `ClusterIssuer` vs namespaced `Issuer` resolve their CA secret in different namespaces.** A `ClusterIssuer`'s `ca.secretName` is looked up in the **cert-manager controller's own namespace** (`cert-manager`), regardless of which namespace the issuer "belongs" to. A namespaced `Issuer`'s `ca.secretName` is looked up in its own namespace. The `iap` chart defaults `issuer.kind: ClusterIssuer`; the `iag5` chart defaults `issuer.kind: Issuer`. If both charts share one CA secret, that secret must exist in **both** the app namespace (for the `Issuer`) **and** the `cert-manager` namespace (for the `ClusterIssuer`) — copy it explicitly:
```bash
kubectl get secret iap-ca-secret -n "${NAMESPACE}" -o yaml \
  | sed "s/namespace: ${NAMESPACE}/namespace: cert-manager/" \
  | kubectl apply -f -
```
A `Certificate` stuck in `Ready: False` with no obvious error is the symptom — check `kubectl describe certificate <name> -n <ns>` and confirm the referenced CA secret actually exists in the namespace the issuer kind expects.

**Install missing components (if warranted):**

For each missing component, show the install command and ask "Install? [yes / skip]":

```bash
# cert-manager
if [ "${INSTALL_CERTMANAGER}" = "true" ]; then
    echo "Install cert-manager?"
    # On yes:
    kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
    kubectl wait --for=condition=Available deployment --all -n cert-manager --timeout=120s
    echo "✅ cert-manager installed"
fi

# AWS Load Balancer Controller (requires service account + IRSA)
if [ "${INSTALL_AWSLBC}" = "true" ]; then
    echo "Install AWS Load Balancer Controller?"
    # On yes:
    helm repo add eks https://aws.github.io/eks-charts 2>/dev/null || true
    helm repo update eks
    # Note: IRSA (IAM role for service account) must exist. Show the IAM policy requirement.
    echo "⚠️  AWS LBC requires an IAM role with the AWSLoadBalancerControllerIAMPolicy attached."
    echo "    Follow: https://docs.aws.amazon.com/eks/latest/userguide/aws-load-balancer-controller.html"
    echo "    IAM role ARN (set in .env as EKS_LBC_ROLE_ARN or enter now):"
    # read EKS_LBC_ROLE_ARN
    helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
        --namespace kube-system \
        --set clusterName="${EKS_CLUSTER_NAME}" \
        --set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"="${EKS_LBC_ROLE_ARN}"
    echo "✅ AWS Load Balancer Controller installed"
fi

# EBS CSI driver via EKS add-on (if eksctl available)
if [ "${INSTALL_EBSCSI}" = "true" ]; then
    echo "Install EBS CSI driver add-on?"
    # On yes:
    aws eks create-addon \
        ${CRED_FLAGS} \
        --cluster-name "${EKS_CLUSTER_NAME}" \
        --region "${EKS_CLUSTER_REGION}" \
        --addon-name aws-ebs-csi-driver
    echo "✅ EBS CSI driver add-on installation initiated"
fi
```

**ExternalDNS (optional — automates DNS record creation):**
```
Install ExternalDNS? (optional — automates DNS entry creation for the IAP ingress hostname)
  Requires: Route53 hosted zone + IAM policy for ExternalDNS
  [yes / skip]:
```

If yes, show the Helm install command (requires `K8S_HOSTNAME` and Route53 zone ID from `.env` or prompt). Do not install without engineer approval.

**Print preflight summary table:**
```
╔══════════════════════════════════════════════════════════════╗
║  Kubernetes Preflight Summary                                ║
╠═══════════════════════════╦══════════════════════════════════╣
║  Cluster grade            ║ {minimum | production}           ║
║  Node type                ║ {EKS_NODE_TYPE}                  ║
║  Kubernetes version       ║ {K8S_VERSION}                    ║
║  Nodes                    ║ {node count} ready               ║
║  cert-manager             ║ ✅ / ⚠️  not installed           ║
║  AWS LBC                  ║ ✅ / ⚠️  not installed           ║
║  EBS CSI driver           ║ ✅ / ⚠️  not installed           ║
║  StorageClass iap-ebs-gp3 ║ ✅ / ⚠️  will create in Step 5c ║
║  MongoDB                  ║ {MONGO_URL masked to host only}  ║
║  Redis                    ║ {REDIS_HOST}:{REDIS_PORT}        ║
╚═══════════════════════════╩══════════════════════════════════╝
```

**Abort if kubectl or Helm are missing.** Warn on missing cert-manager / AWS LBC / EBS CSI but allow engineer to continue if they confirm the warnings.

Proceed to Step 5 for the Helm deployment.

---

## Step 3 — Clone and Configure Dev Stack (Docker paths)

*Skip this step for K8s — proceed to Step 5.*

### Step 3a — Clone or update repo

```bash
DEVSTACK_DIR="${HOME}/itential-dev-stack"

if [ -d "${DEVSTACK_DIR}/.git" ]; then
    echo "Found existing dev stack at ${DEVSTACK_DIR} — pulling latest"
    git -C "${DEVSTACK_DIR}" pull origin main
else
    echo "Cloning itential-dev-stack..."
    git clone https://github.com/itential/itential-dev-stack "${DEVSTACK_DIR}"
fi

cd "${DEVSTACK_DIR}"
echo "Dev stack version: $(git log -1 --format='%h %s')"
```

**For Docker on VM — install Docker Engine if not present (freshly provisioned EC2):**

If the Step 2 preflight showed `❌ Docker not running`, install Docker Engine on the VM before transferring the dev stack:

```bash
ssh -i "${SSH_KEY}" "${SSH_USER}@${SSH_HOST}" "
set -e
echo '==> Installing Docker Engine on Rocky Linux 9...'
sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker \${USER}
echo '✅ Docker Engine installed and started'
docker info > /dev/null && echo '✅ Docker responding' || echo '❌ Docker not responding — check systemctl status docker'
"
echo ""
echo "⚠️  SSH reconnect needed to pick up docker group membership."
echo "   Subsequent commands re-open the SSH session automatically."
```

**For Docker on VM:** after cloning locally, transfer to the VM:

```bash
scp -i "${SSH_KEY}" -r "${DEVSTACK_DIR}" "${SSH_USER}@${SSH_HOST}:~/itential-dev-stack"
```

### Step 3b — Select platform version

Ask the engineer:
```
What IAP version should the reproduction environment run?
  Example: 6.5.2 (exact maintenance release) or 6 (latest 6.x rolling)
  Version to reproduce: 
```

Set `IAP_VERSION` from engineer input.

Map to image variables:
```bash
ECR=497639811223.dkr.ecr.us-east-2.amazonaws.com
PLATFORM_IMAGE="${ECR}/automation-platform-config-lcm:${IAP_VERSION}"
```

Ask: "Does this issue involve IAG? If yes, which version — 4 or 5?"
- No IAG → `GATEWAY4_ENABLED=false`, `GATEWAY5_ENABLED=false`, `STACK_PROFILE=platform`
- IAG4 → `GATEWAY4_ENABLED=true`, `GATEWAY4_IMAGE="${ECR}/automation-gateway:${GATEWAY4_VERSION}"`, `STACK_PROFILE=full`
- IAG5 → `GATEWAY5_ENABLED=true`, `GATEWAY5_IMAGE="${ECR}/automation-gateway5:${GATEWAY5_VERSION}"`, `STACK_PROFILE=full`

### Step 3c — Build .env file

```bash
# Generate encryption key
ENCRYPTION_KEY=$(openssl rand -hex 32)

# Determine BIND_ADDRESS
if [ "${DEPLOY_TYPE}" = "docker-local" ]; then
    BIND_ADDRESS="127.0.0.1:"   # localhost-only; change to "" to expose on network
else
    BIND_ADDRESS=""             # expose on all interfaces (VM deployment)
fi

# Write .env from .env.example
cp .env.example .env

# Patch values
python3 - <<PYEOF
import re

patches = {
    'PLATFORM_IMAGE': '${PLATFORM_IMAGE}',
    'ITENTIAL_ENCRYPTION_KEY': '${ENCRYPTION_KEY}',
    'STACK_PROFILE': '${STACK_PROFILE}',
    'GATEWAY4_ENABLED': '${GATEWAY4_ENABLED:-false}',
    'GATEWAY5_ENABLED': '${GATEWAY5_ENABLED:-false}',
    'BIND_ADDRESS': '${BIND_ADDRESS}',
}

with open('.env') as f:
    content = f.read()

for key, val in patches.items():
    content = re.sub(rf'^{key}=.*', f'{key}={val}', content, flags=re.MULTILINE)
    if f'{key}=' not in content:
        content += f'\n{key}={val}'

with open('.env', 'w') as f:
    f.write(content)
print('✅ .env written')
PYEOF
```

Show the engineer the final `.env` with the encryption key masked (first 8 + `...`):
```
PLATFORM_IMAGE=497639811223.dkr.ecr.us-east-2.amazonaws.com/automation-platform-config-lcm:6.5.2
ITENTIAL_ENCRYPTION_KEY=a3f9b2c1...  [64-char hex, stored only in this .env]
STACK_PROFILE=platform
GATEWAY4_ENABLED=false
GATEWAY5_ENABLED=false
BIND_ADDRESS=127.0.0.1:
```

---

### Step 3d — Final Deployment & Resource Confirmation

**This is the last checkpoint before any container is started.** Check the dev stack's own `docker-compose.yml` for explicit `deploy.resources` / `cpus` / `mem_limit` settings on the platform, MongoDB, Redis, and gateway services:

```bash
grep -A3 -E "^\s*(cpus|mem_limit|mem_reservation):" "${DEVSTACK_DIR}/docker-compose.yml" 2>/dev/null \
  || echo "No explicit per-service CPU/memory limits found in docker-compose.yml"
```

Present the final summary and require explicit approval before starting anything:

```
══════════════════════════════════════════════════════════════
  FINAL CONFIRMATION — {Docker local | Docker on VM}
══════════════════════════════════════════════════════════════
  Method:        {Docker Compose — this machine | Docker Compose on {SSH_HOST}}
  IAP version:   {IAP_VERSION}
  Gateway:       {none | IAG4 {version} | IAG5 {version}}

  Container resource limits:
    Platform:    {explicit limit found in docker-compose.yml, or
                  "No explicit limit — shares {host}'s full CPU/RAM pool"}
    MongoDB:     {same pattern}
    Redis:       {same pattern}
    Gateway:     {same pattern, if enabled}

  {If Docker local: "Host: this machine — Docker Desktop's configured
   resource pool (Settings → Resources) is the effective ceiling."}
  {If Docker on VM (existing): "Host: {SSH_HOST} — whatever CPU/RAM that
   VM has is the effective ceiling; not managed by this session."}
  {If Docker on VM (freshly provisioned in this session): "Host: the
   {INSTANCE_TYPE} instance provisioned above ({vCPU} vCPU / {RAM} GB) —
   already confirmed."}

Proceed and start the stack? [yes / set limits first / abort]:
══════════════════════════════════════════════════════════════
```

**On "set limits first":** offer to add `cpus:` / `mem_limit:` overrides to a `docker-compose.override.yml` for services the engineer wants constrained (useful when trying to faithfully reproduce a customer's resource-constrained environment), then re-present this confirmation.

**On "yes":** proceed to Step 4.

---

## Step 4 — Start Services (Docker paths)

### Step 4a — ECR login (Docker local)

If `DEPLOY_TYPE=docker-local`, the Step 1d ECR login already ran on this machine.

If `DEPLOY_TYPE=docker-vm`, transfer the ECR token to the VM:

```bash
# Get token locally, push to VM
if [ "${CRED_MODE}" = "static-env-vars" ]; then
    TOKEN=$(aws ecr get-login-password --region us-east-2)
else
    TOKEN=$(aws ecr get-login-password --region us-east-2 --profile "${SESSION_AWS_PROFILE}")
fi

ssh -i "${SSH_KEY}" "${SSH_USER}@${SSH_HOST}" \
    "echo '${TOKEN}' | docker login --username AWS --password-stdin 497639811223.dkr.ecr.us-east-2.amazonaws.com"
```

### Step 4b — Start the stack

Run on local or via SSH:

```bash
# Option A — automated setup (recommended for first run)
make setup

# Option B — step by step (if setup fails or for debugging)
# make generate-key     # if key not already set in .env
# make certs            # generate TLS certs
# make up               # start services
```

**For Docker on VM, run via SSH:**
```bash
ssh -i "${SSH_KEY}" "${SSH_USER}@${SSH_HOST}" "cd ~/itential-dev-stack && make setup"
```

### Step 4c — Wait for health

Poll until all services are healthy (timeout 3 minutes):

```python
import subprocess, time, sys, json

MAX_RETRIES, DELAY = 5, 5
for attempt in range(1, MAX_RETRIES + 1):
    result = subprocess.run(
        ["docker", "compose", "ps", "--format", "json"],
        capture_output=True, text=True
    )
    services = [json.loads(l) for l in result.stdout.strip().split('\n') if l]
    unhealthy = [s for s in services if s.get('Health') not in ('healthy', '')]
    if not unhealthy:
        print("✅ All services healthy")
        break
    print(f"[{attempt}/{MAX_RETRIES}] Waiting... unhealthy: {[s['Service'] for s in unhealthy]}")
    time.sleep(DELAY)
else:
    print("❌ Failed after 5 attempts — checking logs:")
    subprocess.run(["docker", "compose", "logs", "--tail=50"])
    sys.exit(1)
```

### Step 4d — Verify platform health

```bash
# Wait a moment for IAP to finish init
sleep 5

curl -sk https://localhost:3443/health/platform | python3 -m json.tool 2>/dev/null \
  || curl -sk http://localhost:3000/health/platform
```

### Step 4e — Post-Build Component Summary

**Detect the actual running versions — never assume the requested tag was what actually started.** Image tags can resolve to a different patch version (`6` rolling tags, cached layers), and MongoDB/Redis versions are set by the dev-stack's own compose file, not by anything this skill controls directly.

```bash
DC_EXEC="docker compose exec -T"
[ "${DEPLOY_TYPE}" = "docker-vm" ] && DC_EXEC="ssh -i ${SSH_KEY} ${SSH_USER}@${SSH_HOST} cd ~/itential-dev-stack && docker compose exec -T"

# Platform — actual version from the health endpoint (falls back to requested tag if the field is absent)
PLATFORM_VERSION_ACTUAL=$(curl -sk https://localhost:3443/health/platform 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version') or d.get('platformVersion') or '${IAP_VERSION} (requested — actual not reported by endpoint)')" 2>/dev/null \
    || echo "${IAP_VERSION} (requested — health endpoint unreachable)")

# MongoDB — actual server version
MONGO_VERSION_ACTUAL=$(${DC_EXEC} mongo mongosh --quiet --eval "db.version()" 2>/dev/null \
    || ${DC_EXEC} mongo mongo --quiet --eval "db.version()" 2>/dev/null \
    || echo "unknown — exec into the mongo container to check")

# Redis — actual server version
REDIS_VERSION_ACTUAL=$(${DC_EXEC} redis redis-cli INFO server 2>/dev/null \
    | grep "redis_version:" | cut -d: -f2 | tr -d '\r' \
    || echo "unknown — exec into the redis container to check")

# Gateway — actual version (if enabled)
if [ "${GATEWAY5_ENABLED}" = "true" ]; then
    GATEWAY_VERSION_ACTUAL=$(${DC_EXEC} gateway5 cat /opt/automation-gateway/VERSION 2>/dev/null || echo "${GATEWAY5_VERSION} (requested)")
elif [ "${GATEWAY4_ENABLED}" = "true" ]; then
    GATEWAY_VERSION_ACTUAL=$(${DC_EXEC} gateway4 python3 -c "import automation_gateway; print(automation_gateway.__version__)" 2>/dev/null || echo "${GATEWAY4_VERSION} (requested)")
fi
```

Print the full build summary:

```
╔════════════════════════════════════════════════════════════════════╗
║  ENVIRONMENT BUILD SUMMARY — Docker ({docker-local | docker-vm})   ║
╠════════════════════════════════════════════════════════════════════╣
║  Method:          {Docker Compose — this machine | on {SSH_HOST}} ║
║  Started:         {timestamp}                                     ║
╠═══════════════╦═══════════════════════╦════════════════════════════╣
║ Component     ║ Version               ║ Access                     ║
╠═══════════════╬═══════════════════════╬════════════════════════════╣
║ Platform      ║ {PLATFORM_VERSION_ACTUAL} ║ https://localhost:3443 ║
║ MongoDB       ║ {MONGO_VERSION_ACTUAL}    ║ localhost:27017         ║
║ Redis         ║ {REDIS_VERSION_ACTUAL}    ║ localhost:6379          ║
║ Gateway       ║ {GATEWAY_VERSION_ACTUAL or "not deployed"} ║ {gateway URL if enabled} ║
╠═══════════════╩═══════════════════════╩════════════════════════════╣
║ Default creds:  admin@itential.com / admin                         ║
║ Env file:       repro/{ISD_TICKET_KEY}/.env                        ║
╚════════════════════════════════════════════════════════════════════╝
```

**If any version shows "unknown" or a container name mismatch error**, check the dev-stack's actual service names (`docker compose config --services`) — they may differ across dev-stack versions.

Create `repro/{ISD_TICKET_KEY}/.env` with the dev stack connection details so the orchestrator and sub-skills can use this environment:

```bash
mkdir -p "repro/${ISD_TICKET_KEY}"
cat > "repro/${ISD_TICKET_KEY}/.env" <<EOF
PLATFORM_URL=https://localhost:3443
AUTH_METHOD=basic
CLIENT_ID=admin@itential.com
CLIENT_SECRET=admin
# Reproduction environment — Docker local via /deploy-containers
# IAP version: ${IAP_VERSION}
# Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
echo "✅ repro/${ISD_TICKET_KEY}/.env written — orchestrator will auto-detect on next /troubleshoot run"
```

---

### Step 4f — Post-Build Learnings & Documentation Check

**Run this after every build, successful or not.** The goal is to catch drift between what this skill documents and what actually happened — before that gap causes the next engineer to hit the same surprise unguided.

**Reflect on the session** — answer each honestly, based on what actually happened in this conversation, not what should have happened:

1. Did any command in Steps 0–4e fail in a way not already covered by an existing gotcha callout in this SKILL.md, requiring an improvised workaround?
2. Did any step take meaningfully longer or shorter than its Quick Reference time estimate (more than ~50% off)?
3. Did the engineer have to supply information, make a decision, or answer a question this skill's prompts didn't anticipate?
4. Did any tool, API, or container behave differently than documented — a flag that doesn't exist, a different default, a version requirement that turned out stricter or looser than stated?
5. Did the actual detected versions (Step 4e) reveal anything worth noting — e.g., a "latest" tag resolving to an unexpectedly old/new version?

**Check whether each "yes" is already documented:**

```bash
# Search this SKILL.md for an existing callout on the same topic before assuming it's new
grep -n -i "{keyword from the learning}" .claude/skills/deploy-containers/SKILL.md

# Search the HTML guide too — a learning might already be captured there even if the
# SKILL.md itself wasn't updated
grep -n -i "{keyword}" docs/troubleshooting-agent-guide.html
```

**If every "yes" is already covered:** state so explicitly and stop — do not manufacture a contribution where none is warranted.

**If genuinely new learnings exist, draft the exact addition** (matching the style of existing gotcha callouts elsewhere in this file — bold summary line, symptom, root cause, fix) and present it:

```
══════════════════════════════════════════════════════════════
  POST-BUILD LEARNINGS — this session
══════════════════════════════════════════════════════════════
  {learning 1}: NEW — not currently documented
  {learning 2}: already documented at Step {N} — no action

  Proposed addition to .claude/skills/deploy-containers/SKILL.md:
  ─────────────────────────────────────────────────────────
  {exact drafted text}
  ─────────────────────────────────────────────────────────

Contribute this update? [yes → runs /contribute skill-fix deploy-containers
                          / edit / skip]
══════════════════════════════════════════════════════════════
```

**On "yes":** invoke `/contribute skill-fix deploy-containers "{one-line description of the fix}"` to run the branch → write → diff → PR flow — do not write directly to `SKILL.md` outside that flow, even though this skill is repo-native and technically editable directly; routing through `/contribute` keeps every doc change reviewable as a PR rather than a silent edit.

---

## Step 5 — Kubernetes Deployment

*This section covers DEPLOY_TYPE=k8s only. Steps 3-4 are skipped.*

### Step 5a — Namespace and imagePullSecret

```bash
NAMESPACE="${K8S_NAMESPACE:-itential}"

# Create namespace (idempotent)
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
echo "✅ Namespace ${NAMESPACE} ready"

# Create ECR pull secret
echo "Creating ECR imagePullSecret..."
if [ "${CRED_MODE}" = "static-env-vars" ]; then
    ECR_TOKEN=$(aws ecr get-login-password --region us-east-2)
else
    ECR_TOKEN=$(aws ecr get-login-password --region us-east-2 --profile "${SESSION_AWS_PROFILE}")
fi

kubectl create secret docker-registry ecr-pull-secret \
    --namespace="${NAMESPACE}" \
    --docker-server=497639811223.dkr.ecr.us-east-2.amazonaws.com \
    --docker-username=AWS \
    --docker-password="${ECR_TOKEN}" \
    --dry-run=client -o yaml | kubectl apply -f -

unset ECR_TOKEN
echo "✅ ECR pull secret created"
```

### Step 5b — Platform Secrets

**Show each secret manifest to engineer before applying. Require explicit "yes".**

Pre-populate from variables set in Steps 2.k8s.2 and 2.k8s.3. For any value not yet set, prompt the engineer (never echo passwords):

```
Required K8s secret values — pre-populated from .env where available:
  (leave a field blank to be prompted; credentials are not echoed to terminal)

  Admin user password  [ITENTIAL_DEFAULT_USER_PASSWORD]: {blank — prompt}
  MongoDB URL          [{MONGO_URL masked to show host only}]: {use .env value / prompt to override}
  MongoDB password     [{ITENTIAL_MONGO_PASSWORD from .env or prompt}]:
  Redis host           [{REDIS_HOST}:{REDIS_PORT}]: {use .env value / prompt to override}
  Redis password       [{ITENTIAL_REDIS_PASSWORD from .env or blank}]:
  CA cert path         [{TLS_CA_CERT_PATH from .env or 'skip'}]:
```

Generate encryption key automatically (or use existing if set in `.env`):
```bash
ENCRYPTION_KEY="${ITENTIAL_ENCRYPTION_KEY:-$(openssl rand -hex 32)}"
echo "ITENTIAL_ENCRYPTION_KEY — $([ -n "${ITENTIAL_ENCRYPTION_KEY}" ] && echo 'from .env' || echo 'generated')"
```

Build `ITENTIAL_MONGO_URL` from components if only host/port/password were provided:
```bash
# If MONGO_URL not already a full connection string, build it
if [ -z "${MONGO_URL}" ] && [ -n "${MONGO_HOST}" ]; then
    MONGO_URL="mongodb://${MONGO_USER}:${MONGO_PASSWORD}@${MONGO_HOST}:${MONGO_PORT:-27017}/itential"
fi
```

Show the manifest (password values masked) and require approval:

```bash
cat <<EOF
--- PROPOSED K8s SECRET MANIFEST (itential-platform-secrets) ---
apiVersion: v1
kind: Secret
metadata:
  name: itential-platform-secrets
  namespace: ${NAMESPACE}
type: Opaque
stringData:
  ITENTIAL_DEFAULT_USER_PASSWORD: "**hidden**"
  ITENTIAL_ENCRYPTION_KEY:        "${ENCRYPTION_KEY:0:8}...[64 chars]"
  ITENTIAL_MONGO_PASSWORD:        "**hidden**"
  ITENTIAL_MONGO_URL:             "${MONGO_URL//:\/\/*@/:\/\/**hidden**@}"
  ITENTIAL_REDIS_PASSWORD:        "**hidden**"
---
Apply this secret? [yes / abort]:
EOF
```

On approval:
```bash
kubectl create secret generic itential-platform-secrets \
    --namespace="${NAMESPACE}" \
    --from-literal=ITENTIAL_DEFAULT_USER_PASSWORD="${ADMIN_PASSWORD}" \
    --from-literal=ITENTIAL_ENCRYPTION_KEY="${ENCRYPTION_KEY}" \
    --from-literal=ITENTIAL_MONGO_PASSWORD="${MONGO_PASSWORD}" \
    --from-literal=ITENTIAL_MONGO_URL="${MONGO_URL}" \
    --from-literal=ITENTIAL_REDIS_PASSWORD="${REDIS_PASSWORD}" \
    --dry-run=client -o yaml | kubectl apply -f -

echo "✅ itential-platform-secrets applied"
```

**For IAG5** — also create `itential-gateway-secrets`. **Key name must be `gatewayEncryptionKey`** (confirmed against the `iag5` chart's `deployment-server.yaml`/`deployment-runner.yaml` `subPath` reference — `encryptionKey` is silently wrong and produces no error, just a missing key at mount time):
```bash
GATEWAY_ENC_KEY=$(openssl rand -hex 32)
kubectl create secret generic itential-gateway-secrets \
    --namespace="${NAMESPACE}" \
    --from-literal=gatewayEncryptionKey="${GATEWAY_ENC_KEY}" \
    --dry-run=client -o yaml | kubectl apply -f -
```

**For TLS CA** — ask engineer to provide `ca.crt` path (or skip if not needed):
```bash
kubectl create secret generic itential-ca \
    --namespace="${NAMESPACE}" \
    --from-file=ca.crt="${CA_CERT_PATH}" \
    --dry-run=client -o yaml | kubectl apply -f -
```

### Step 5c — StorageClass

Notes from Itential docs:
- Provisioner must be `ebs.csi.aws.com` (EBS only; **do NOT use EFS, NFS, or efs.csi.aws.com**)
- Start with **10 GB** per adapter PersistentVolume; resize upward as needed
- `volumeBindingMode: WaitForFirstConsumer` is required — pod must be scheduled first

```bash
# Check if iap-ebs-gp3 exists
if kubectl get storageclass iap-ebs-gp3 > /dev/null 2>&1; then
    echo "✅ StorageClass iap-ebs-gp3 already exists"
else
    echo "Creating StorageClass iap-ebs-gp3..."
    kubectl apply -f - <<'EOF'
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: iap-ebs-gp3
  annotations:
    storageclass.kubernetes.io/is-default-class: "false"
provisioner: ebs.csi.aws.com
parameters:
  type: gp3
volumeBindingMode: WaitForFirstConsumer
reclaimPolicy: Delete
allowVolumeExpansion: true
EOF
    echo "✅ StorageClass iap-ebs-gp3 created"
fi
```

---

### Step 5c.5 — Final Consolidated Confirmation (All Components)

**This is the last checkpoint before any application workload (Platform, MongoDB, Redis, Gateway) is deployed.** By this point every component's method and sizing is known — the EKS cluster (Step 2.k8s.1), MongoDB (Step 2.k8s.2), and Redis (Step 2.k8s.3) each already had their own confirmation if newly provisioned. This step aggregates everything into one final summary before the first Helm install runs.

Compute Platform/Gateway pod resource requests from grade (same values Step 5d's Helm values file will use):

```python
if K8S_CLUSTER_GRADE == "production":
    platform_req, platform_lim = ("4 vCPU / 8 GB", "16 vCPU / 32 GB")
else:
    platform_req, platform_lim = ("2 vCPU / 4 GB", "4 vCPU / 16 GB")
```

```
══════════════════════════════════════════════════════════════════════
  FINAL CONFIRMATION — Full Environment Before Deployment
══════════════════════════════════════════════════════════════════════
  Method:  Kubernetes (Helm charts) — grade: {K8S_CLUSTER_GRADE}

  ┌─────────────┬──────────────────────────────────┬──────────────────┐
  │ Component   │ Method                            │ CPU / Memory     │
  ├─────────────┼──────────────────────────────────┼──────────────────┤
  │ EKS nodes   │ {existing cluster | new eksctl}   │ {vCPU}×{count} vCPU /│
  │             │                                    │ {RAM}×{count} GB │
  │ MongoDB     │ {Atlas|DocumentDB(new)|on-prem}   │ {instance class + │
  │             │                                    │ vCPU/RAM, or      │
  │             │                                    │ "external — not   │
  │             │                                    │ managed here"}    │
  │ Redis       │ {ElastiCache(new)|existing}        │ {same pattern}    │
  │ Platform    │ Helm — {replicaCount} replica(s)  │ requests {platform_req}│
  │             │                                    │ limits  {platform_lim}│
  │ Gateway     │ {none|IAG4|IAG5} via Helm         │ {resource requests │
  │             │                                    │ if enabled}        │
  │ Adapter PV  │ {pv|layered|n/a}                   │ {K8S_ADAPTER_PV_SIZE}│
  └─────────────┴──────────────────────────────────┴──────────────────┘

  Ongoing AWS cost drivers: EKS cluster + nodes, MongoDB (if provisioned),
  Redis (if provisioned) — all continue billing until explicitly torn
  down (Step 6).

Proceed with deploying Platform/Gateway now? [yes / abort]:
══════════════════════════════════════════════════════════════════════
```

**Do not proceed to Step 5d without an explicit "yes" to this exact prompt** — the per-component confirmations earlier in Step 2 covered infrastructure creation individually; this is the one place the engineer sees (and approves) the whole picture at once before the application itself goes live.

---

### Step 5d — Helm Install: IAP Platform

**Before writing any values file — verify the chart's real schema.** Helm silently drops unknown keys in `--values` files: it does not error, does not warn, and the release still installs "successfully" while the intended override has zero effect. Never author a values file from memory or assumption. Always confirm first:

```bash
helm show values iap/iap > /tmp/iap-default-values.yaml
helm pull iap/iap --untar --untardir /tmp/iap-chart-inspect
grep -rn '\.Values\.' /tmp/iap-chart-inspect/iap/templates/ | less
```

Cross-check every key you're about to set against `/tmp/iap-default-values.yaml` and the template `grep` output before writing `/tmp/iap-values.yaml` below. The real `iap` chart schema uses `storageClass: {enabled, name}` (not `persistence.storageClassName`) and a flat `env:` map for Mongo/Redis connection settings (not nested `mongodb:`/`redis:` blocks) — those nested blocks do not exist in the chart and were a documentation error in earlier versions of this skill.

**Before running `helm upgrade --install`, check for a stale `pending-install` release from a prior interrupted session.** `helm upgrade --install --atomic --timeout` only rolls back/uninstalls on failure if the controlling Helm client process is still alive to observe the timeout. If that process was killed (session restart, terminal closed, agent context reset) mid-install, the release is left stuck in `pending-install` indefinitely — nothing will ever trigger its own cleanup, and a fresh `helm upgrade --install` against it will error or produce confusing partial-state behavior. Check and clear it first:
```bash
helm list -n "${NAMESPACE}" -a | grep -i pending
# If a stale pending-install (or pending-upgrade) release is found for iap/iag5:
helm uninstall iap -n "${NAMESPACE}"    # or iag5 — match the release name found above
```

Determine replica count from cluster grade:
- Minimum (dev/test): `replicaCount=1`
- Production: `replicaCount=2` (one pod per node, separate AZs — StatefulSet distributes automatically)

```bash
# Add Helm repo
helm repo add iap https://itential.github.io/iap-helm 2>/dev/null || true
helm repo update iap
echo "Latest iap chart: $(helm search repo iap/iap --output json | python3 -c 'import sys,json; print(json.load(sys.stdin)[0][\"app_version\"])')"

# Determine replicas from grade
REPLICA_COUNT=1
[ "${K8S_CLUSTER_GRADE}" = "production" ] && REPLICA_COUNT=2

# Determine ingress type
INGRESS_TYPE="${K8S_INGRESS_TYPE:-alb}"   # alb | nginx

# Build Helm values file (avoids long --set chains, easier to review)
cat > /tmp/iap-values.yaml <<EOF
image:
  repository: 497639811223.dkr.ecr.us-east-2.amazonaws.com/automation-platform-config-lcm
  tag: "${IAP_VERSION}"

imagePullSecrets:
  - name: ecr-pull-secret

replicaCount: ${REPLICA_COUNT}

# Platform pod resource requests (from Itential docs)
resources:
  requests:
    cpu: "$([ "${K8S_CLUSTER_GRADE}" = "production" ] && echo '4' || echo '2')"
    memory: "$([ "${K8S_CLUSTER_GRADE}" = "production" ] && echo '8Gi' || echo '4Gi')"
  limits:
    cpu: "$([ "${K8S_CLUSTER_GRADE}" = "production" ] && echo '16' || echo '4')"
    memory: "$([ "${K8S_CLUSTER_GRADE}" = "production" ] && echo '32Gi' || echo '16Gi')"

# StorageClass for platform data volumes — real chart key is storageClass.name, NOT persistence.storageClassName
storageClass:
  enabled: true
  name: iap-ebs-gp3

# IAP mounts a PVC for /var/log/itential and for /opt/itential/platform/services/custom
# (adapters/apps) — but the StatefulSet template only wires the volumeMount in when these
# flags are true. They default to false. Skipping this silently produces PVCs that exist
# but are never mounted, and app-level errors like "/var/log/itential is not a writable
# location" that give no hint the fix is a values.yaml flag.
mountLogVolume: true
mountAdapterVolume: true

# Mongo/Redis connection settings — flat env map, NOT nested mongodb:/redis: blocks
# (those keys do not exist in the chart and are silently ignored if used)
env:
  ITENTIAL_MONGO_URL: "${MONGO_URL}"
  ITENTIAL_MONGO_AUTH_ENABLED: "${ITENTIAL_MONGO_AUTH_ENABLED:-true}"
  ITENTIAL_MONGO_TLS_ENABLED: "${ITENTIAL_MONGO_TLS_ENABLED:-false}"
  ITENTIAL_REDIS_HOST: "${REDIS_HOST}"
  ITENTIAL_REDIS_PORT: "${REDIS_PORT:-6379}"
  # If Redis has no auth configured, this MUST be set to an empty string — the chart
  # defaults ITENTIAL_REDIS_USERNAME to "itential", which makes the platform attempt an
  # AUTH against a Redis server with no requirepass set, and fails with a generic
  # "Unable to connect to Redis. Please verify authentication properties are valid" error
  # that gives no indication the real cause is an unwanted AUTH attempt.
  ITENTIAL_REDIS_USERNAME: "${ITENTIAL_REDIS_USERNAME:-}"

# Startup/liveness/readiness probe tuning — IAP boots ~40 sequential named services
# (Tags, WorkFlowEngine, JsonForms, MOP, Search, GatewayManager, InventoryManager, etc.).
# The chart default startupProbe budget (failureThreshold 3 * periodSeconds 30 ≈ 270s) is
# frequently too short on constrained/local nodes (kind, small EC2, laptop Docker Desktop),
# causing the container to be killed mid-boot. Raise failureThreshold for minimum-grade /
# local clusters. Note: livenessProbe is an exec `pgrep` chain against named Pronghorn
# processes, NOT an HTTP check — the chart's livenessProbe.path key has no effect on it;
# only initialDelaySeconds/periodSeconds/timeoutSeconds/failureThreshold matter.
startupProbe:
  enabled: true
  failureThreshold: $([ "${K8S_CLUSTER_GRADE}" = "production" ] && echo '5' || echo '20')

livenessProbe:
  enabled: true
  timeoutSeconds: $([ "${K8S_CLUSTER_GRADE}" = "production" ] && echo '10' || echo '30')
  failureThreshold: $([ "${K8S_CLUSTER_GRADE}" = "production" ] && echo '3' || echo '10')

readinessProbe:
  enabled: true
  timeoutSeconds: $([ "${K8S_CLUSTER_GRADE}" = "production" ] && echo '10' || echo '30')
  failureThreshold: $([ "${K8S_CLUSTER_GRADE}" = "production" ] && echo '3' || echo '10')

# Ingress
ingress:
  enabled: ${K8S_HOSTNAME:+true}${K8S_HOSTNAME:-false}
  className: "$([ "${INGRESS_TYPE}" = "alb" ] && echo 'alb' || echo 'nginx')"
  annotations:
$(if [ "${INGRESS_TYPE}" = "alb" ]; then
cat <<'ANNOTATIONS'
    # AWS Load Balancer Controller annotations (Itential-documented)
    alb.ingress.kubernetes.io/scheme: "${K8S_INGRESS_SCHEME:-internet-facing}"
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/healthcheck-path: "/health/status?exclude-service=true"
    alb.ingress.kubernetes.io/healthcheck-port: "3443"
    alb.ingress.kubernetes.io/healthcheck-protocol: HTTPS
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS": 443},{"HTTPS": 8080}]'
    alb.ingress.kubernetes.io/websocket-paths: "/ws"
    alb.ingress.kubernetes.io/ssl-policy: ELBSecurityPolicy-TLS13-1-2-2021-06
    alb.ingress.kubernetes.io/target-group-attributes: >-
      stickiness.enabled=true,stickiness.lb_cookie.duration_seconds=3600
    alb.ingress.kubernetes.io/certificate-arn: "${ACM_CERT_ARN:-}"
ANNOTATIONS
else
cat <<'ANNOTATIONS'
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/proxy-connect-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-buffering: "off"
    nginx.ingress.kubernetes.io/configuration-snippet: |
      proxy_set_header Upgrade \$http_upgrade;
      proxy_set_header Connection "upgrade";
ANNOTATIONS
fi)
  hosts:
    - host: "${K8S_HOSTNAME:-}"
      paths:
        - path: /
          pathType: Prefix
EOF

echo "=== Helm values written to /tmp/iap-values.yaml — review before applying ==="
cat /tmp/iap-values.yaml

# Show dry-run
echo ""
echo "=== Helm dry-run (review before applying) ==="
helm upgrade --install iap iap/iap \
    --namespace="${NAMESPACE}" \
    --values /tmp/iap-values.yaml \
    --dry-run 2>&1 | head -100
echo ""
echo "Apply this Helm release? [yes / abort]:"
```

On approval, run without `--dry-run`:
```bash
helm upgrade --install iap iap/iap \
    --namespace="${NAMESPACE}" \
    --values /tmp/iap-values.yaml
echo "✅ IAP Helm release deployed"
```

**ACM certificate (if ALB ingress):** If `ACM_CERT_ARN` is not set and `K8S_HOSTNAME` is provided, ask the engineer for the ARN:
```
An ACM TLS certificate is required for the ALB.
ACM Certificate ARN (e.g. arn:aws:acm:us-east-2:123456789:certificate/...):
```
Set `ACM_CERT_ARN` and re-run the Helm upgrade.

### Step 5e — Helm Install: IAG (if needed)

**IAG5:**

Same rule as Step 5d — verify the chart's real schema before writing values, don't rely on `--set` chains for anything beyond the trivial image override:
```bash
helm show values iag5/iag5 > /tmp/iag5-default-values.yaml
helm pull iag5/iag5 --untar --untardir /tmp/iag5-chart-inspect
grep -rn '\.Values\.' /tmp/iag5-chart-inspect/iag5/templates/ | less
```

Bare `--set` flags for image/pull-secret alone are not sufficient — IAG5 also needs etcd disabled (unless a real etcd cluster is provisioned), cert-manager disabled at the chart level (cluster-wide cert-manager handles it, per the Step 2.k8s gotcha above), a namespaced `Issuer` + `Certificate` block (the `iag5` chart defaults `issuer.kind: Issuer`, not `ClusterIssuer` — see the CA-secret-namespace gotcha above), and `serverSettings` pointed at the IAP service so it can register:

```bash
helm repo add iag5 https://itential.github.io/iag5-helm 2>/dev/null || true
helm repo update iag5

cat > /tmp/iag5-values.yaml <<EOF
hostname: ${IAG5_HOSTNAME:-iag5.itential.local}
port: 50051
useTLS: true

etcd:
  enabled: false

certManager:
  enabled: false

image:
  repository: 497639811223.dkr.ecr.us-east-2.amazonaws.com/automation-gateway5
  tag: "${GATEWAY5_VERSION}"
  pullPolicy: "IfNotPresent"

imagePullSecrets:
  - name: ecr-pull-secret

service:
  type: ClusterIP

issuer:
  kind: Issuer
  enabled: true
  name: iag5-ca-issuer
  caSecretName: iap-ca-secret

certificate:
  enabled: true
  issuerRef:
    name: iag5-ca-issuer
    kind: Issuer
  renewBefore: 48h
  duration: 2160h
  dnsNames:
    - ${IAG5_HOSTNAME:-iag5.itential.local}
    - iag5-service.${NAMESPACE}.svc.cluster.local

resources:
  enabled: true

runnerSettings:
  replicaCount: 0

serverSettings:
  replicaCount: 1
  connectEnabled: true
  connectHosts: "iap-service.${NAMESPACE}.svc.cluster.local:8080"
  connectInsecureEnabled: ${IAG5_CONNECT_INSECURE:-true}

applicationSettings:
  clusterId: ${IAG5_CLUSTER_ID:-cluster_1}
  logLevel: ${IAG5_LOG_LEVEL:-INFO}
  storeBackend: memory
EOF

echo "=== Helm values written to /tmp/iag5-values.yaml — review before applying ==="
cat /tmp/iag5-values.yaml

helm upgrade --install iag5 iag5/iag5 \
    --namespace="${NAMESPACE}" \
    --values /tmp/iag5-values.yaml \
    --dry-run 2>&1 | head -100
echo "Apply IAG5 Helm release? [yes / skip]:"
```

On approval, run without `--dry-run`:
```bash
helm upgrade --install iag5 iag5/iag5 \
    --namespace="${NAMESPACE}" \
    --values /tmp/iag5-values.yaml
echo "✅ IAG5 Helm release deployed"
```

**Post-install pairing (required before the IAG5-to-IAP WebSocket handshake succeeds):** IAG5 connecting to IAP's Gateway Manager is not just a network/TLS matter — IAP validates the incoming client certificate against a `GATEWAYS` collection record in MongoDB, looked up by `clusterId`. Until this gateway is registered in IAP (via the IAP UI's Gateway Manager section, or its API), the connection will fail with a WebSocket close `1002 (protocol error): Uncaught error while executing validateCertificate` — this is expected for a freshly deployed, unregistered gateway, not a values.yaml or networking defect. Register the gateway (matching `applicationSettings.clusterId` above) before expecting IAG5 to show as connected.

**IAG4** (requires node labeling first):
```bash
# IAG4 requires node label + taint
echo "IAG4 requires a dedicated node. Which node should run IAG4?"
kubectl get nodes -o wide
echo "Node name: "
# Read NODE_NAME from engineer

kubectl label node "${NODE_NAME}" itential.io/app=iag --overwrite
kubectl taint node "${NODE_NAME}" itential.io/role=iag:NoSchedule --overwrite

helm repo add iag4 https://itential.github.io/iag4-helm 2>/dev/null || true
helm repo update iag4

helm upgrade --install iag4 iag4/iag4 \
    --namespace="${NAMESPACE}" \
    --set "image.repository=497639811223.dkr.ecr.us-east-2.amazonaws.com/automation-gateway" \
    --set "image.tag=${GATEWAY4_VERSION}" \
    --set "imagePullSecrets[0].name=ecr-pull-secret" \
    --dry-run
echo "Apply IAG4 Helm release? [yes / skip]:"
```

### Step 5f — Post-Install Verification

```bash
echo "=== Pod status ==="
kubectl get pods -n "${NAMESPACE}" -o wide

echo ""
echo "=== Services ==="
kubectl get svc -n "${NAMESPACE}"

echo ""
echo "Waiting for IAP StatefulSet to reach ready state..."
kubectl rollout status statefulset/iap -n "${NAMESPACE}" --timeout=300s \
  && echo "✅ IAP StatefulSet ready" \
  || echo "❌ Rollout timeout — check: kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/name=iap --tail=50"

echo ""
echo "=== Platform health check ==="
# Port-forward to verify health (docs health path: /health/status?exclude-service=true)
kubectl port-forward svc/iap 3443:3443 -n "${NAMESPACE}" &
PF_PID=$!
sleep 5

HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" \
    "https://localhost:3443/health/status?exclude-service=true" 2>/dev/null)

if [ "${HTTP_CODE}" = "200" ]; then
    echo "✅ IAP health endpoint returned 200"
    curl -sk "https://localhost:3443/health/status?exclude-service=true" | python3 -m json.tool 2>/dev/null | head -30
else
    echo "⚠️  Health endpoint returned ${HTTP_CODE} — checking logs:"
    kubectl logs -n "${NAMESPACE}" -l app.kubernetes.io/name=iap --tail=30
fi

kill $PF_PID 2>/dev/null
```

**Access options:**
1. **Port-forward (no ingress needed — for reproduction):**
   ```bash
   kubectl port-forward svc/iap 3443:3443 -n "${NAMESPACE}"
   # Open: https://localhost:3443
   # Credentials: admin@itential.com / [ITENTIAL_DEFAULT_USER_PASSWORD]
   ```

2. **Ingress (if K8S_HOSTNAME was set and Step 5g ingress was deployed):**
   ```
   https://{K8S_HOSTNAME}
   ```

### Step 5f.5 — Post-Build Component Summary

**Detect actual versions — never rely solely on what was requested.** The Helm chart tag, the DocumentDB/ElastiCache engine version chosen at provisioning, and Atlas/on-prem endpoints supplied by the engineer can all differ from what actually ends up running.

```bash
# Cluster
K8S_VERSION_ACTUAL=$(kubectl version -o json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('serverVersion',{}).get('gitVersion','unknown'))")
NODE_COUNT_ACTUAL=$(kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')

# Platform — actual image tag from the running pod (Helm values can drift from what was requested if --reuse-values was used)
PLATFORM_VERSION_ACTUAL=$(kubectl get pods -n "${NAMESPACE}" -l app.kubernetes.io/name=iap -o jsonpath='{.items[0].spec.containers[0].image}' 2>/dev/null | awk -F: '{print $NF}')

# MongoDB — actual server version (throwaway pod probe; works for Atlas/DocumentDB/on-prem alike)
MONGO_VERSION_ACTUAL=$(kubectl run mongo-version-check --rm -i --restart=Never --image=mongo:7 -n "${NAMESPACE}" \
    --command -- mongosh "${MONGO_URL}" --quiet --eval "db.version()" 2>/dev/null \
    || echo "unknown — probe pod could not reach ${MONGO_URL%%@*}@...")

# Redis — actual server version (throwaway pod probe)
REDIS_VERSION_ACTUAL=$(kubectl run redis-version-check --rm -i --restart=Never --image=redis:7 -n "${NAMESPACE}" \
    --command -- redis-cli -h "${REDIS_HOST}" -p "${REDIS_PORT:-6379}" INFO server 2>/dev/null \
    | grep "redis_version:" | cut -d: -f2 | tr -d '\r' \
    || echo "unknown — probe pod could not reach ${REDIS_HOST}")

# Gateway — actual image tag (if deployed)
if helm status iag5 -n "${NAMESPACE}" > /dev/null 2>&1; then
    GATEWAY_VERSION_ACTUAL=$(kubectl get pods -n "${NAMESPACE}" -l app.kubernetes.io/name=iag5 -o jsonpath='{.items[0].spec.containers[0].image}' 2>/dev/null | awk -F: '{print $NF}')
    GATEWAY_LABEL="IAG5"
elif helm status iag4 -n "${NAMESPACE}" > /dev/null 2>&1; then
    GATEWAY_VERSION_ACTUAL=$(kubectl get pods -n "${NAMESPACE}" -l app.kubernetes.io/name=iag4 -o jsonpath='{.items[0].spec.containers[0].image}' 2>/dev/null | awk -F: '{print $NF}')
    GATEWAY_LABEL="IAG4"
fi
```

Print the full build summary:

```
╔══════════════════════════════════════════════════════════════════════════╗
║  ENVIRONMENT BUILD SUMMARY — Kubernetes (EKS)                            ║
╠══════════════════════════════════════════════════════════════════════════╣
║  Cluster:        {EKS_CLUSTER_NAME}  ({EKS_CLUSTER_REGION})              ║
║  K8s version:    {K8S_VERSION_ACTUAL}                                    ║
║  Nodes:          {NODE_COUNT_ACTUAL} × {EKS_NODE_TYPE}                   ║
║  Namespace:      {NAMESPACE}                                             ║
║  Grade:          {K8S_CLUSTER_GRADE}                                     ║
╠═══════════════╦═════════════════════════╦════════════════════════════════╣
║ Component     ║ Version                 ║ Source / Access                ║
╠═══════════════╬═════════════════════════╬════════════════════════════════╣
║ Platform      ║ {PLATFORM_VERSION_ACTUAL} ║ Helm release "iap"            ║
║ MongoDB       ║ {MONGO_VERSION_ACTUAL}    ║ {Atlas|DocumentDB|on-prem}    ║
║ Redis         ║ {REDIS_VERSION_ACTUAL}    ║ {ElastiCache|existing}        ║
║ Gateway       ║ {GATEWAY_VERSION_ACTUAL or "not deployed"} ║ {GATEWAY_LABEL or "n/a"} ║
║ Adapter store ║ {K8S_ADAPTER_METHOD: pv (Ngi GB) | layered} ║            ║
╠═══════════════╩═════════════════════════╩════════════════════════════════╣
║ Access:       https://localhost:3443 (port-forward) {or K8S_HOSTNAME}    ║
║ Credentials:  admin@itential.com / [ITENTIAL_DEFAULT_USER_PASSWORD]      ║
║ Config saved: environments/eks/{EKS_CLUSTER_NAME}.yaml                   ║
╚══════════════════════════════════════════════════════════════════════════╝
```

**Clean up the throwaway probe pods if `--rm` didn't fully remove them** (rare, on probe timeout):
```bash
kubectl delete pod mongo-version-check redis-version-check -n "${NAMESPACE}" --ignore-not-found 2>/dev/null
```

### Step 5f.6 — Post-Build Learnings & Documentation Check

**Run this after every K8s build, successful or not.** Same mechanic as Step 4f (Docker paths) — catch drift between what this skill documents and what actually happened, before it becomes the next engineer's unguided surprise.

**Reflect on the session:**

1. Did any command across Steps 0–5f.5 fail in a way not already covered by an existing gotcha callout (e.g., the ones already documented for `pending-install` releases, the chart's real `storageClass`/flat `env:` schema, ALB provisioning failures, or the default-sample-adapter crash-loop), requiring an improvised fix?
2. Did EKS provisioning, DocumentDB/ElastiCache provisioning, or the Helm install take meaningfully longer/shorter than the Quick Reference estimates?
3. Did the engineer have to supply information or make a call this skill's prompts didn't anticipate — an unusual VPC/subnet layout, an IAM permission gap, an unexpected chart schema key?
4. Did `eksctl`, `helm`, `kubectl`, or the AWS CLI behave differently than documented — a flag renamed/removed, an addon that auto-installed something already covered manually, a different default region/AZ behavior?
5. Did the actual detected versions (Step 5f.5) reveal anything worth noting?

**Check whether each "yes" is already documented** (search both this file and the HTML guide before assuming something is new):

```bash
grep -n -i "{keyword from the learning}" .claude/skills/deploy-containers/SKILL.md
grep -n -i "{keyword}" docs/troubleshooting-agent-guide.html
```

**If every "yes" is already covered:** say so and stop.

**If genuinely new, draft the addition and present it** (same format as Step 4f):

```
══════════════════════════════════════════════════════════════
  POST-BUILD LEARNINGS — this session (Kubernetes)
══════════════════════════════════════════════════════════════
  {learning}: NEW — not currently documented
  {learning}: already documented at Step {N} — no action

  Proposed addition to .claude/skills/deploy-containers/SKILL.md:
  ─────────────────────────────────────────────────────────
  {exact drafted text}
  ─────────────────────────────────────────────────────────

Contribute this update? [yes → runs /contribute skill-fix deploy-containers
                          / edit / skip]
══════════════════════════════════════════════════════════════
```

**On "yes":** invoke `/contribute skill-fix deploy-containers "{one-line description}"` — same reasoning as Step 4f, route through the PR flow rather than editing directly.

### Step 5g — Ingress (optional — if K8S_HOSTNAME is set)

*Skip if reproducing without an external hostname. Port-forward is sufficient for most repro work.*

If `K8S_HOSTNAME` is set and `K8S_INGRESS_TYPE=alb`, check that the ALB was provisioned:

```bash
# Check ingress status
kubectl get ingress -n "${NAMESPACE}" -o wide
# The ADDRESS column shows the ALB DNS name — this may take 3-5 minutes to populate
```

If the ALB DNS address is empty after 5 minutes:
```bash
# Inspect the ingress controller events
kubectl describe ingress iap -n "${NAMESPACE}"
kubectl logs -n kube-system deployment/aws-load-balancer-controller --tail=30
```

Common ALB provisioning failures:
| Symptom | Cause | Fix |
|---------|-------|-----|
| `error creating load balancer: not authorized` | LBC IAM role missing permissions | Verify IRSA attachment; check `AWSLoadBalancerControllerIAMPolicy` |
| `certificate ARN invalid` | ACM_CERT_ARN wrong or in wrong region | Re-check ARN matches cluster region |
| `no targets available` | Pods not yet ready | Wait for Step 5f health to pass first |
| `InvalidSubnet` | LBC subnet tags missing | Tag subnets with `kubernetes.io/role/elb: 1` |

If using NGINX ingress:
```bash
kubectl get svc ingress-nginx-controller -n ingress-nginx -o wide
# Use the EXTERNAL-IP or load balancer DNS for DNS mapping
```

### Step 5h — Adapter Delivery Method (if adapter troubleshooting)

*Only needed when the reproduction involves adapter issues — otherwise skip.*

The Itential docs describe two adapter delivery methods on Kubernetes:

```
Adapter Delivery Method:

  1) Persistent Volumes (PV)  — Recommended for troubleshooting
     Adapters are installed into a shared volume mounted into platform pods.
     Install or update adapters without rebuilding the container image.
     Start with 10 GB per adapter PV. Volume is NOT shared between pods
     (each platform pod gets its own PVC in the StatefulSet).

  2) Layered Containers        — Recommended for production stability
     Adapters are baked into a custom Docker image layer on top of the
     platform image. Portable and self-contained. Requires a rebuild
     pipeline to add/update adapters.

Choose [1/2]:
```

**Option 1 — Persistent Volumes (for repro/troubleshooting):**

The `iap-helm` chart supports adapter PVCs via values. Add to `/tmp/iap-values.yaml` and re-run Helm upgrade:

```yaml
# Adapter PV configuration (add to /tmp/iap-values.yaml)
adapters:
  persistentVolume:
    enabled: true
    storageClassName: iap-ebs-gp3
    size: "${K8S_ADAPTER_PV_SIZE:-10Gi}"    # start with 10 GB per Itential docs
```

After re-deploying, install adapters directly into the PV:
```bash
# Exec into the platform pod to install adapter
IAP_POD=$(kubectl get pods -n "${NAMESPACE}" -l app.kubernetes.io/name=iap -o name | head -1)
kubectl exec -n "${NAMESPACE}" "${IAP_POD}" -- \
    npm install --prefix /adapters @itentialopensource/adapter-{name}@{version}
echo "✅ Adapter installed into PV — available to all pods sharing this StatefulSet PVC"
```

**Option 2 — Layered Containers:**

Build a custom image with the adapter baked in (requires Docker build environment):
```bash
cat > /tmp/Dockerfile.adapter <<EOF
FROM 497639811223.dkr.ecr.us-east-2.amazonaws.com/automation-platform-config-lcm:${IAP_VERSION}
RUN npm install @itentialopensource/adapter-{name}@{version}
EOF

# Build and push to ECR
docker build -t "${ECR_REGISTRY}/automation-platform-config-lcm:${IAP_VERSION}-with-{adapter}" \
    -f /tmp/Dockerfile.adapter .

docker push "${ECR_REGISTRY}/automation-platform-config-lcm:${IAP_VERSION}-with-{adapter}"
```

Then update the Helm release to use the custom image tag and re-apply. Show the command, require engineer approval before pushing to ECR.

---

## Step 6 — Cleanup

### EC2 instance cleanup (if provisioned in Step 2.vm)

If an EC2 instance was provisioned during this session (`INSTANCE_ID` is set):

```
Do you want to terminate the EC2 instance ({INSTANCE_ID} — {VM_PUBLIC_IP})?

  ⚠️  Termination is permanent — all instance storage is destroyed.
  Type 'yes terminate {INSTANCE_ID}' to confirm:
```

On confirmation:
```bash
aws ec2 terminate-instances \
    ${CRED_FLAGS} \
    --region "${REGION}" \
    --instance-ids "${INSTANCE_ID}"
echo "✅ Instance ${INSTANCE_ID} termination initiated."
```

If engineer wants to keep the instance: remind them it will continue incurring AWS costs. Print the instance ID and public IP for their records.

---

### Docker cleanup

```bash
echo "Cleanup options:"
echo "  1) Stop containers only (preserves data volumes) — make down"
echo "  2) Full teardown (DESTROYS MongoDB volumes — irreversible) — make clean"
echo "Choice [1/2]:"
```

On choice 1 (safe):
```bash
make down
echo "✅ Containers stopped. Data volumes preserved. Run 'make up' to restart."
```

On choice 2 (destructive — require explicit confirmation):
```bash
echo "⚠️  WARNING: make clean will permanently delete all MongoDB data."
echo "This cannot be undone. Type 'yes I want to delete all data' to confirm:"
# Read confirmation
if [ "${CONFIRM}" = "yes I want to delete all data" ]; then
    make clean
    echo "✅ Full teardown complete. All volumes removed."
else
    echo "Aborted. Run 'make down' to stop containers without deleting data."
fi
```

### Kubernetes cleanup

```bash
echo "Cleanup options:"
echo "  1) Uninstall Helm releases only (PVCs survive)"
echo "  2) Uninstall + delete namespace (ALL resources and PVCs deleted — irreversible)"
echo "Choice [1/2]:"
```

On choice 1:
```bash
helm uninstall iap -n "${NAMESPACE}" 2>/dev/null && echo "✅ iap uninstalled"
helm uninstall iag5 -n "${NAMESPACE}" 2>/dev/null && echo "✅ iag5 uninstalled"
helm uninstall iag4 -n "${NAMESPACE}" 2>/dev/null && echo "✅ iag4 uninstalled"
echo "Note: PVCs and secrets remain. Re-install with 'helm upgrade --install' to reuse them."
```

On choice 2 (destructive):
```bash
echo "⚠️  WARNING: deleting namespace '${NAMESPACE}' removes ALL resources, secrets, and PVCs."
echo "Type 'yes delete namespace ${NAMESPACE}' to confirm:"
# Read confirmation
if [ "${CONFIRM}" = "yes delete namespace ${NAMESPACE}" ]; then
    kubectl delete namespace "${NAMESPACE}"
    echo "✅ Namespace ${NAMESPACE} deleted."
else
    echo "Aborted."
fi
```

---

## Quick Reference

| Path | Steps | Time estimate |
|---|---|---|
| Docker local | 0 → 1 → 1e → 2 → 3 → **3d (confirm)** → 4 → **4e (summary)** → **4f (learnings)** | ~5 min |
| Docker on existing VM | 0 → 1 → 1e → 2 (existing) → 3 → **3d (confirm)** → 4 → **4e (summary)** → **4f (learnings)** | ~10 min |
| Docker on new EC2 | 0 → 1 → 1e → 2 (2.vm.1–2.vm.4 **[confirm w/ vCPU:RAM]**, new EC2) → 3 (Docker install) → 3 → **3d (confirm)** → 4 → **4e (summary)** → **4f (learnings)** | ~15–20 min |
| K8s — existing cluster | 0 → 1 → 1e → 2.k8s.0 → 2.k8s.2 → 2.k8s.3 → 2.k8s.4 → 5a → 5b → 5c → **5c.5 (confirm)** → 5d → 5e → 5f → **5f.5 (summary)** → **5f.6 (learnings)** | ~20–30 min |
| K8s — new EKS cluster (min) | 0 → 1 → 1e → 2.k8s.0 → 2.k8s.1 **[confirm w/ vCPU:RAM]** → 2.k8s.2 **[confirm if new]** → 2.k8s.3 **[confirm if new]** → 2.k8s.4 → 5a–5c → **5c.5 (confirm)** → 5d–5f → **5f.5 (summary)** → **5f.6 (learnings)** | ~35–45 min (15-20 for EKS) |
| K8s — new EKS cluster (prod) | same as above | ~40–50 min |
| K8s + ingress | …5f.6 → 5g | +5–10 min (ALB provisioning) |
| K8s + adapter PV | …5f.6 → 5h (option 1) | +5 min |
| Any path reusing an existing DB (Step 1e confirmed) | skips 2.k8s.2/2.k8s.3 or dev-stack's bundled Mongo/Redis | −5–10 min |

**Confirmation gates that show method + CPU/memory before creating anything billable:**

| Gate | Fires when | Shows |
|---|---|---|
| Step 2.vm.3 | Docker on new EC2 | Instance type, vCPU/RAM |
| Step 2.k8s.1 | New EKS cluster | Node type, per-node + total vCPU/RAM |
| Step 2.k8s.2 choice 4 | New DocumentDB | Instance class, vCPU/RAM, instance count |
| Step 2.k8s.3 choice 3 | New ElastiCache | Node type, vCPU/RAM |
| Step 3d | Any Docker path, before `make setup`/`make up` | Method, per-container limits (or "unconstrained — shares host pool") |
| Step 5c.5 | Any K8s path, before first Helm install | Full table — every component's method + CPU/RAM in one place |

**Post-build summaries that show what was actually built (versions, not requested tags):**

| Summary | Fires when | Shows |
|---|---|---|
| Step 4e | Any Docker path, after health check passes | Actual Platform/MongoDB/Redis/Gateway versions (queried live, not assumed from image tags) |
| Step 5f.5 | Any K8s path, after health check passes | Cluster name/version/node count, actual Platform/MongoDB/Redis/Gateway versions (queried via pod image tags and throwaway probe pods), adapter storage method, saved config path |

`/themis-aws-deploy` has the equivalent as Step 6c (`LOCAL-EXTENSIONS.md`) — a per-role host/version table sourced from `run-vars.yml` (`platform_release`/`gateway_release`) and the certify reports' actual MongoDB/Redis version lines, supplementing the vendor's own End-of-run summary.

**Post-build learnings check — the last step of every build, always:**

| Step | Fires when | Does |
|---|---|---|
| Step 4f | Any Docker path, after Step 4e | Reflects on the session for undocumented gotchas, timing drift, unanticipated decisions, or tooling surprises; checks this SKILL.md and the HTML guide before assuming anything is new; drafts a fix and offers `/contribute skill-fix deploy-containers` |
| Step 5f.6 | Any K8s path, after Step 5f.5 | Same mechanic, K8s-specific reflection questions (EKS/DocumentDB/ElastiCache/Helm surprises) |

`/themis-aws-deploy` has the equivalent as Step 6d (`LOCAL-EXTENSIONS.md`) — same reflection questions adapted for Ansible/`tofu`/AWS specifics, drafts as a proper `[OVERRIDE]`/`[INSERT AFTER Step N]` extension section (never a raw diff of the vendor `SKILL.md`), and routes through `/contribute skill-fix themis-aws-deploy`, which detects the vendor-sync status and automatically targets `LOCAL-EXTENSIONS.md` instead of `SKILL.md`.

**EKS node sizing (from docs.itential.com):**

| Grade | AWS instance | vCPU | RAM | Use case |
|-------|-------------|------|-----|----------|
| Minimum | `m5a.xlarge` | 4 | 16 GB | Dev / test / reproduction |
| Production | `c6a.4xlarge` | 16 | 32 GB | Customer-similar load testing |

| Image | ECR path |
|---|---|
| Platform | `497639811223.dkr.ecr.us-east-2.amazonaws.com/automation-platform-config-lcm:{tag}` |
| Gateway4 | `497639811223.dkr.ecr.us-east-2.amazonaws.com/automation-gateway:{tag}` |
| Gateway5 | `497639811223.dkr.ecr.us-east-2.amazonaws.com/automation-gateway5:{tag}` |

| Dev stack Make target | Effect |
|---|---|
| `make login` | ECR auth |
| `make setup` | Full first-time setup (key + certs + start + configure) |
| `make up` | Start services |
| `make down` | Stop services (preserve volumes) |
| `make clean` | ⚠️ Stop + remove volumes |
| `make logs` | Stream all service logs |
| `make status` | Show URLs, ports, health |
