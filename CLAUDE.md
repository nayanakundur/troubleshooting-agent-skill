# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

A Claude Code skill family for Itential Platform support engineering. There is no runnable application, no build system, and no test suite — the "code" is Claude Code skills (SKILL.md files) and supporting reference data. The primary deliverable is the `/troubleshoot` skill invoked directly in Claude Code sessions.

## Skill Architecture

One orchestrator skill delegates to a triage sub-skill (Phase 1) and six specialist diagnostic sub-skills (Phase 2). The orchestrator drives a **3-phase investigation lifecycle** (Phase 1: Ticket Understanding & Triage → Phase 2: Symptom Analysis & Routing → Phase 3: Reproduce & Workaround) followed by four closing phases (Phase 4: Diagnostic Report, Phase 5: Engineering Escalation, Phase 6: Resolution Learning, Phase 7: Manager Escalation). Sub-skills authenticate themselves from `.env` when invoked.

| Skill | Invoke | Covers |
|-------|--------|--------|
| **Orchestrator** | `/troubleshoot ISD-XXXX` | Full investigation lifecycle; delegates Phase 1 to triage sub-skill, Phase 2 to specialist sub-skills |
| **Triage** | `/troubleshoot-triage ISD-XXXX` | Phase 1 triage for ISD, IPSO, and ENG tickets — offline only. Resume check, `--list`, `--auto` mode, IPSO→ENG promotion |
| Adapters | `/troubleshoot-adapters` | Settings comparison, debug mode (auth_logging + console_level), cleanup; Phase 4 owns Kafka adapter OFFLINE / consumer lag; Phase 5 inspects OSS adapter source from GitLab (error.json, package.json, adapter code) — derived findings only, no code recorded |
| Workflows | `/troubleshoot-workflows` | Job error analysis, task failures, JST errors, import failures, childJob chains |
| Jobs | `/troubleshoot-jobs` | Stuck/errored jobs, slow-job baseline comparison, parent-child chain traversal |
| Databases | `/troubleshoot-databases` | MongoDB (slow queries, COLLSCAN, replica set), Redis (eviction, Bull queues), ElastiCache |
| Infrastructure | `/troubleshoot-infra` | CPU/memory/disk, containers (OOMKilled), EKS, SSH multi-host, network |
| Logs | `/troubleshoot-logs` | IAP/IAG/MongoDB/Redis/LB log collection and cross-component timestamp correlation |
| **Contribute** | `/contribute [ISD-XXXX \| scan \| version-note \| skill-fix \| known-bug ENG-XXXX]` | Reads closed investigation artifacts, generates formatted resolution entries / version notes / skill fixes, presents for engineer approval, and opens a GitHub PR — full git branch → write → diff → commit → push → PR flow |
| **Deploy Containers** | `/deploy-containers [docker-local \| docker-vm \| k8s]` | Provisions a containerized Itential Platform reproduction environment. Handles ECR auth (CRED_MODE pattern), Docker Compose dev stack setup, or Kubernetes Helm chart deployment. Wired into `/troubleshoot` Phase 3 via Step 3b.0 deployment type selection |
| **Sync Vendor Skills** | `/sync-vendor-skills [<skill-name> \| --all \| --report-only]` | Post-sync review and repair of LOCAL-EXTENSIONS.md files. Reads the validation report from `validate-extensions.py`, diffs old vs new vendor step content, drafts label fixes and content merges, and applies changes with engineer approval |
| **OSS Tools** | `/troubleshoot-oss [<tool> \| --list]` | Diagnoses ISD/IPSO tickets about open-source Itential tools from `github.com/itential` — Ansible deployer, Helm charts (iap-helm / iag5-helm / iag4-helm), job-archiver, IPCTL, itential-MCP, itential-dev-stack. Uses the GitHub public API (no auth required) for release correlation, CHANGELOG diffing, issue matching, and config requirement extraction. Wired into `/troubleshoot` Phase 2 routing when `Component: OSS-Tool` is detected by triage |

Skills live in `.claude/skills/<skill-name>/SKILL.md`. Each SKILL.md is self-contained — it includes all curl commands, phase-by-phase instructions, gotchas, and cleanup steps.

