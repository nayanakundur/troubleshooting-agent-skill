# Kubernetes Prerequisites

## macOS as Control Plane

Running `/deploy-containers k8s` from macOS is fully supported. All EKS worker nodes run in AWS —
the Mac is only the operator machine, so local RAM and CPU are not a bottleneck.

**Required tools (install once via Homebrew):**

```bash
brew install awscli          # AWS CLI v2 — ECR auth, EKS cluster ops, ElastiCache provisioning
brew install eksctl           # eksctl ≥ 0.190 — cluster provisioning and IRSA role creation
brew install kubectl          # kubectl — cluster management, Helm lifecycle
brew install helm             # Helm 3.15+ — IAP/IAG chart installs
```

Docker Desktop or OrbStack must be running for `docker login` (ECR token step). It does not run any Itential workloads locally.

**AWS credentials:** configure via `aws configure sso` (recommended) or a static IAM key. The engineer selects the profile in Step 1 of the skill.

---

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
