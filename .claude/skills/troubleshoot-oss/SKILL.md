---
name: troubleshoot-oss
description: Diagnose ISD and IPSO tickets about open-source Itential tools from github.com/itential — the Ansible deployer, Helm charts (iap-helm / iag5-helm / iag4-helm), job-archiver, IPCTL, itential-MCP, and itential-dev-stack. Uses the GitHub public API (no auth required) for release correlation, changelog diffing, issue matching, and config requirement extraction.
argument-hint: "[<tool-name> | --list]"
---

# /troubleshoot-oss — Open-Source Itential Tools Diagnostic

Invoked from Phase 2 routing when `ticket_context.md` contains `Component: OSS-Tool`,
or when the engineer explicitly invokes for a GitHub-hosted Itential tool issue.

All GitHub API calls are unauthenticated GET requests — no token needed. All fetched
source content is analyzed in memory; only derived findings (config key names, version
ranges, error pattern summaries) are saved to disk or posted to Jira.

---

## OSS Tool Registry

| Tool key | Display name | GitHub repo | Ticket signal words |
|---|---|---|---|
| `deployer` | Itential Deployer | `itential/itential.deployer` | deployer, FAILED!, ansible, playbook, become, role, collection install |
| `iap-helm` | IAP Helm Chart | `itential/iap-helm` | iap-helm, helm install, helm upgrade + IAP, iap chart |
| `iag5-helm` | IAG5 Helm Chart | `itential/iag5-helm` | iag5-helm, IAG5 on kubernetes, gateway5 helm |
| `iag4-helm` | IAG4 Helm Chart | `itential/iag4-helm` | iag4-helm, IAG4 on kubernetes, gateway4 helm |
| `job-archiver` | Job Archiver | `itential/job-archiver` | job archiver, archiver, job retention, archive jobs |
| `ipctl` | IPCTL | `itential/ipctl` | ipctl, iap cli, command-line tool |
| `itential-mcp` | itential-MCP | `itential/itential-mcp` | MCP server, claude integration, mcp tool, tool call fail |
| `dev-stack` | itential-dev-stack | `itential/itential-dev-stack` | dev stack, make setup, make up, itential-dev-stack |

---

## CRITICAL SAFETY RULES

- **GitHub API is read-only** — all calls are `GET`; never POST, PATCH, or PUT to GitHub
- **Never open a GitHub issue** without displaying the full draft and receiving explicit engineer "yes"
- **Never save source code** fetched from GitHub repos — same rule as adapters Phase 5; record only derived insights (config key names, version strings, error summaries, boolean flags)
- **Read-only platform API** (inherited) — no write operations to IAP without explicit engineer approval

---

## Phase 0 — Tool Detection

### Step 0a — Identify the OSS tool

If invoked with a tool name argument (e.g. `/troubleshoot-oss deployer`):
- Match against the Tool key column in the registry above; set `OSS_TOOL` and `OSS_REPO`.

If invoked with `--list`:
- Print the registry table and exit. No further steps.

If invoked with no argument or the tool is ambiguous:
- Read `ticket_context.md` — scan the `Summary`, `Error Message`, `Symptom`, and `Component` fields
- Match against the signal words in the registry
- If exactly one tool matches: confirm it and proceed
- If multiple or zero matches, present a numbered menu:

```
Which OSS tool does this ticket involve?
  1) Itential Deployer      (itential/itential.deployer)
  2) IAP Helm Chart         (itential/iap-helm)
  3) IAG5 Helm Chart        (itential/iag5-helm)
  4) IAG4 Helm Chart        (itential/iag4-helm)
  5) Job Archiver           (itential/job-archiver)
  6) IPCTL                  (itential/ipctl)
  7) itential-MCP           (itential/itential-mcp)
  8) itential-dev-stack     (itential/itential-dev-stack)

Choice [1-8]:
```

Set:
```
OSS_TOOL=<tool-key>
OSS_REPO=itential/<repo-name>
OSS_RAW=https://raw.githubusercontent.com/${OSS_REPO}/main
OSS_API=https://api.github.com/repos/${OSS_REPO}
```