## Investigation Data Layout

Each investigation produces per-ticket artifacts under `data/`. Multiple investigations (ISD, IPSO, ENG) can coexist simultaneously — each in its own timestamped folder:

```
data/
├── <ISO-timestamp>/
│   └── <TICKET-KEY>/           — ISD-XXXX, IPSO-XXXX, or ENG-XXXX
│       ├── ticket_context.md       — Jira ticket snapshot and symptom summary; flags: outage_flag, service_request_flag, feature_request_flag, problem_ticket_key
│       ├── pre-investigation-summary.md — Triage output (Phase 1 / troubleshoot-triage)
│       ├── known_issues.md         — Matched past cases and ENG bugs
│       ├── confluence_references.md — KB articles and runbooks found
│       ├── docs_references.md      — Live docs.itential.com excerpts (Step 1e-docs, triage)
│       ├── diagnostic_report.md   — Findings, evidence, recommended actions (Phase 4)
│       ├── outage_summary_report.md — Customer/management-facing outage report (Phase 4, outage tickets only)
│       └── eng_ticket_draft.md    — ENG ticket draft saved if engineer declines immediate filing (IPSO only)
├── known-resolutions.md            — Accumulated resolution patterns (append-only)
├── product-capability-reference.md — Platform version history, open bugs, components, version behavioral notes
└── ISD-Triage-Skill-Executive-Brief.md — Business case and case studies
```

The `/troubleshoot-triage` sub-skill checks for an existing folder before creating a new one — if a prior triage exists for the same ticket key, it prompts the engineer to resume or start fresh. Use `/troubleshoot-triage --list` to see all in-flight investigations.

## Credentials and Auth

Each investigation uses a `.env` file (gitignored) in the repo root or working directory:

