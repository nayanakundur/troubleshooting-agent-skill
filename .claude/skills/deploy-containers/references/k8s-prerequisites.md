# Kubernetes Prerequisites

## Cluster Requirements

| Requirement | Minimum | Notes |
|---|---|---|
| Kubernetes version | 1.31 | Check: `kubectl version` |
| Helm | 3.15.0 | Check: `helm version --short` |
| Node CPU | 4 cores per node | IAP StatefulSet (2 replicas) |
| Node RAM | 16 GB per node | |
| StorageClass | `iap-ebs-gp3` (EBS gp3) | Created by skill if absent |
| cert-manager | Any recent | For TLS — optional but recommended |
| External MongoDB | Required | IAP Helm chart does NOT bundle MongoDB |
| External Redis | Required | IAP Helm chart does NOT bundle Redis |

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

For non-EKS clusters, change the `provisioner` in the StorageClass manifest to match your CSI driver.

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

The IAG5 chart bundles an etcd subchart (bitnami 11.3.0). No external etcd needed.

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