Also read `OSS_VERSION` from `ticket_context.md` field `OSS Version:` (written by triage Step 1b). If not present, ask the engineer: "What version of {display name} is the customer running? (check the ticket or ask the customer — `deployer --version`, `helm show chart iap/iap | grep appVersion`, etc.)"

### Step 0b — Confirm

Print a one-line confirmation:
```
🔍 Diagnosing: {display name} ({OSS_REPO}) — customer version: {OSS_VERSION | unknown}
```

---

## Phase 1 — GitHub Repository Baseline

Run all three fetches in sequence (each is a single HTTP GET; do not block if one fails — note the failure and continue).

### Step 1a — README Analysis

```
WebFetch https://raw.githubusercontent.com/{OSS_REPO}/main/README.md
  (fallback: /master/README.md)
```

Analyze in memory. Extract and record only:
- **Minimum version requirements** — e.g. "Requires IAP ≥ 6.x", "Ansible ≥ 2.13", "Kubernetes ≥ 1.28", "Helm ≥ 3.10"
- **Required configuration keys** — table of required settings / env vars with no defaults
- **Known limitations callouts** — any "⚠️ Note:", "Known issue:", "Limitation:" blocks
- **TROUBLESHOOTING section** — if present, extract error patterns and their resolutions

### Step 1b — Release History

```
WebFetch https://api.github.com/repos/{OSS_REPO}/releases?per_page=20
```

Parse the JSON response (array of release objects). Find the release matching `OSS_VERSION`:
- Match `tag_name` against `OSS_VERSION` (try exact, then with/without `v` prefix, then substring)
- Note `CUSTOMER_RELEASE_DATE` (the `published_at` of the matched release)
- Count releases after the customer's release: `RELEASES_BEHIND`
- Extract `body` field of the latest release for a quick "what's new"

If the customer version is not in the last 20 releases, note "version predates GitHub release history" and set `RELEASES_BEHIND = unknown (old version)`.

### Step 1c — Changelog Analysis

```
WebFetch https://raw.githubusercontent.com/{OSS_REPO}/main/CHANGELOG.md
  (fallback: /master/CHANGELOG.md, then /CHANGELOG.rst, then /CHANGELOG)
```

Scan for the section corresponding to `OSS_VERSION`. Extract:
- Bug-fix entries for the customer's version (lines with "fix", "bug", "patch", "regression")
- Bug-fix entries in all versions between customer's version and latest that mention the affected component or error keyword from the ticket

Record only the textual summary lines — not surrounding code blocks.

### Step 1d — Baseline Summary Display

Print a formatted box:

```
╔══════════════════════════════════════════════════════════════╗
║  GitHub Baseline: {OSS_REPO}                                 ║
╠══════════════════════════════════════════════════════════════╣
║  Customer version:  {OSS_VERSION | unknown}                  ║
║  Latest release:    {LATEST_RELEASE} ({LATEST_RELEASE_DATE}) ║
║  Releases behind:   {RELEASES_BEHIND}                        ║
╠══════════════════════════════════════════════════════════════╣
║  Min requirements from README:                               ║
║    {requirement 1}                                           ║
║    {requirement 2}                                           ║
╠══════════════════════════════════════════════════════════════╣
║  Changelog fixes since customer version (relevant area):     ║
║    {vX.Y.Z: fix description}                                 ║
║    — (none found) if empty                                   ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Phase 2 — GitHub Issue Correlation

### Step 2a — Build Search Terms

From `ticket_context.md`, extract `Error Message`. Clean it:
- Strip UUID / IP / hostname tokens
- Take the first complete sentence (up to 150 characters)
- Remove surrounding quotes

Also extract a component keyword from `Symptom` (e.g. "playbook", "helm", "archive", "connection refused").

Set:
```
ERROR_TERM="{cleaned error message}"
COMPONENT_TERM="{component keyword}"
```

### Step 2b — Search GitHub Issues

Run two searches (open and closed):

```
WebFetch https://api.github.com/search/issues?q={ERROR_TERM}+repo:{OSS_REPO}&state=open&per_page=5
WebFetch https://api.github.com/search/issues?q={ERROR_TERM}+repo:{OSS_REPO}&state=closed&per_page=5
```

If `ERROR_TERM` is empty or too short (< 10 chars), substitute `COMPONENT_TERM`.

For each result, parse: `number`, `title`, `state`, `state_reason`, `created_at`, `html_url`, `labels[].name`, `pull_request` (if present → indicates a PR, not an issue), `body` (first 200 chars).

### Step 2c — Correlation Judgment

For each matching issue, assess:

1. Does the customer's error string appear in the issue title or body?
2. Does the customer's version fall in the affected range (compare CUSTOMER_RELEASE_DATE vs issue created_at and any referenced fix version)?

Classify each match:

| Classification | Meaning | Action |
|---|---|---|
| `KNOWN_BUG — open` | Issue is open, no fix yet | Report URL; draft ISD internal comment noting the issue |
| `KNOWN_BUG — fixed in vX.Y.Z` | Issue is closed with a fix version tagged | Report fix version; compare vs customer version; advise upgrade if customer is behind |
| `WONT_FIX` | Issue closed as `not_planned` or labeled `wont-fix` | Explain the official stance; pivot to workaround |
| `PARTIAL_MATCH` | Issue title/body is related but not exact | Surface it as a lead; flag for manual review |
| `NO_MATCH` | No matching issues found | Continue to Phase 3 |

Display the classification prominently:

```
══════════════════════════════════════════════════════════════
  GitHub Issue Correlation: {OSS_REPO}
══════════════════════════════════════════════════════════════

  🔴 KNOWN_BUG — open
     Issue #456: "{issue title}"
     Opened: {date} | Labels: {labels}
     URL: https://github.com/{OSS_REPO}/issues/456
     First 200 chars of body: "{excerpt}"

  — or —

  🟢 KNOWN_BUG — fixed in v2.3.1
     Issue #389: "{issue title}" (closed: completed)
     Fix tagged: v2.3.1 (customer is on v{OSS_VERSION})
     URL: https://github.com/{OSS_REPO}/issues/389
     Recommendation: upgrade to v2.3.1 or later

  — or —

  ✅ NO_MATCH — no open or closed GitHub issues match this error.
     Proceeding to tool-specific diagnostics.
══════════════════════════════════════════════════════════════
```

---

## Phase 3 — Tool-Specific Diagnostics

Branch by `OSS_TOOL`. Each branch may fetch additional files from the repo (in-memory only).

---

### Phase 3A — Itential Deployer (`deployer`)

Fetch in-memory:
```
WebFetch https://raw.githubusercontent.com/itential/itential.deployer/main/roles/iap/defaults/main.yml
WebFetch https://raw.githubusercontent.com/itential/itential.deployer/main/meta/main.yml
```

(Fallback: browse the directory tree via `WebFetch https://api.github.com/repos/itential/itential.deployer/contents/roles/iap` to confirm actual paths.)

**Extract (record):**
- All `iap_*` variables with no default (or `null` / `""` default) → these are **required** for the role to work; check if all are present in the customer's `run-vars.yml` or inventory
- `min_ansible_version` from `meta/main.yml` → compare to customer's Ansible version (if provided)

**Common failure mode table — show always:**