```
PLATFORM_URL=https://customer.itential.io
AUTH_METHOD=oauth          # oauth | basic
CLIENT_ID=...
CLIENT_SECRET=...
MONGO_URL=mongodb://...    # optional — for direct DB diagnostics
REDIS_HOST=...             # optional
SSH_HOST_1=...             # optional — multi-host SSH pattern
SSH_USER_1=...
SSH_KEY_PATH_1=...
SSH_ROLE_1=...             # label: iap | mongo | redis | gateway
JIRA_API_TOKEN=...         # for Jira MCP write operations
GITLAB_TOKEN=...           # GitLab Deploy Token — pull platform-claude-skills (read_repository)
JFROG_TOKEN=...            # JFrog Identity Token — pull platform RPMs from itential.jfrog.io
                           #   Generate: itential.jfrog.io → User Profile → Generate Identity Token
                           #   Used by: scripts/pull-platform-rpms.sh, deployer-inventory skill,
                           #   /themis-aws-deploy (auto-populates repository_password + gateway .whl pull)
AWS_REGION=                # AWS region (default: us-east-1 from Themis terraform.tfvars)
                           #   /themis-aws-deploy writes this to auto-account.tfvars
AWS_KEY_NAME=              # EC2 key pair name (non-pe-team-sbx accounts only)
                           #   NOTE: if aws_profile uses static STS creds (aws_access_key_id/
                           #   aws_secret_access_key/aws_session_token, not SSO-backed), those
                           #   sessions expire and need periodic re-provisioning — run
                           #   `aws sts get-caller-identity --profile <profile>` before a build
                           #   to check freshness (ExpiredToken fails fast at tofu plan/apply)
AWS_SECURITY_GROUP_IDS=    # comma-separated SG IDs; must allow SSH (22) inbound (non-pe-team-sbx only)
                           #   Re-verify these IDs still exist before every run, not just the
                           #   first — SGs can be deleted/rotated between sessions and only
                           #   surface as InvalidGroup.NotFound at `tofu plan`
AWS_SUBNET_IDS=            # comma-separated subnet IDs — maps to public-1a/b/c aliases in subnet_map
                           #   Re-verify these IDs still exist before every run, same reason as above
AWS_DEFAULT_SUBNET=        # which public-1x alias to use as default_subnet (optional, default: public-1a)
AWS_INSTANCE_TYPE_PLATFORM=  # instance type for platform nodes (e.g. t3.large); default: t3.medium
AWS_INSTANCE_TYPE_REDIS=     # instance type for redis nodes; default: t3.medium
AWS_INSTANCE_TYPE_MONGODB=   # instance type for mongo nodes; default: t3.medium
AWS_INSTANCE_TYPE_GATEWAY=   # instance type for gateway nodes; default: t3.medium
DEPLOY_GATEWAY_TFVARS=    # true = apply *-with-gateway.tfvars overrides (legacy Themis only)
                          #   Current Themis main already includes gateway VMs in base tfvars.
# ── /deploy-containers (Docker / Kubernetes reproduction) ──────────────────
ECR_REGISTRY=497639811223.dkr.ecr.us-east-2.amazonaws.com   # Itential ECR account (us-east-2)
DEVSTACK_DIR=              # override default ~/itential-dev-stack clone location

# Kubernetes (EKS) cluster settings
K8S_NAMESPACE=itential     # target namespace for Helm deployments
K8S_CONTEXT=               # kubectl context (blank = current-context)
K8S_CLUSTER_GRADE=         # minimum | production (drives node type + replica count)
EKS_CLUSTER_NAME=          # EKS cluster name (existing or to be provisioned)
EKS_CLUSTER_REGION=us-east-2
EKS_NODE_TYPE=             # m5a.xlarge (minimum) | c6a.4xlarge (production)
EKS_NODE_COUNT=            # desired node count (2 minimum, 3 production)
EKS_K8S_VERSION=1.31       # Kubernetes version (1.31+)
EKS_LBC_ROLE_ARN=          # IAM role ARN for AWS Load Balancer Controller (IRSA)

# External MongoDB (required — Helm charts do NOT include MongoDB)
MONGO_URL=                 # full connection string (mongodb+srv://... or mongodb://host:port/db)
ITENTIAL_MONGO_PASSWORD=   # MongoDB auth password

# External Redis (required — Helm charts do NOT include Redis)
REDIS_HOST=                # Redis endpoint (ElastiCache or custom)
REDIS_PORT=6379
ITENTIAL_REDIS_PASSWORD=   # Redis AUTH token (blank if Redis has no auth)

# IAP application secrets (K8s)
ITENTIAL_ENCRYPTION_KEY=   # 64-char hex; auto-generated if blank (openssl rand -hex 32)
ITENTIAL_DEFAULT_USER_PASSWORD=   # IAP admin password (set in K8s secret)

# TLS
TLS_CA_CERT_PATH=          # path to CA cert file for itential-ca K8s secret

# Ingress (optional — skip for port-forward-only repro)
K8S_INGRESS_TYPE=alb       # alb (AWS LBC) | nginx
K8S_HOSTNAME=              # FQDN for IAP ingress (e.g. iap.example.com)
K8S_INGRESS_SCHEME=internet-facing   # internet-facing | internal
ACM_CERT_ARN=              # ACM certificate ARN for ALB TLS termination

# Adapter delivery (K8s)
K8S_ADAPTER_METHOD=pv      # pv (persistent volumes) | layered (baked-in image)
K8S_ADAPTER_PV_SIZE=10Gi   # PV size per adapter (start 10 GB per Itential docs)
```

Auth tokens are cached in `.auth.json` (gitignored). The orchestrator reuses a token if it is less than 50 minutes old and `platform_url` matches; otherwise it re-authenticates silently.

## Safety Rules (Non-Negotiable)

These apply in every troubleshooting session:

- **Read-only platform API** — no PUT/POST/DELETE/PATCH without explicit engineer approval
- **Read-only MongoDB** — no writes, no `db.dropCollection()`, no index creation without approval
- **Read-only Redis** — no SET, DEL, FLUSHDB
- **Jira comments** — present the draft and wait for explicit approval before posting; all ISD comments must be internal (`commentVisibility: {"type": "role", "value": "Service Desk Team"}`)
- **ENG tickets** — present the bug report draft and wait for approval before filing
- **Adapter debug mode** — always disable `auth_logging` and reset `console_level` to `error` before ending a session; debug mode exposes credentials in logs
- **Adapter PUT** — does not support partial updates; always GET the current settings, modify in-place, then PUT the full body

