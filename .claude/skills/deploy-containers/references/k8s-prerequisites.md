# Kubernetes Prerequisites

## Two Supported Cluster Types: EKS or Local `kind`

`/deploy-containers k8s` supports both an AWS EKS cluster and a pre-existing local `kind` cluster
(`kubectl config current-context` showing `kind-<name>`) — the skill detects which one is active
and the rest of the flow (Helm installs, secrets, health probes) applies identically. Only image-pull
mechanics differ between the two (see the arm64 gotcha below).

**Running `/deploy-containers k8s` against EKS from macOS is fully supported** — all EKS worker
nodes run in AWS, so the Mac is only the operator machine and local RAM/CPU are not a bottleneck.
**Running against local `kind`** puts every workload on the Mac itself — size `kind`'s Docker
Desktop resource allocation accordingly (minimum grade: 4 vCPU / 16 GB, matching the docs.itential.com
EKS node minimum, since IAP's own resource needs don't change based on where the cluster runs).

> **Gotcha — Apple Silicon (arm64) `kind` nodes cannot pull amd64-only images the normal way.**
> Unlike `docker run`, which transparently emulates amd64 via QEMU, containerd inside a `kind` node
> performs strict CRI platform matching and will fail (or silently pick the wrong manifest) when
> pulling an amd64-only image — this affects the IAG5 image (`automation-gateway5:*-amd64`) in
> particular. **Fix:** pull the image on the host first (where Docker Desktop's emulation works),
> then side-load it directly into the `kind` node's containerd, bypassing the platform-matched
> registry pull:
> ```bash
> docker pull --platform linux/amd64 <image>:<tag>
> kind load docker-image <image>:<tag> --name <kind-cluster-name>
> ```
> This is specific to `kind` on Apple Silicon — EKS worker nodes (always the platform the image was
> built for) never hit this.

**Required tools (install once via Homebrew):**

```bash
brew install awscli          # AWS CLI v2 — ECR auth, EKS cluster ops, ElastiCache provisioning (EKS path only)
brew install eksctl           # eksctl ≥ 0.190 — cluster provisioning and IRSA role creation (EKS path only)
brew install kubectl          # kubectl — cluster management, Helm lifecycle (both paths)
brew install helm             # Helm 3.15+ — IAP/IAG chart installs (both paths)
brew install kind             # kind — only if building a local cluster instead of EKS
```

Docker Desktop or OrbStack must be running — for the EKS path just for `docker login` (ECR token
step); for the local `kind` path, Docker Desktop also runs every cluster node and workload, so its
configured resource allocation (Settings → Resources) is the effective ceiling for the whole cluster.

**AWS credentials:** configure via `aws configure sso` (recommended) or a static IAM key. The engineer selects the profile in Step 1 of the skill. Not needed for the local `kind` path beyond ECR pull auth.

---

## Cluster Requirements

| Requirement | Minimum | Notes |
|---|---|---|
| Kubernetes version | 1.31 | Check: `kubectl version` |
| Helm | 3.15.0 | Check: `helm version --short` |
| Node CPU | 4 cores per node | IAP StatefulSet (2 replicas) |
| Node RAM | 16 GB per node | |
| StorageClass | `iap-ebs-gp3` (EBS gp3) | Created by skill if absent; on `kind`, the provisioner differs — see the StorageClass section below |
| cert-manager | Any recent | **Required — not optional.** Must be installed cluster-wide, never as a per-chart subchart dependency (see gotcha below) |
| External MongoDB | Required | IAP Helm chart does NOT bundle MongoDB |
| External Redis | Required | IAP Helm chart does NOT bundle Redis |

> **Gotcha — install cert-manager cluster-wide, never as a per-chart subchart dependency.**
> Setting `certManager.enabled: true` inside the `iap`/`iag5` Helm values causes a chicken-and-egg
> problem: the chart's `--dry-run` (and often the real install) needs cert-manager's CRDs (`Issuer`,
> `ClusterIssuer`, `Certificate`) to already exist. Install cert-manager once, cluster-wide, before
> any Itential Helm install:
> ```bash
> kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
> kubectl wait --for=condition=Available deployment --all -n cert-manager --timeout=120s
> ```
> then set `certManager.enabled: false` in every chart's values file — the chart still creates its
> own `Issuer`/`Certificate` objects, it just doesn't try to install the controller itself.
>
> **Related gotcha — `ClusterIssuer` vs namespaced `Issuer` resolve their CA secret in different
> namespaces.** A `ClusterIssuer`'s `ca.secretName` is looked up in the **cert-manager controller's
> own namespace** (`cert-manager`), regardless of which namespace the issuer "belongs" to. A
> namespaced `Issuer`'s `ca.secretName` is looked up in its own namespace. The `iap` chart defaults
> `issuer.kind: ClusterIssuer`; the `iag5` chart defaults `issuer.kind: Issuer`. If both charts share
> one CA secret, that secret must exist in **both** the app namespace (for the `Issuer`) **and** the
> `cert-manager` namespace (for the `ClusterIssuer`) — copy it explicitly rather than assuming one
> secret is enough.

## Required Tools (Local Machine)

| Tool | Minimum Version | Install |
|---|---|---|
| kubectl | Matches cluster version ±1 | `brew install kubectl` |
| Helm | 3.15.0 | `brew install helm` |
| AWS CLI | 2.x | `brew install awscli` (for ECR token) |

## External Dependencies

Both MongoDB and Redis must be accessible from inside the cluster **before** installing the IAP Helm chart.

### MongoDB
- Version: 7.x or 8.x recommended (IAP 6.x)
- IAP needs a user with `readWrite` on the `itential` database
- Connection string format: `mongodb://<user>:<password>@<host>:27017/itential`
- For reproduction: a MongoDB Atlas free tier or a standalone container outside the cluster works fine

### Redis
- Version: 7.x recommended
- IAP needs AUTH password access if Redis is auth-enabled
- For reproduction: a single Redis container outside the cluster with a simple password is sufficient

## VPC / Networking Requirements

ElastiCache Redis and the EKS cluster **must be in the same VPC**. The skill handles this
automatically when provisioning from scratch, but when connecting to an existing cluster you need:

1. **ElastiCache subnet group** — must reference subnets from the EKS VPC.
   Look up the VPC and subnets of an existing cluster:
   ```bash
   aws eks describe-cluster --name <cluster> --region <region> \
       --query 'cluster.resourcesVpcConfig.{vpcId:vpcId,subnetIds:subnetIds}'
   ```

2. **Security group rule** — EKS node security group → ElastiCache port 6379.
   Get the node security group:
   ```bash
   aws eks describe-cluster --name <cluster> --region <region> \
       --query 'cluster.resourcesVpcConfig.clusterSecurityGroupId' --output text
   ```
   Then authorize ingress on port 6379 in the ElastiCache security group from that SG ID.

3. **Transit encryption** — ElastiCache clusters provisioned by the skill use TLS (`--transit-encryption-enabled`). The Redis client in IAP connects with TLS by default on Redis 7.x.

---

## IAM Roles Required

| Role | Who needs it | How the skill creates it |
|---|---|---|
| EKS node role with ECR read | All paths — nodes pull IAP images from ECR | Created automatically by `eksctl create cluster`; the skill adds ECR read inline if missing (see "Option B — IAM role on nodes" in ECR section) |
| LBC IRSA role (`AWSLoadBalancerControllerIAMPolicy`) | ALB ingress only | Step 2.k8s.1b provisions this automatically for new clusters via `eksctl create iamserviceaccount`; set `EKS_LBC_ROLE_ARN` in `.env` for existing clusters |

Port-forward access (`kubectl port-forward svc/iap 3443:3443 -n itential`) requires neither role.

---

## ECR Access from Cluster

The cluster needs to pull from `497639811223.dkr.ecr.us-east-2.amazonaws.com`.

**Option A — imagePullSecret (skill handles this):**
The skill creates a `ecr-pull-secret` of type `docker-registry` in the `itential` namespace using a short-lived ECR token. Tokens expire after 12 hours; re-run Step 5a to refresh.

**Option B — IAM role on nodes (preferred for EKS):**
If the cluster nodes have an IAM instance role with ECR read permissions, no pull secret is needed. Add this policy to the node role:
```json
{
  "Effect": "Allow",
  "Action": [
    "ecr:GetAuthorizationToken",
    "ecr:BatchGetImage",
    "ecr:GetDownloadUrlForLayer"
  ],
  "Resource": "*"
}
```
When using Option B, skip the `imagePullSecrets` Helm value and the `ecr-pull-secret` creation in Step 5a.

## StorageClass: iap-ebs-gp3

The IAP Helm chart requires this specific StorageClass name. The skill creates it automatically if absent.

For EKS, the EBS CSI driver must be installed:
```bash
# Check if EBS CSI driver is installed
kubectl get pods -n kube-system | grep ebs-csi

# If not installed (EKS add-on):
aws eks create-addon --cluster-name <cluster> --addon-name aws-ebs-csi-driver
```

For local `kind` clusters, use `provisioner: rancher.io/local-path` (kind's built-in default CSI) instead of `ebs.csi.aws.com` — the skill's default manifest targets EKS's EBS CSI driver and needs this one field changed for `kind`. For any other non-EKS cluster, change the `provisioner` to match your CSI driver.

## K8s Secrets Required

### itential-platform-secrets (mandatory)

| Key | Description |
|---|---|
| `ITENTIAL_DEFAULT_USER_PASSWORD` | Admin UI login password |
| `ITENTIAL_ENCRYPTION_KEY` | 64-char hex — generate with `openssl rand -hex 32` |
| `ITENTIAL_MONGO_PASSWORD` | MongoDB user password |
| `ITENTIAL_MONGO_URL` | Full MongoDB connection string |
| `ITENTIAL_REDIS_PASSWORD` | Redis AUTH password (empty string if no auth) |

### itential-ca (optional — TLS)
Contains `ca.crt` — the Certificate Authority used to sign IAP's TLS cert.

### itential-gateway-secrets (IAG5 only)

| Key | Description |
|---|---|
| `encryptionKey` | IAG5 encryption key — generate with `openssl rand -hex 32` |

### ecr-pull-secret (docker-registry type)
Created by the skill in Step 5a. Referenced by `imagePullSecrets` in each Helm chart.

## Helm Chart Repos

| Component | Repo URL | Chart name |
|---|---|---|
| IAP | `https://itential.github.io/iap-helm` | `iap/iap` |
| IAG5 | `https://itential.github.io/iag5-helm` | `iag5/iag5` |
| IAG4 | `https://itential.github.io/iag4-helm` | `iag4/iag4` |

> **Gotcha — check for a stale `pending-install` release before the first Helm install of a
> session.** `helm upgrade --install --atomic --timeout` only rolls back/uninstalls on failure if
> the controlling Helm client process is still alive to observe the timeout. If a prior session
> was killed (terminal closed, agent context reset) mid-install, the release is left stuck in
> `pending-install` indefinitely — a fresh `helm upgrade --install` against it errors or produces
> confusing partial-state behavior. Check first, especially on a cluster that's been used before:
> ```bash
> helm list -n itential --pending
> # If found: helm uninstall <release> -n itential   (safe — nothing succeeded)
> ```

> **Gotcha — never author a Helm values file from memory.** Helm silently drops unknown keys in
> `--values` files: it does not error, does not warn, and the release still installs "successfully"
> while the intended override has zero effect. Always confirm the chart's real schema first:
> ```bash
> helm show values iap/iap > /tmp/iap-default-values.yaml
> helm pull iap/iap --untar --untardir /tmp/iap-chart-inspect
> grep -rn '\.Values\.' /tmp/iap-chart-inspect/iap/templates/ | less
> ```
> The real `iap` chart schema uses `storageClass: {enabled, name}` (not `persistence.storageClassName`)
> and a flat `env:` map for Mongo/Redis connection settings (not nested `mongodb:`/`redis:` blocks).

## IAG4 Node Requirements

IAG4 requires dedicated nodes with a label and taint:

```bash
# Label
kubectl label node <node-name> itential.io/app=iag

# Taint (prevents other pods from landing on this node)
kubectl taint node <node-name> itential.io/role=iag:NoSchedule
```

IAG4 also creates two PVCs per instance:
- SQLite database volume
- Code/script volume

## IAG5 Modes

| Mode | When to use |
|---|---|
| Simple | Single-node, reproduction environments |
| Distributed | HA, multiple IAG5 replicas with shared etcd |

The IAG5 chart bundles an etcd subchart (bitnami 11.3.0), but it does not activate itself
correctly by default — bare `--set` flags for image/pull-secret alone are not sufficient. For
**Simple mode** (the reproduction default), explicitly set `etcd.enabled: false` in the values
file; only set it `true` when actually running Distributed mode with a provisioned etcd cluster.
The chart also needs `certManager.enabled: false` (cluster-wide cert-manager handles TLS — see the
cert-manager gotcha above) and a namespaced `Issuer` + `Certificate` block, since the `iag5` chart
defaults `issuer.kind: Issuer`, not `ClusterIssuer` like the `iap` chart.

## Accessing the Deployed Platform

For reproduction (no ingress required):
```bash
kubectl port-forward svc/iap 3443:3443 -n itential
# Then: https://localhost:3443
```

For persistent access, configure an ingress:
- AWS EKS: AWS ALB Ingress Controller (`kubernetes.io/ingress.class: alb`)
- Other: NGINX Ingress Controller

## Rollout Verification

```bash
# Check all pods
kubectl get pods -n itential

# IAP StatefulSet status (2/2 expected)
kubectl rollout status statefulset/iap -n itential --timeout=300s

# IAG5 deployment status
kubectl rollout status deployment/iag5 -n itential --timeout=300s

# Platform health (via port-forward)
curl -sk https://localhost:3443/health/platform | python3 -m json.tool
```