| Ansible task pattern | Root cause | Check |
|---|---|---|
| `Install IAP RPMs` / `yum install` failure | JFrog token expired or wrong RPM URL | Verify `JFROG_TOKEN` freshness; check `platform_packages` URL pattern in `run-vars.yml` — `GATEWAY-MANAGER` repo doubles its name in the path |
| `Configure MongoDB replica set` | Host unreachable during setup | SSH key, security group port 27017, `mongodb_hosts` in inventory |
| `Start IAP service` / systemd fails | RPM not actually installed or wrong package name | Check `platform_packages` list — version string must match an existing JFrog artifact |
| `Generate TLS certificates` / `itential.tls` role missing | Ansible collection not installed | `ansible-galaxy collection install itential.tls` |
| `UNREACHABLE` | SSH port blocked or wrong key | SG ingress rule port 22, `ansible_ssh_private_key_file` path, `chmod 400` on key |
| `sudo: a password is required` | `become` without sudo password or NOPASSWD | Set `ansible_become_password` or configure `NOPASSWD: ALL` in `/etc/sudoers.d/` |
| `Package not found` in JFrog | Wrong JFrog repo name or version | URL-encode version string; check repo routing table in CLAUDE.md `JFrog RPM Repository` section |
| Python `ImportError` | Ansible Python dependencies missing on target | `dnf install python3-{library}` on target node before re-running |

**Ask the engineer:**
1. "What Ansible version is running? (`ansible --version`)"
2. "What is the exact task name where the playbook failed? (from the `FAILED!` line in the Ansible output)"
3. "Is `run-vars.yml` used, or a raw inventory file? (affects variable lookup path)"

---

### Phase 3B — Helm Charts (`iap-helm`, `iag5-helm`, `iag4-helm`)

Determine the chart directory. For `iap-helm` the main chart is typically in the repo root; for `iag5-helm` and `iag4-helm` similarly.

Fetch in-memory:
```
WebFetch https://raw.githubusercontent.com/itential/{OSS_REPO_NAME}/main/values.yaml
WebFetch https://raw.githubusercontent.com/itential/{OSS_REPO_NAME}/main/Chart.yaml
```

**Extract (record):**
- All `existingSecret` references → these are K8s Secrets the customer must pre-create; list them
- Default `storageClassName` → check if the customer has that StorageClass or has overridden it
- `kubeVersion` constraint from `Chart.yaml` → compare to cluster version
- `appVersion` from `Chart.yaml` → IAP/IAG version this chart targets
- `resources.requests.cpu` and `resources.requests.memory` → minimum resources per pod

**Check against the customer's environment (ask engineer if unknown):**

| Required | How to verify |
|---|---|
| `{existingSecret}` K8s Secrets exist | `kubectl get secret -n {NAMESPACE} {secret-name}` |
| StorageClass `{storageClassName}` exists | `kubectl get storageclass {name}` |
| Cluster Kubernetes ≥ `{kubeVersion}` | `kubectl version --short` |
| Each node has ≥ `{resources.requests}` free | `kubectl describe node` → Allocatable vs Requested |
| ECR pull secret exists | `kubectl get secret ecr-pull-secret -n {NAMESPACE}` |

**Common failure mode table — show always:**

| Error / symptom | Root cause | Fix |
|---|---|---|
| `PersistentVolumeClaim pending` | StorageClass missing or wrong name | Create the StorageClass (`iap-ebs-gp3` is the EBS CSI default — see `/deploy-containers` skill) |
| `ImagePullBackOff` | ECR pull secret missing or expired | Recreate: `aws ecr get-login-password ... \| kubectl create secret docker-registry ecr-pull-secret ...` |
| `CrashLoopBackOff` on IAP pod | Env var or secret value wrong (e.g. `ITENTIAL_ENCRYPTION_KEY` must be 64 hex chars) | `kubectl logs {pod} --previous`; verify secret contents |
| `Error: INSTALLATION FAILED: template ...` | Helm version incompatible or missing required value | Check `helm version` vs `Chart.yaml` `kubeVersion`; run `helm install --debug` for template render error detail |
| Pod `Pending` indefinitely | Node resources exhausted | `kubectl describe pod {pod} -n {ns}` → Events section; consider `resources.requests` reduction or node scale-out |
| `secret not found` on pod startup | `existingSecret` key doesn't exist in the namespace | `kubectl get secret {name} -n {ns}` to verify name and namespace match exactly |
| `cert-manager` errors | cert-manager not installed or wrong version | `kubectl get pods -n cert-manager`; check version constraint |