## Vendor Sync

Two external skill libraries are vendored into this repo. Neither is a live dependency —
syncs are on-demand, pull only specific paths, and always leave the commit to you.

### builder-skills (GitHub — public)

Pulls two things from `https://github.com/itential/builder-skills`:

1. **Helper JSON bundles** → `vendor/builder-skills/` (importable platform asset bundles, create/update/operation templates)
2. **Builder skill files** → `.claude/skills/` (SKILL.md files for `/builder-agent`, `/qa-agent`, `/itential-lcm`, `/explore`, etc. — loadable by the Skill tool during Phase 3)

**No authentication required** — the GitHub repo is public.

**Staleness is checked automatically in two places:**
- At the start of the Constructive Fix Path (before any builder-skill template import) — the orchestrator runs `--check` and prompts the engineer to sync if behind
- At the end of every Claude Code session — the `Stop` hook prints a staleness report if the vendor copy is out of date

```bash
scripts/sync-builder-skills.sh --check [branch]   # fast staleness check
scripts/sync-builder-skills.sh [branch]            # full sync
```

After running: review `git diff vendor/builder-skills/ .claude/skills/` and `vendor/builder-skills/SYNC_CHANGELOG.md`, then commit deliberately.

### platform-skills (GitLab — private)

Pulls operational platform skills from `https://gitlab.com/itential/platform-engineering/platform-claude-skills`:

- **Skill files + companion scripts/docs** → `.claude/skills/` (SKILL.md files for `/mongodb`, `/redis`, `/prometheus`, `/itential-platform`, `/itential-gateway`, `/themis-aws-deploy`, etc.)
- Companion files (shell scripts, reference docs, tfvars) land **adjacent to their SKILL.md** — no separate vendor helpers directory

**Authentication required:** the GitLab repo is private. Add a Deploy Token to `.env`:

```
GITLAB_TOKEN=<deploy-token-value>
```

To create a Deploy Token: GitLab → `platform-claude-skills` → Settings → Repository →
Deploy tokens (name: `troubleshooting-agent`, scope: `read_repository`). Share via
1Password or Vault — this token is read-only and scoped to this repo only.

**Staleness is checked automatically at every session end** — the `Stop` hook
(`check-platform-skills-staleness.py`) compares the vendored SHA to upstream.
Within a session, the orchestrator's Phase 2b Platform Skills Staleness Gate runs
`--check` before invoking any platform skill for the first time.

```bash
scripts/sync-platform-skills.sh --check [branch]   # fast staleness check (requires GITLAB_TOKEN in .env)
scripts/sync-platform-skills.sh [branch]            # full sync
```

After running: review `git diff .claude/skills/` and `vendor/platform-skills/SYNC_CHANGELOG.md`, then commit deliberately.

**Troubleshooting-relevant skills** (wired into diagnostic routing):

| Skill | Domain | Wired into |
|-------|---------|------------|
| `/mongodb` | Full MongoDB replica set life report (scored HEALTHY/DEGRADED/CRITICAL) | `troubleshoot-databases` Step 1k |
| `/redis` | Full Redis Sentinel life report (scored HEALTHY/DEGRADED/CRITICAL) | `troubleshoot-databases` Step 2i |
| `/prometheus` | PromQL metrics analysis: CPU, heap, task rate, alerts, TSDB | `troubleshoot-infra` Phase 3x |
| `/itential-platform` | IAP admin: adapters, applications, job workers (29 tasks) | `troubleshoot` Phase 2b routing |
| `/itential-gateway` | IAG admin: health, logs, etcd cluster | `troubleshoot` Phase 2b routing |

Other skills in the library (`/deployer-inventory`, `/perf-test-analysis`, `/perflab`,
`/themis-aws-deploy`) are synced and available but not wired into troubleshooting routing.

