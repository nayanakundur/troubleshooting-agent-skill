# Troubleshoot Skill — User Guide

The `/troubleshoot` skill is an AI-powered, full-stack support engineer built into Claude Code for the Itential Platform. It replaces the "stare at logs and guess" approach with a structured, evidence-driven investigation that goes from symptom to root cause to recommended action — all in one session.

---

## Quick Start

### Step 1 — Install Claude Code

Claude Code is the CLI that runs skills. Install it with npm:

```bash
npm install -g @anthropic-ai/claude-code
```

Requires **Node.js 18+**. Verify:

```bash
node --version   # must be 18.0.0 or higher
claude --version
```

### Step 2 — Log in to Claude

```bash
claude login
```

This opens your browser to authenticate with your Anthropic account. You will need:
- An [Anthropic Console](https://console.anthropic.com) account, **or**
- An enterprise SSO login if your organization uses Claude through Itential's license

Once authenticated, your session is stored locally — you stay logged in across terminal sessions.

> **Itential team members:** use your `@itential.com` Google account via SSO on the Anthropic Console login page.

### Step 3 — Clone the Skills Repo

```bash
git clone https://github.com/itentialmo15/troubleshooting-agent-skill.git
cd troubleshooting-agent-skill
```

> The skills live in `.claude/skills/` — Claude Code discovers them automatically when you run `claude` from inside this directory.

### Step 4 — Create Your `.env`

```bash
cp .env.example .env   # if provided, or create from scratch
```

Minimum required fields to start (see [Prerequisites](#prerequisites) for the full list):

```bash
PLATFORM_URL=https://your-iap-instance.itential.io
AUTH_METHOD=oauth          # "oauth" or "password"
CLIENT_ID=your-client-id
CLIENT_SECRET=your-client-secret
JIRA_URL=https://itential.atlassian.net
JIRA_USER=you@itential.com
JIRA_API_TOKEN=your-atlassian-api-token
```

### Step 5 — Run the Skill

```bash
claude
/troubleshoot ISD-XXXX
```

Replace `ISD-XXXX` with your actual Jira ticket key. The agent reads your `.env` and begins Phase 1 (offline triage) immediately.

---

## What It Is

A skill that acts as a senior Itential support engineer. It knows every IAP API endpoint, understands the full platform stack (IAP → IAG → MongoDB → Redis → Kafka → OS), and runs the same diagnostic playbook a seasoned engineer would run — but faster, reproducibly, and without forgetting a step.

It does not guess. It collects evidence, cross-references it, and tells you exactly what it found and why it matters.

---

## When to Use It

Use `/troubleshoot` any time something is **broken or degraded**:

| Symptom | Example |
|---------|---------|
| Workflow or job failing | Job stuck in `error` state, task output shows API error |
| Workflow import failing | `POST /workflow_builder/import` returns 500 or "Missing Params" |
| Adapter OFFLINE or DEAD | Health endpoint shows adapter `RUNNING/OFFLINE` |
| IAG service failing | GatewayManager task errors, service not found, 404 from IAG |
| Platform slow | Jobs taking 10× longer than usual; UI response times degraded |
| Jobs stuck / queued | Queue depth growing, jobs never completing |
| Container crash-looping | Docker container restarting repeatedly, OOMKilled |
| MongoDB issues | Slow queries, connection saturation, replica set unhealthy |
| Redis issues | Memory full, eviction happening, Bull queue stalled |
| Kafka lag | Consumer falling behind, adapter OFFLINE, topic missing |
| HTTP errors | UI returning 502/504, API returning unexpected status codes |
| Known bug? | Want to know if a bug is filed in ENG or ISD, and if there's a fix |

---

## Environments Covered

| Environment Type | Support Level |
|-----------------|---------------|
| **Docker Compose** (local dev, PE lab) | Full — direct container access, `docker logs`, `docker stats`, `docker exec` |
| **Bare-metal / VM** | Full — via SSH (`SSH_HOST` + `SSH_USER` + `SSH_KEY_PATH` in `.env`) |
| **Kubernetes / EKS** | Full — `kubectl` log collection via `KUBE_NAMESPACE` + `KUBE_POD_PATTERN` |
| **AWS EKS with CloudWatch** | Full — CloudWatch Logs Insights via `AWS_*` credentials |
| **Cloud IAP (p6.pe, PE)** | API-level only — no direct container access unless SSH/K8s creds provided |

---

## What It Troubleshoots

### Platform Components

| Component | What It Checks |
|-----------|---------------|
| **IAP Core** | App health, adapter health, job errors, workflow validation, JST scripts |
| **Adapters** | Settings (stub mode, auth method, host, SSL), live log capture, misconfig vs sampleProperties |
| **IAG (v4 and v5)** | Connectivity, token refresh (`token_timeout`), service list, job history, IAG-side logs |
| **MongoDB** | Connectivity, replica set status, slow ops, collection sizes, index health, slow query log |
| **Redis** | Memory, eviction policy, Bull/BullMQ queue depths, replication, slowlog |
| **Kafka** | Broker connectivity, topic/partition health, consumer group lag, adapter settings |
| **OS / Containers** | CPU, memory, OOMKill, disk usage, file descriptor limits, container restart counts |
| **HTTP / UI** | Response time baselines, slow endpoint analysis, TLS check, concurrent load |
| **Prometheus / Grafana** | Process CPU, heap, event loop, GC pause, HTTP latency histograms |
| **Logs** | webserver.log (HTTP access), mongod.log (slow queries), Redis log, Load Balancer access log |
| **Jira ENG/ISD** | Known bugs, fix versions, workarounds, resolution paths |

### Issue Categories

**Functional** — things that should work but don't:
- Workflow/job failing or erroring
- Template or workflow import failure
- JST (JavaScript Transformation) syntax or runtime error
- Adapter not connecting or returning wrong data
- IAG adapter OFFLINE or token expired
- Container crash-looping or failing to start

**Performance / Non-functional** — things that are slow or stuck:
- Jobs running slower than expected or never completing
- High memory/CPU on IAP or IAG
- MongoDB query latency or aggregation timeouts
- Redis key eviction or queue backlog
- Platform startup slow, apps timing out at boot
- Kafka consumer lag growing

---

## Prerequisites

### Software Requirements

| Requirement | Version | Notes |
|---|---|---|
| **Node.js** | 18.0.0+ | Check with `node --version` |
| **Claude Code** | Latest | Install: `npm install -g @anthropic-ai/claude-code` |
| **Anthropic account** | Any plan | Console account or enterprise SSO |
| **Git** | Any | To clone the repo |
| **curl** | Any | Pre-installed on macOS/Linux; used for platform API calls |
| **jq** | Any | `brew install jq` (macOS) or `apt install jq` (Linux) |

> **No Docker required** to run investigations against live environments. Docker is only needed for Phase 3 local reproduction environments.

### Required `.env` Variables (always)

Add to `{troubleshooting-agent-skill}/.env`:

```bash
PLATFORM_URL=https://your-iap-instance:3443
AUTH_METHOD=password          # or "oauth"
USERNAME=admin@pronghorn      # password auth
PASSWORD=admin
# OAuth instead:
# CLIENT_ID=your-client-id
# CLIENT_SECRET=your-client-secret
```

### Optional (unlocks deeper investigation)

Add whichever apply to your environment:

```bash
# ── MongoDB deep diagnostics ───────────────────────────────────
MONGO_URL=mongodb://user:pass@host:27017/itential?authSource=admin
# Replica set (prefer secondary reads):
# MONGO_URL=mongodb://u:p@h1:27017,h2:27017/itential?replicaSet=rs0&readPreference=secondary

# ── Redis deep diagnostics ─────────────────────────────────────
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=

# ── SSH Targets — one numbered block per host ─────────────────
# role: iap | mongodb | redis | iag | kafka | lb | generic
# label: short name used in report filenames (no spaces)
SSH_HOST_1=10.0.1.50
SSH_USER_1=ec2-user
SSH_KEY_PATH_1=~/.ssh/id_rsa
SSH_PORT_1=22
SSH_ROLE_1=iap
SSH_LABEL_1=iap-node-1

SSH_HOST_2=10.0.1.51
SSH_USER_2=ec2-user
SSH_KEY_PATH_2=~/.ssh/id_rsa
SSH_PORT_2=22
SSH_ROLE_2=mongodb
SSH_LABEL_2=mongo-primary

SSH_HOST_3=10.0.1.52
SSH_USER_3=ec2-user
SSH_KEY_PATH_3=~/.ssh/id_rsa
SSH_PORT_3=22
SSH_ROLE_3=mongodb
SSH_LABEL_3=mongo-secondary-1

# Legacy single-host (still supported, used if no SSH_HOST_N blocks):
# SSH_HOST=10.0.1.50
# SSH_USER=ec2-user
# SSH_KEY_PATH=~/.ssh/id_rsa

# ── Kubernetes / EKS log collection (live customer environments) ──
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_SESSION_TOKEN=
KUBE_NAMESPACE=itential-prod
KUBE_POD_PATTERN=platform6

# ── /deploy-containers — Kubernetes reproduction environment ──────
# Required for provisioning a new EKS cluster + ElastiCache Redis.
# See environments/k8s-example.env for a complete populated example.

K8S_CLUSTER_GRADE=minimum          # minimum | production
EKS_CLUSTER_NAME=itential-repro    # used as name prefix for ElastiCache resources
EKS_CLUSTER_REGION=us-east-2
EKS_NODE_TYPE=                     # auto-set from grade: m5a.xlarge (minimum) | c6a.4xlarge (production)
EKS_NODE_COUNT=                    # auto-set from grade: 2 (minimum) | 3 (production)
EKS_K8S_VERSION=1.31
EKS_LBC_ROLE_ARN=                  # IAM role ARN for AWS Load Balancer Controller; skill auto-creates for new clusters

# External MongoDB (required — Helm charts do NOT bundle MongoDB)
MONGO_URL=                         # mongodb+srv://... or mongodb://host:port/db
ITENTIAL_MONGO_PASSWORD=

# External Redis — ElastiCache or standalone (required — Helm charts do NOT bundle Redis)
REDIS_HOST=
REDIS_PORT=6379
ITENTIAL_REDIS_PASSWORD=           # Redis AUTH token; leave blank if Redis has no auth

# IAP secrets (written to itential-platform-secrets K8s secret)
ITENTIAL_ENCRYPTION_KEY=           # 64-char hex; auto-generated if blank
ITENTIAL_DEFAULT_USER_PASSWORD=

# TLS (optional — skip for port-forward-only repro)
TLS_CA_CERT_PATH=                  # path to CA cert file

# Ingress (optional — skip for port-forward-only repro)
K8S_INGRESS_TYPE=alb               # alb | nginx
K8S_HOSTNAME=                      # FQDN e.g. iap-repro.internal.example.com
K8S_INGRESS_SCHEME=internet-facing
ACM_CERT_ARN=                      # ACM certificate ARN for ALB TLS

# Adapter delivery
K8S_ADAPTER_METHOD=pv              # pv (persistent volumes) | layered (baked-in image)
K8S_ADAPTER_PV_SIZE=10Gi

# kubectl targeting
K8S_NAMESPACE=itential
K8S_CONTEXT=                       # blank = current-context from KUBECONFIG

# ECR registry (do not change)
ECR_REGISTRY=497639811223.dkr.ecr.us-east-2.amazonaws.com

# ── IAG direct diagnostics ────────────────────────────────────
IAG_URL=http://localhost:8083
IAG_USERNAME=admin
IAG_PASSWORD=admin
IAG_VERSION=4         # "4" or "5"

# ── Metrics ───────────────────────────────────────────────────
PROMETHEUS_URL=http://localhost:9090
GRAFANA_URL=http://localhost:3000
GRAFANA_API_KEY=

# ── Application logs ──────────────────────────────────────────
WEBSERVER_LOG_CONTAINER=platform      # Docker container for IAP
MONGO_LOG_CONTAINER=mongodb
REDIS_LOG_CONTAINER=redis
LB_TYPE=nginx                         # nginx|haproxy|aws_alb|f5
LB_CONTAINER=nginx

# ── Jira known-issue search ───────────────────────────────────
JIRA_URL=https://itential.atlassian.net
JIRA_USER=you@itential.com
JIRA_API_TOKEN=your-token-from-atlassian
JIRA_PROJECTS=ENG,ISD
```

The skill reads whatever is present and tells you what's **missing** for deeper investigation. It never asks for credentials that are already in `.env`.

---

## How to Invoke

```
/troubleshoot
/troubleshoot job ABC123 stuck in error state since 9am
/troubleshoot adapter-servicenow OFFLINE after upgrade to 5.55.5
/troubleshoot platform slow, jobs taking 10x longer than yesterday
/troubleshoot workflow import failing with 500
```

The argument is optional but helps skip triage questions. If omitted, the skill asks what's broken.

---

## Investigation Phases

The orchestrator runs a **3-phase investigation model** followed by four closing phases. Not every issue needs all phases — it routes by symptom in Phase 2.

### Investigation Protocol (runs throughout all phases)

The **Itential Product Support Investigation Protocol** (8 sections: symptoms, timeline, reproducibility, impact, business impact, resolution, recovery time, evidence) governs all customer communication at every phase. It is not a one-time questionnaire — it runs continuously and must be kept current.

### Phase 1: Ticket Understanding & Triage

Entirely offline — Jira, Confluence, and local reference files only. No platform authentication.

- Read the ISD ticket (Jira): summary, description, attachments, SLA, all comments
- Extract structured context to `ticket_context.md`
- **Platform support status check**: cross-reference IAP version against `data/product-capability-reference.md` — flag EOL versions immediately
- Mine similar ISD/ENG Jira tickets for past resolutions and open bugs
- Search Confluence for runbooks and KB articles
- **Priority mismatch detection**: scan description for high-impact signals ("production down", "go-live blocked") — escalate immediately if detected
- **Generate engineer question list**: structured pre-investigation checklist (Version & Environment, Symptom Precision, Scope & Reproduction, Recent Changes, Evidence Needed) — gates Phase 2
- Produce `pre-investigation-summary.md`

**Gate:** engineer reviews questions and decides to proceed or send them to the customer first.

### Phase 2: Symptom Analysis & Routing

No orchestrator-level platform authentication. Sub-skills authenticate themselves from `.env` when invoked.

- Address any Investigation Protocol gaps still open after Phase 1
- Classify the issue (Functional vs Performance/Non-functional) and route:

| Symptom | Sub-skill |
|---|---|
| Workflow/job/JST/import issues | `/troubleshoot-workflows` |
| Adapter OFFLINE / auth failure | `/troubleshoot-adapters` |
| Stuck/slow jobs | `/troubleshoot-jobs` |
| MongoDB/Redis | `/troubleshoot-databases` |
| CPU/memory/disk/containers | `/troubleshoot-infra` |
| Log collection needed | `/troubleshoot-logs` |
| IAG inline / Kafka inline / UI latency inline | Phase 2 inline diagnostics |

- After sub-skill confirms root cause → route to builder-skills for fix construction (Constructive Fix Path)

### Phase 3: Reproduce & Workaround

Authentication happens here — after the engineer selects an environment.

**Step 3a — Environment Selection**: Discover all `.env` and `.env.*` files in the project. If multiple exist, present a numbered list showing each file's `PLATFORM_URL` and ask the engineer to choose. Authenticate with the selected `.env`.

**`.env` naming convention:**
- `.env` — default customer environment (project root)
- `.env.{label}` — named environments (e.g., `.env.staging`, `.env.acme-prod`)
- `repro/{ISD_TICKET_KEY}/.env` — isolated local reproduction environment

**Step 3b — Reproduce**: Using the authenticated session, trigger the confirmed root cause in the selected environment. Import failing workflow/adapter config, execute trigger steps, capture evidence.

**Step 3c — Find Workarounds using builder-skills**: In the same selected environment, invoke the appropriate builder-skill:
- Workflow structural issue → `/builder-agent`
- JST error → `/builder-agent` (test locally, then PUT)
- Adapter misconfiguration → `/troubleshoot-adapters` fix path
- JSON Form → `/itential-json-forms`
- MOP command template → `/itential-mop`
- LCM issue → `/itential-lcm`

All writes require explicit engineer approval. The builder-skill invocation does not bypass read-only-by-default rules.

**Local reproduction environment (when needed)**: Create `repro/{ISD_TICKET_KEY}/.env` with Docker-local credentials, then spin up a version-matched local stack (Docker Compose). Validate the fix there before applying to the customer environment.

### Closing Phases

| Phase | Name | What Happens |
|-------|------|-------------|
| **4** | Diagnostic Report | Full findings, evidence, root cause, recommendations saved to `diagnostic_report.md` |
| **5** | Engineering Escalation | Bug report drafted, ENG ticket created (with approval), linked to ISD |
| **6** | Resolution Learning | Resolution pattern appended to `data/known-resolutions.md`; ISD ticket resolved |
| **7** | Manager Escalation | Priority mismatch templates, SLA-breach escalation, management messaging |

**Quick routing guide:**

```
Adapter OFFLINE         → Phase 2 (troubleshoot-adapters) → Phase 3 (workaround)
Jobs slow               → Phase 2 (troubleshoot-jobs + troubleshoot-databases)
IAG failing             → Phase 2 inline IAG diagnostics + troubleshoot-adapters
Disk / memory / OOM     → Phase 2 (troubleshoot-infra)
UI slow / API timeouts  → Phase 2 inline UI diagnostics + troubleshoot-logs
Kafka consumer lag      → Phase 2 inline Kafka diagnostics
EOL platform version    → Phase 1 flags immediately, recommend upgrade path
Production down         → Phase 1 Step 1f detects, escalates before investigation
```

---

## Safety Rules

The skill **never** performs destructive actions without explicit user consent:

- No `PUT`, `DELETE`, or `PATCH` API calls
- No MongoDB writes, updates, or deletes — read-only only
- No Redis `SET`, `DEL`, `FLUSHDB` — read-only only
- No service, adapter, or container restarts without confirmation
- Adapter debug mode requires explicit consent — it exposes credentials in logs
- Adapter cleanup (reset `auth_logging` + `console_level`) always runs after debug mode
- All ISD comments must be internal (`Service Desk Team` visibility only)
- All Jira/ENG actions require explicit engineer approval per action

---

## Actions the Skill Can Perform

| Action | Requires Consent? |
|--------|------------------|
| Read IAP health endpoints | No |
| Get job details and error messages | No |
| Search jobs by name/status | No |
| Get adapter settings | No |
| Compare adapter config against sampleProperties | No |
| Run MongoDB read queries | No |
| Run Redis read commands | No |
| Collect Docker/K8s/SSH logs | No |
| Search Jira ENG/ISD projects | No |
| Save artifacts to `data/{TIMESTAMP}/` | No |
| Enable adapter `auth_logging` or `console_level=debug` | **Yes — always ask** |
| Restart an adapter or service | **Yes — always ask** |
| Any PUT/PATCH/DELETE on any endpoint | **Yes — always ask** |
| Any MongoDB write operation | **Never (blocked)** |
| Any Redis write operation | **Never (blocked)** |

---

## Key Commands (What the Skill Runs Internally)

These are the underlying API calls and shell commands the skill uses. Useful for understanding what it's doing or running manually.

### IAP Health Snapshot

```bash
# App health
curl -sk "$PLATFORM_URL/health/applications?token=$TOKEN"

# Adapter health
curl -sk "$PLATFORM_URL/health/adapters?token=$TOKEN"
```

### Get a Failing Job

```bash
# By job ID
curl -sk "$PLATFORM_URL/operations-manager/jobs/$JOB_ID?token=$TOKEN"

# Search by workflow name + status
curl -sk "$PLATFORM_URL/operations-manager/jobs?name=MyWorkflow&status=error&limit=5&token=$TOKEN"
```

### Get Adapter Settings

```bash
curl -sk "$PLATFORM_URL/adapters/adapter-servicenow?token=$TOKEN" \
  | python3 -c "
import sys,json
d=json.load(sys.stdin)
p=d['data']['properties']['properties']
print('stub:', p.get('stub'))
print('host:', p.get('host'))
print('auth_method:', p.get('authentication',{}).get('auth_method'))
"
```

### MongoDB Diagnostics

```bash
# Current slow operations (>5s)
mongosh "$MONGO_URL" --eval "db.currentOp({secs_running:{'\$gt':5}})"

# Key collection sizes
mongosh "$MONGO_URL" --eval "
['jobs','workflows','adapters'].forEach(function(c){
  print(c + ': ' + db.getCollection(c).countDocuments({}));
})"

# Slow query log (last 10 queries >100ms)
mongosh "$MONGO_URL" --eval "
db.system.profile.find({millis:{'\$gt':100}}).sort({ts:-1}).limit(10).forEach(function(p){
  print(p.ts, p.op, p.ns, p.millis+'ms');
})"
```

### Redis Diagnostics

```bash
# Memory summary
redis-cli -h $REDIS_HOST -p $REDIS_PORT INFO memory | grep -E "used_memory_human|maxmemory|evicted"

# Bull queue depths
redis-cli -h $REDIS_HOST KEYS "bull:*:waiting" | while read k; do
  echo "$k: $(redis-cli -h $REDIS_HOST LLEN $k)"
done

# Slow log
redis-cli -h $REDIS_HOST SLOWLOG GET 10
```

### Docker Container Health

```bash
# CPU and memory snapshot
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

# Check for OOMKilled
docker inspect platform | python3 -c "
import sys,json
d=json.load(sys.stdin)[0]
s=d.get('State',{})
print('OOMKilled:', s.get('OOMKilled'))
print('Restarts:', d.get('RestartCount'))
"

# Tail IAP logs
docker logs --tail 200 platform 2>&1 | grep -E "ERROR|WARN|error|warn"
```

### Jira Search

```bash
JIRA_AUTH=$(echo -n "$JIRA_USER:$JIRA_API_TOKEN" | base64)
JQL='project in (ENG,ISD) AND text ~ "WorkflowBuilder stopped during execution" ORDER BY updated DESC'

curl -s "$JIRA_URL/rest/api/3/search" \
  -H "Authorization: Basic $JIRA_AUTH" \
  -G --data-urlencode "jql=$JQL" \
  --data-urlencode "fields=summary,status,fixVersions,resolution" \
  --data-urlencode "maxResults=10"
```

---

## Examples

### Example 1 — Adapter OFFLINE

```
/troubleshoot adapter-servicenow shows OFFLINE in health dashboard
```

**What the skill does:**
1. Authenticates to IAP, confirms adapter `RUNNING/OFFLINE`
2. Fetches adapter settings — finds `stub: false`, `auth_method: request_token`, `token_timeout: -1`
3. Fetches `sampleProperties` from GitLab — `token_timeout` default is `3600`
4. **Finding**: `token_timeout: -1` disables automatic token refresh. Adapter authenticates at startup then never refreshes. Token expired after 1 hour → OFFLINE.
5. Saves report to `data/20260404_090000/adapter-servicenow/report.md`

**Sample output:**
```
## Issues Found
- 🔴 Critical: token_timeout: -1 — token refresh disabled; adapter goes OFFLINE after token expires
- 🟡 Warning: healthcheck.type: startup — healthcheck only runs at boot, not on schedule

## Recommendations
1. Set token_timeout to 3600 (matches sampleProperties default)
2. Change healthcheck.type to intermittent so OFFLINE is detected automatically
3. Restart adapter after changes to force re-authentication
```

---

### Example 2 — Jobs Slow (Performance Investigation)

```
/troubleshoot jobs are running 10x slower than normal since 2pm yesterday
```

**What the skill does:**
1. Fetches running/queued job counts — finds 847 jobs queued (normal is <10)
2. Checks MongoDB `currentOp` — finds 3 operations running >60 seconds on the `jobs` collection
3. Checks Redis Bull queues — `bull:WorkFlowEngine:waiting` has 1,200 entries backed up
4. Checks Docker stats — `platform` container at 98% CPU
5. Searches Jira ENG: finds `ENG-4821` "Bull queue saturation under high concurrency" — fixed in 5.55.6
6. Customer is on 5.55.5

**Sample output:**
```
## Root Cause
Bull job queue saturation. 1,200+ jobs backed up in Redis queue. MongoDB showing 
3 long-running operations blocking the jobs collection. Platform CPU at 98%.

## Evidence
- Redis bull:WorkFlowEngine:waiting depth: 1,247
- MongoDB currentOp: 3 operations >60s on db.jobs (COLLSCAN — missing index)
- Docker CPU: platform 98.2% sustained

## Jira
- ENG-4821 [Done / Fixed in 5.55.6]: Bull queue saturation under high concurrency
  Workaround: add index on jobs.status+jobs.last_updated (see ticket comment)
  Fix: upgrade to 5.55.6+

## Recommendations
1. Immediate: apply index workaround from ENG-4821 (requires user consent for mongosh)
2. Short-term: upgrade to 5.55.6
3. Monitor: set up Bull queue depth alert at depth > 100
```

---

### Example 3 — Workflow Import Failing

```
/troubleshoot importing my workflow JSON returns 500 every time
```

**What the skill does:**
1. Reads the workflow JSON — checks structure
2. Attempts import wrapped as `{"workflow": {...}}` — still 500
3. Binary A/B isolation — strips fields group by group
4. **Finding**: top-level `"groups"` field missing from workflow JSON; IAP schema has `additionalProperties: false`
5. Adds `"groups": []`, re-imports — succeeds

**Sample output:**
```
## Root Cause
Workflow JSON is missing the required top-level "groups": [] field.
IAP's WorkflowBuilder schema uses additionalProperties: false — any missing
required field causes a silent 500 with "WorkflowBuilder stopped during execution".

## Fix
Add "groups": [] at the top level of the workflow JSON (same level as "name", 
"tasks", "transitions"). The import will succeed immediately after.

## Fixed JSON (diff)
+ "groups": [],
  "name": "my-workflow",
  ...
```

---

### Example 4 — IAG Adapter OFFLINE

```
/troubleshoot iag1 adapter shows RUNNING/OFFLINE
```

**What the skill does:**
1. Gets adapter settings — extracts `host` (172.18.0.5), `port` (8083), `auth_method: request_token`, `token_timeout: -1`
2. Tests connectivity: `curl -m 5 http://172.18.0.5:8083/api/v2.0/about` → `EHOSTUNREACH`
3. Checks Docker network: `docker network inspect itential_default` — no container at 172.18.0.5
4. Checks IAG container status: IAG container not running
5. **Finding**: IAG container stopped/not started. IP in adapter config is stale (container was recreated and got a new IP).

**Sample output:**
```
## Root Cause
IAG container is not running. The IP stored in the IAP adapter (172.18.0.5) 
has no container. EHOSTUNREACH confirms no routing path to that address.

## Recommendations
1. Start the IAG container: docker compose up -d iag (requires user consent)
2. After restart, verify IAG's new IP: docker inspect iag | grep IPAddress
3. If IP changed, update adapter host field to new IP
4. Also: set token_timeout to 3600 — current value -1 will cause OFFLINE on next token expiry
```

---

## Output Artifacts

Every investigation saves artifacts to `data/{TIMESTAMP}/`:

| File | Contents |
|------|---------|
| `summary.md` | One-line status per component investigated |
| `diagnostic_report.md` | Full report: findings, root cause, recommendations |
| `{adapter}/report.md` | Adapter gather report (settings, misconfigs, issues) |
| `{adapter}/log_analysis.md` | Adapter debug log analysis |
| `webserver_raw.txt` | Raw webserver.log for the incident window |
| `webserver_analysis.md` | HTTP error rates, slow endpoints, top routes |
| `mongod_raw.txt` | Raw mongod.log for the incident window |
| `mongod_analysis.md` | Slow queries, connection events, COLLSCAN hits |
| `redis_raw.txt` | Raw Redis log for the incident window |
| `redis_analysis.md` | Memory warnings, eviction, BGSAVE failures |
| `lb_access_raw.txt` | Raw LB access log for the incident window |
| `lb_analysis.md` | 5xx rates, upstream timeouts, top clients |
| `jira_findings.md` | Matching ENG/ISD tickets, fix versions, workarounds |
| `os_diagnostics.txt` | Docker stats, disk, CPU, memory, FD limits |
| `api_latency.txt` | Response time measurements per endpoint |

---

## How to Use It Effectively

**1. Give the incident time upfront**
The skill filters logs and jobs to a time window. "Yesterday at 2pm EST" is far more useful than "recently". The more specific, the faster the investigation.

```
/troubleshoot platform was slow yesterday 2026-04-03 between 14:00-16:00 EST
```

**2. Include error text when you have it**
Copy-paste the exact error string from the UI or logs. This drives both the investigation and the Jira search.

```
/troubleshoot job failing with "No config found for Adapter: servicenow-prod"
```

**3. Name the adapter or workflow**
Avoids searching through all jobs. The skill goes directly to the right asset.

```
/troubleshoot adapter-automation_gateway named iag1 shows OFFLINE
```

**4. Have `.env` set up before starting**
The skill reads credentials from `.env`. If `MONGO_URL` isn't there when MongoDB diagnostics are needed, it notes the gap and asks you to add it. Fewer interruptions = faster investigation.

**5. Let it run phases in sequence**
Each phase surfaces evidence that informs the next. Don't skip ahead — Phase 3a findings often explain what Phase 3b is showing.

**6. Add Jira credentials for internal investigations**
If you're on the Itential team, `JIRA_API_TOKEN` unlocks Phase 3j which cross-references every finding against known ENG/ISD tickets. Takes 30 seconds to set up and can short-circuit an hour of debugging.

---

## Known Bugs — Quick Reference

Recognise these on sight. If the error string matches, skip straight to the workaround.

| Ticket | Error / Symptom | Component | Affected | Workaround |
|--------|----------------|-----------|----------|------------|
| ISD-9261 | `TypeError: Cannot set properties of undefined (setting 'childJobLoopIndex')` in browser console when clicking "enable query" on a looped childJob task | Automation Studio UI | Platform 6.4.0 | Pass the `data_array` value as a separate flat job variable; wire `data_array` directly to `$var.job.varName` without enable query. See `troubleshooting-specs/spec-troubleshoot-workflows.md §8` and `data/2026-06-09T00-00-00/ISD-9261/`. |

> **Note:** ISD-9261 errors only appear in the **browser console** (F12 → Console). They do not show in `job.error[]`, the UI job view, or platform logs. If a customer reports "enable query doesn't work on childJob" this is the bug — no platform-side investigation needed.

---

## Common Pitfalls

| Pitfall | What to Do Instead |
|---------|-------------------|
| Starting with Jira search before diagnosing | Run Phase 3a–3i first; use specific error strings for Jira search |
| Checking `job.status: complete` and stopping | Always check `job.error` — jobs can be `complete` with errors |
| Assuming adapter `RUNNING` = healthy | `RUNNING/OFFLINE` = process up, connection down. Check connectivity |
| Using adapter instance name in `app` field | `app` must be the **type name** (e.g., `Servicenow`), not instance name (e.g., `servicenow-prod`) |
| Forgetting `token_timeout: -1` is the default | This is the #1 cause of IAG going OFFLINE hours after deployment |
| Checking one Redis key | Bull queues have multiple states: `waiting`, `active`, `failed`, `delayed` — check all |
| Normalizing load average without CPU count | `load avg 4.0` on a 2-core host is critical; on 8-core it's fine |