**Run dry-run Helm check if cluster is accessible:**
```bash
helm upgrade --install {release} {chart} \
  --namespace {NAMESPACE} \
  --dry-run \
  --debug \
  2>&1 | head -100
```
Surface any template rendering errors or missing values to the engineer.

---

### Phase 3C — Job Archiver (`job-archiver`)

Fetch in-memory:
```
WebFetch https://raw.githubusercontent.com/itential/job-archiver/main/README.md
```

**Extract (record):**
- Required configuration keys (IAP URL, auth, batch_size, age_threshold, archive destination)
- Minimum IAP version compatibility (README often states this)
- Cron schedule or daemon mode configuration

**Common failure mode table:**

| Error / symptom | Root cause | Check |
|---|---|---|
| `Connection refused` / `ECONNREFUSED` | IAP URL is wrong or IAP is not reachable | Verify `IAP_URL` config; test with `curl -k {IAP_URL}/health/platform` from the archiver host |
| `401 Unauthorized` | IAP token expired or wrong credentials | Re-generate the IAP service account token; verify `CLIENT_ID` / `CLIENT_SECRET` or `AUTH_TOKEN` |
| Jobs not being archived | `age_threshold` too short or archiver not running | Check archiver process/cron is active; confirm `age_threshold` matches intended retention |
| Archiver crashes (OOM) | `batch_size` too large for job payload size | Reduce `batch_size` (try 100 → 25); check job payload size in MongoDB |
| `Permission denied` on archive destination | Filesystem / S3 permissions | Verify destination path write permissions or S3 IAM policy |
| No output / silent failure | Incorrect log level or stdout redirect | Set `LOG_LEVEL=debug` and check full stdout |

**Ask the engineer:**
1. "What does the archiver config look like? (share `config.json` / `.env` — mask credentials)"
2. "Is the archiver running as a cron job, systemd service, or container?"
3. "How many jobs are expected to be archived, and how large are they on average?"

---

### Phase 3D — IPCTL (`ipctl`)

Fetch in-memory:
```
WebFetch https://raw.githubusercontent.com/itential/ipctl/main/README.md
```

**Extract (record):**
- Config file location and format (usually `~/.ipctl/config.yaml` or `IPCTL_CONFIG`)
- Required env vars (`IAP_URL`, `IAP_TOKEN` or `CLIENT_ID` / `CLIENT_SECRET`)
- Authentication method (token vs. OAuth)

**Common failure mode table:**

| Error / symptom | Root cause | Check |
|---|---|---|
| `connection refused` | IAP URL wrong or IAP not reachable | `curl -k {IAP_URL}/health/platform` from the IPCTL host |
| `401 Unauthorized` | Token expired or missing | Re-authenticate: `ipctl auth login` or re-generate token |
| `command not found: ipctl` | Binary not in PATH or not installed | Check install location; `which ipctl`; verify PATH includes the install dir |
| `certificate verify failed` | Self-signed TLS cert on IAP | Set `--insecure` flag or add CA cert to system trust store |
| `no such resource` / `404` | Resource name mismatch or wrong IAP version | Verify resource name with `ipctl {resource-type} list` |
| Config file not found | Default config path doesn't exist | Run `ipctl config init` or set `IPCTL_CONFIG=/path/to/config.yaml` |

**Ask the engineer:**
1. "What exact IPCTL command was run? (full command line)"
2. "What does `ipctl version` output?"
3. "Is IPCTL using a config file or environment variables for auth?"

---

### Phase 3E — itential-MCP (`itential-mcp`)

Fetch in-memory:
```
WebFetch https://raw.githubusercontent.com/itential/itential-mcp/main/README.md
WebFetch https://api.github.com/repos/itential/itential-mcp/contents/src
```

**Extract (record):**
- Required env vars (from README — typically `IAP_URL`, `IAP_TOKEN` or `CLIENT_ID`/`CLIENT_SECRET`, possibly `IAP_MCP_PORT`)
- Available tool names (from `src/` directory listing — file names often map to tool names; do NOT read file contents, only names)
- MCP server transport (stdio vs. HTTP/SSE — from README)