> **`/themis-aws-deploy` local extensions:** When executing this skill, Claude must also
> read `.claude/skills/themis-aws-deploy/LOCAL-EXTENSIONS.md` alongside the vendor SKILL.md.
> It contains `.env`-based account overrides (AWS region, instance types, SG/subnet IDs),
> corrected architecture facts (gateway now in base tfvars, flat ASA naming), and extended
> pre-flight steps. `[OVERRIDE]` sections replace the corresponding vendor instruction;
> `[INSERT AFTER Step N]` sections add to it. This file is not vendored and survives syncs.

> **`/themis-aws-deploy` first-time setup:** `run-vars.yml` is gitignored (per-engineer, contains
> absolute local paths). On first use, copy the committed template and fill in your values:
> `cp .claude/skills/themis-aws-deploy/run-vars.yml.example .claude/skills/themis-aws-deploy/run-vars.yml`
> Leave `repository_api_key` blank — it is auto-populated from `JFROG_TOKEN` in `.env` (Step 1a).

> **`/deploy-containers`:** A standalone skill for provisioning Docker and Kubernetes reproduction
> environments. It is wired into `/troubleshoot` Phase 3 via Step 3b.0 (deployment type selection).
> ECR auth uses the same CRED_MODE pattern as `themis-aws-deploy`. Reference docs are in
> `.claude/skills/deploy-containers/references/` (docker-prerequisites.md, k8s-prerequisites.md).
> The `run-vars.yml.example` template covers K8s namespace, Helm chart versions, and dev stack path.

### Vendor Skill Extension Policy

**Rule: never modify any file listed in `vendor/platform-skills/SYNC_MANIFEST.json` or any file under `vendor/builder-skills/`.** A future `sync-platform-skills.sh` or `sync-builder-skills.sh` run silently overwrites those files. Local changes are lost without warning.

**How to extend a vendor skill without touching it:**

1. **Create `LOCAL-EXTENSIONS.md` adjacent to the vendor `SKILL.md`** — e.g. `.claude/skills/<skill-name>/LOCAL-EXTENSIONS.md`. This file must NOT be listed in `SYNC_MANIFEST.json`; confirm with `grep "<skill-name>/LOCAL-EXTENSIONS" vendor/platform-skills/SYNC_MANIFEST.json` (should return empty).

2. **Use two section labels:**
   - `[OVERRIDE] <Section Name>` — replaces the vendor instruction entirely. Claude uses this instead of the vendor text.
   - `[INSERT AFTER Step N]` or `[INSERT AFTER Step Na]` — adds a step at the named position; vendor steps around it run unchanged.

3. **Add a CLAUDE.md pointer** in the platform-skills section of this file so Claude always loads the extension alongside the vendor SKILL.md:
   ```
   > **`/<skill-name>` local extensions:** When executing this skill, Claude must also
   > read `.claude/skills/<skill-name>/LOCAL-EXTENSIONS.md` alongside the vendor SKILL.md.
   > `[OVERRIDE]` sections replace the vendor instruction; `[INSERT AFTER Step N]` adds steps.
   > This file is not vendored and survives syncs.
   ```

4. **Do not put AWS, environment, or account config inside `LOCAL-EXTENSIONS.md` in plaintext.** Runtime secrets stay in `.env` (gitignored); `LOCAL-EXTENSIONS.md` holds structural instructions only (command templates with `<placeholder>` tokens).

**Canonical example:** `.claude/skills/themis-aws-deploy/LOCAL-EXTENSIONS.md` — the template for every future vendor skill extension.

**Post-sync validation:** `scripts/sync-platform-skills.sh` automatically runs `scripts/validate-extensions.py` after each sync. It detects broken anchors (CRITICAL), modified override targets (MODIFIED), and new uncovered vendor steps (NEW), and writes a report to `.claude/skills/<name>/sync-validation-latest.md`. Run `/sync-vendor-skills` in Claude Code to review issues and apply LOCAL-EXTENSIONS.md fixes interactively.

### JFrog RPM Repository (itential.jfrog.io)

