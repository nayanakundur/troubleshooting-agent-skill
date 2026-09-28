# Docker Prerequisites

## Docker Local (macOS / Windows / Linux)

| Requirement | Minimum Version | Install |
|---|---|---|
| Docker Desktop | 4.x | https://docs.docker.com/desktop/ |
| docker compose (v2 plugin) | 2.x | Included with Docker Desktop 4.x |
| AWS CLI | 2.x | `brew install awscli` |
| git | Any | `brew install git` |
| Disk space | 20 GB free | — |

### macOS notes
- Docker Desktop must be running before the skill executes (check the whale icon in the menu bar)
- Resource allocation: Docker Desktop → Settings → Resources → set 4+ CPUs and 8+ GB RAM
- The dev stack uses host-mode networking by default; ports 3000, 3443, 27017, 6379 must be free

### Windows notes
- WSL2 backend is required: Docker Desktop → Settings → General → "Use WSL 2 based engine"
- Run the skill from a WSL2 terminal (Ubuntu) not from PowerShell/CMD for best compatibility
- Port binding: same ports as macOS; check with `netstat -ano | findstr :3443`

### Linux (direct Docker Engine, no Docker Desktop)
- Install Docker Engine + compose plugin: https://docs.docker.com/engine/install/
- Add your user to the `docker` group: `sudo usermod -aG docker $USER` (re-login required)
- Verify: `docker run hello-world`

---

## Docker on VM (SSH)

### Supported OS
- Rocky Linux 9 (recommended — matches Themis EC2 builds)
- Ubuntu 22.04 LTS
- RHEL 9 / AlmaLinux 9

### VM Resource Minimum

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 4 vCPU | 8 vCPU |
| RAM | 16 GB | 32 GB |
| Disk | 50 GB | 100 GB |

### Packages required on VM

**Rocky Linux / RHEL:**
```bash
sudo dnf install -y docker docker-compose-plugin awscli git
sudo systemctl enable --now docker
sudo usermod -aG docker $(whoami)
```

**Ubuntu:**
```bash
# Docker Engine
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $(whoami)
# AWS CLI
sudo snap install aws-cli --classic
# git
sudo apt-get install -y git
```

### SSH requirements
- The engineer's SSH key must be in `~/.ssh/` or the path set in `.env` as `SSH_KEY_PATH_1`
- Port 22 must be open inbound on the VM
- The SSH user must be in the `docker` group (or have sudo)

### .env variables for Docker VM

```
SSH_HOST_1=<vm-ip-or-hostname>
SSH_USER_1=<ssh-username>
SSH_KEY_PATH_1=</absolute/path/to/key.pem>
SSH_ROLE_1=iap
```

---

## ECR Authentication Requirements

ECR (`497639811223.dkr.ecr.us-east-2.amazonaws.com`) requires AWS credentials with:

| IAM Permission | Purpose |
|---|---|
| `ecr:GetAuthorizationToken` | `aws ecr get-login-password` |
| `ecr:BatchGetImage` | Pull image layers |
| `ecr:GetDownloadUrlForLayer` | Pull image layers |

These permissions are on the Itential-managed ECR account. AWS credentials (access key ID / secret / optional session token) are provided by the Itential Account Manager or PE team.

ECR tokens expire after **12 hours**. If a `docker pull` returns `unauthorized`, re-run Step 1d.

---

## Docker Compose Profile Reference

| STACK_PROFILE | Services started |
|---|---|
| `platform` | MongoDB, Redis, Platform (recommended for most repro) |
| `full` | MongoDB, Redis, Platform, Gateway4, Gateway5 |
| `deps` | MongoDB, Redis only |

Feature flags (add to `.env`):
```
GATEWAY4_ENABLED=true    # adds Gateway4 to platform profile
GATEWAY5_ENABLED=true    # adds Gateway5 to platform profile
MCP_ENABLED=true         # adds MCP server
OPENBAO_ENABLED=true     # adds OpenBao vault
LDAP_ENABLED=true        # adds OpenLDAP
```