**Common failure mode table:**

| Error / symptom | Root cause | Check |
|---|---|---|
| `Tool not found: {tool_name}` | Typo in tool name or tool added in newer version | Compare exact spelling vs. available tools in this version; check CHANGELOG for when the tool was added |
| `Request failed` / connection error | IAP URL not reachable from MCP server host | `curl -k {IAP_URL}/health/platform` from the MCP host |
| `401` / `403` from IAP | Token expired or insufficient IAP scopes | Re-generate token; verify the service account has the required IAP roles |
| MCP server not starting | Missing required env vars | Check all required env vars are set; run `node --version` — check Node.js version requirement |
| Tool calls hang / timeout | IAP response too slow or request body too large | Check IAP health; check MCP server timeout config |
| Claude / client can't connect to MCP | Wrong transport or port mismatch | Verify MCP config (`~/.claude/mcp_config.json` or `claude_desktop_config.json`) matches MCP server transport and host/port |

**Ask the engineer:**
1. "What tool call is failing? (exact tool name and arguments)"
2. "Is the MCP server running in stdio mode (Claude Desktop) or HTTP/SSE mode?"
3. "What does the MCP server log show when the call fails?"

---

### Phase 3F — itential-dev-stack (`dev-stack`)

Fetch in-memory:
```
WebFetch https://raw.githubusercontent.com/itential/itential-dev-stack/main/README.md
WebFetch https://raw.githubusercontent.com/itential/itential-dev-stack/main/.env.example
```

**Extract (record):**
- All required `.env` keys without defaults → check if customer has them set
- Docker Compose version requirement
- Makefile targets available (from README)

**Common failure mode table:**

| Error / symptom | Root cause | Check |
|---|---|---|
| `make setup` fails at key generation | `ITENTIAL_ENCRYPTION_KEY` not set or wrong length (must be 64 hex chars) | Run `openssl rand -hex 32` and set in `.env` |
| `make up` services unhealthy | ECR login expired | Run `make login` first; verify AWS credentials are fresh |
| `ConnectionRefused` from host machine | `BIND_ADDRESS=127.0.0.1:` set on a VM | Change to `BIND_ADDRESS=` (empty = all interfaces) for VM access |
| Platform container exits immediately | Wrong image tag or ECR access denied | Check `PLATFORM_IMAGE` tag matches a real ECR tag; run `docker pull {image}` manually to test |
| MongoDB / Redis containers keep restarting | Volume permission issue or conflicting previous run | `docker compose down -v` to clear volumes, then `make setup` again |
| `make clean` removes data | Intentional — `make clean` destroys volumes | Warn: use `make down` (stop without data loss); `make clean` only with explicit approval |

**Note:** `make clean` destroys MongoDB data volumes and is irreversible. This skill will not run `make clean` without explicit engineer confirmation — same rule as the `/deploy-containers` skill.

---

## Phase 4 — Findings Summary and Escalation

### Step 4a — Build findings block

Append to `data/{TIMESTAMP}/{TICKET_KEY}/gather_report.md` under a new section:

```markdown
## Phase OSS — Open-Source Tool Diagnostic

**Tool:** {display name} ({OSS_REPO})
**Customer version:** {OSS_VERSION}
**Latest version:** {LATEST_RELEASE} ({RELEASES_BEHIND} releases behind)
**Analyzed:** {ISO timestamp}

### GitHub Issue Match
{KNOWN_BUG — open #NNN: "{title}" {URL} | KNOWN_BUG — fixed in vX.Y.Z | WONT_FIX | NO_MATCH}

### Minimum Requirements (from README)
{list: requirement → MET / UNVERIFIED / NOT MET (if known)}

### Configuration Gaps
{list of required config keys not confirmed present — or "none identified"}

### Changelog Fixes Missed
{list: vX.Y.Z — fix summary — or "none relevant found"}

### Failure Mode Match
{most likely failure mode from Phase 3 table — or "unknown, needs more info"}

### Recommended Actions
1. {highest-impact action}
2. {second action}
3. {third action, if applicable}
```