Platform RPMs for on-prem / VM deployments are hosted on JFrog. Use
`scripts/pull-platform-rpms.sh` to download them for local reproduction environments
or to feed into the Ansible deployer (`platform_packages` in `run-vars.yml`).

**Authentication:** per-engineer JFrog Identity Token (not shared). Generate at
`itential.jfrog.io → User Profile → Generate Identity Token`. Add to `.env` as `JFROG_TOKEN`.

**Version routing (automatic — detected from version string):**

| JFrog Repo | Component | Version scope |
|---|---|---|
| `itential-config-service-files` | Platform (legacy, all components) | 23.2.x / 2023.x and below |
| `PLATFORM` | Platform core RPM | 6.x+ (P6) |
| `FLOWAI` | FlowAI app | 6.x+ (P6) |
| `CONFIG` | Configuration Manager | 6.x+ (P6) |
| `GATEWAY-MANAGER` | Gateway Manager RPM | All versions |
| `INVENTORY-MANAGER` | Inventory Manager | All versions |
| `SERVICE` | Service Manager app | All versions |
| `automation-gateway` | IAG4 Python wheel (.whl) | All IAG4 versions |
| `gateway5` | IAG5 server RPM + client tarball | All IAG5 versions |

**URL structure quirk — `GATEWAY-MANAGER` doubles its own repo name in the path.**
Every other P6 repo above uses `<REPO>/<Product Name>/<Product Version>/<file>.rpm`
(e.g. `PLATFORM/Platform%206/Platform%206.5.2/itential-platform-6.5.2-1.noarch.rpm`),
but `GATEWAY-MANAGER` requires the repo segment twice —
`GATEWAY-MANAGER/GATEWAY-MANAGER/<file>.rpm` (e.g.
`https://itential.jfrog.io/artifactory/GATEWAY-MANAGER/GATEWAY-MANAGER/itential-gateway_manager-1.0.4.noarch.rpm`).
Using the single-segment pattern here 404s. See
`.claude/skills/themis-aws-deploy/LOCAL-EXTENSIONS.md` for the full worked example used
by `run-vars.yml`'s `platform_packages` list.

```bash
scripts/pull-platform-rpms.sh --check                          # verify token
scripts/pull-platform-rpms.sh --version 6.4.0 --list          # browse without downloading
scripts/pull-platform-rpms.sh --version 6.4.0                 # download all P6 components
scripts/pull-platform-rpms.sh --version 6.4.0 --components platform,config,gateway-manager
scripts/pull-platform-rpms.sh --version 23.2.1                # auto-routes to legacy repo
scripts/pull-platform-rpms.sh --version 4.4.46 --components iag4 --list   # browse IAG4 packages
scripts/pull-platform-rpms.sh --version 1.2.0  --components iag5 --list   # browse IAG5 packages
```

RPMs download to `repro/rpms/{VERSION}/` by default (override with `--out-dir`).
A `JFROG_MANIFEST.json` is written alongside the RPMs with filenames, sha256 checksums,
and download timestamp.

**When to use RPMs vs Docker image:**
- **Docker image** (`registry.itential.com/itential-platform:{version}`) — fast local
  repro for workflow/adapter/UI issues; no OS-level reproduction needed
- **RPMs** — when the issue requires a full OS-level install (init scripts, systemd,
  file permissions, upgrade path), or when reproducing on a VM that matches the
  customer's bare-metal topology via the Ansible deployer (`/themis-aws-deploy` skill)

## Spec → Skill Relationship

`troubleshooting-specs/spec-troubleshoot-<name>.md` files are design documents that preceded each SKILL.md. The SKILL.md is the authoritative implementation; the spec is historical context for why a phase exists or how a decision was made. When there is a conflict, the SKILL.md wins.

## HTML and PPTX Deliverables

All HTML deliverables use the Itential brand template at `~/.claude/templates/itential-html-template.html`. After editing the template (with `__ICON_B64__` and `__FULL_B64__` tokens intact), render it with:

```bash
python3 ~/.claude/templates/render-itential-html.py <template.html> docs/<output.html>
```

Rendered files go in `docs/`. The render script is stdlib-only (no venv needed).