### Step 4b — Escalation path decision

**If a matching open GitHub issue was found (`KNOWN_BUG — open`):**
- Draft an ISD internal comment:
  ```
  Internal: This issue matches an open GitHub issue on {OSS_REPO}:
  #{number}: {title}
  {URL}

  Recommend subscribing to that issue for updates. If customer needs a fix urgently,
  consider whether a workaround is available (see diagnostic notes above).
  ```
  Show the draft and wait for explicit engineer "yes" before posting via the servicedesk API (`public: false`).

**If the bug is fixed in a newer version (`KNOWN_BUG — fixed in vX.Y.Z`):**
- Draft an ISD internal comment recommending upgrade to the fix version.
- If this is an IPSO ticket: note the fix version in the ENG ticket draft.

**If no issue was found and the problem is reproducible:**
- Recommend the engineer open a GitHub issue. Draft:
  ```
  Title: {concise description of the problem}

  ## Environment
  - Tool version: {OSS_VERSION}
  - IAP version: {from ticket_context}
  - OS / Kubernetes version: {from ticket_context}

  ## Steps to Reproduce
  {from ticket description + diagnostic findings}

  ## Expected Behavior
  {what should happen}

  ## Actual Behavior
  {error message / observed behavior}

  ## Additional Context
  {any changelog findings, config gap findings}
  ```
  Show the draft. Ask: "Open this GitHub issue? [yes / edit / no]"
  If "yes": `WebFetch POST https://api.github.com/repos/{OSS_REPO}/issues` with the draft body — **only after explicit "yes"**.
  Note: Opening a GitHub issue requires a GitHub token. If no token is available, tell the engineer to open it manually and paste the draft.

**If this is an IPSO ticket:**
- Add the GitHub issue correlation findings to the ENG ticket draft (in `eng_ticket_draft.md`)
- Note the GitHub issue URL if one exists

### Step 4c — Post findings to Jira (ISD only)

When the engineer approves, post the Recommended Actions as an internal ISD comment:
```
Internal: OSS Tool Diagnostic — {display name} v{OSS_VERSION}

{Recommended Actions 1-3 from Step 4a}

GitHub issue: {URL or "no matching issue found"}
Upgrade available: {yes — v{fix_version} | no}

Full analysis saved to gather_report.md.
```

---

## Quick Reference

### Tool detection signals

| Signal in ticket | Likely tool |
|---|---|
| `FAILED!` + Ansible task name | `deployer` |
| `helm install` / `helm upgrade` + IAP | `iap-helm` |
| `helm install` / `helm upgrade` + IAG5 | `iag5-helm` |
| `helm install` / `helm upgrade` + IAG4 | `iag4-helm` |
| `archiver` / `job archiver` / `job retention` | `job-archiver` |
| `ipctl` command / IAP CLI | `ipctl` |
| `MCP server` / `tool call` / `Claude integration` | `itential-mcp` |
| `dev stack` / `make setup` / `make up` | `dev-stack` |

### Phase flow

```
0 — Tool detection  →  OSS_TOOL + OSS_REPO + OSS_VERSION set
1 — GitHub baseline →  README requirements, release version, changelog diffs
2 — Issue search    →  KNOWN_BUG / NO_MATCH classification
3 — Tool-specific   →  Config gap check, failure mode table, engineer questions
4 — Findings + ESC  →  gather_report.md section, ISD comment, GitHub issue draft
```

### GitHub API calls used (all unauthenticated GET)

| Step | API call |
|---|---|
| 1a | `raw.githubusercontent.com/{repo}/main/README.md` |
| 1b | `api.github.com/repos/{repo}/releases?per_page=20` |
| 1c | `raw.githubusercontent.com/{repo}/main/CHANGELOG.md` |
| 2b | `api.github.com/search/issues?q={term}+repo:{repo}&state=open` |
| 2b | `api.github.com/search/issues?q={term}+repo:{repo}&state=closed` |
| 3E | `api.github.com/repos/{repo}/contents/src` (directory listing only) |
