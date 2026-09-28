---
name: troubleshoot-adapters
description: Troubleshoot IAP adapters — gather settings, compare against sampleProperties, run debug mode (auth_logging/console_level), capture live logs, clean up after debugging, and inspect OSS adapter source from GitLab for error code mapping, auth pattern analysis, and reproduction step construction. Covers OFFLINE, wrong data, auth failure, Kafka consumer lag, and GitLab source inspection scenarios.
argument-hint: "[adapter name]"
---

# Troubleshoot Adapters

**Owns:** Full adapter diagnostic cycle — gather (settings collection and misconfiguration analysis), debug mode (live log capture during restart), and cleanup (reverse debug settings). Also owns Kafka adapter diagnostics: broker connectivity, consumer group lag, and topic partition analysis.
**Use when:** An adapter is OFFLINE, returning wrong data, failing auth, a job error has `IAPerror.source: adapter`, or a Kafka adapter is OFFLINE / consumer lag is growing.

---

## CRITICAL SAFETY RULES

- **Gather phase: GET only** — no modifications
- **Debug phase and Cleanup: PUT/restart require explicit user consent before each action**
- **Always run Cleanup (Phase 3) after Debug (Phase 2)** — leaving `auth_logging: true` exposes credentials in logs
- **Read `.env` for credentials** — never ask the user for credentials already in `.env`
- **builder-skill invocations also use `.env`** — when invoking builder-skills for fixes or workarounds (after Phase 1 or Phase 2 confirms root cause), source `.env` before invoking so the skill targets the correct platform with the correct credentials
- **Install (Phase 6): file staging is unattended, activation is not** — deploying a new adapter package's files to disk (`npm pack`/tar extract/`npm install` under `services/adapter-<name>/`) requires no consent and may proceed automatically. Everything that makes the new adapter *live* — the platform restart that loads the model, and creating the adapter/sample instance — requires explicit engineer consent first, enforced by `.claude/hooks/bash-safety-guard.py` (`RESTART_APPROVED=yes` / `ADAPTER_CREATE_APPROVED=yes`). Never add either marker without a clear "yes" from the engineer in the current conversation.

---

## Auth Reuse

**Env file selection:** If the orchestrator already ran Step 3a and `.auth.json` exists with a token less than 50 minutes old and `platform_url` matches, reuse that token directly — skip env discovery.

If no valid cached token exists, run env discovery before authenticating:

```bash
# Discover all .env files across the entire project tree
python3 - <<'PYEOF'
import os
project = "{project_path}"
skip = {".git", "node_modules", "__pycache__", ".venv", "vendor", ".terraform"}
found = []
for root, dirs, files in os.walk(project):
    dirs[:] = [d for d in dirs if d not in skip]
    for f in files:
        if f == ".env" or f.startswith(".env."):
            found.append(os.path.relpath(os.path.join(root, f), project))
found.sort()
for i, p in enumerate(found, 1):
    url = ""
    try:
        for line in open(os.path.join(project, p)):
            if line.startswith("PLATFORM_URL="):
                url = line.split("=", 1)[1].strip(); break
    except Exception: pass
    print(f"  [{i}] {p}  →  {url or '[PLATFORM_URL not set]'}")
if not found:
    print("No .env files found. Create one at the project root.")
PYEOF
```

If multiple files found → present the list and ask the engineer to choose before authenticating. See `/troubleshoot` Step 3a for the full interactive selection flow (including mix-and-match variables from different files).

Check `{project_path}/.auth.json`:
- If `platform_url` matches the selected env and `timestamp` < 50 minutes old → reuse token
- Otherwise authenticate from the selected env file and save `.auth.json`

**Password auth:**
```bash
curl -sk -X POST "{PLATFORM_URL}/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "{USERNAME}", "password": "{PASSWORD}"}'
```

**OAuth:**
```bash
curl -sk -X POST "{PLATFORM_URL}/oauth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id={CLIENT_ID}&client_secret={CLIENT_SECRET}&grant_type=client_credentials"
```

---

## Phase 1: Gather — Settings Collection & Analysis

**Cloud customer check (read ticket_context.md first):** If `cloud_customer_flag: true` is set in `ticket_context.md`, the customer is on `itential-saas` — Itential-managed cloud IAP with on-premises IAG. Apply these constraints for every step in this skill:

| Scenario | Cloud customer (itential-saas) note |
|---|---|
| Adapter OFFLINE | **First check: Itential NAT IP whitelisting.** Itential NATs all adapter/integration traffic. The target system must whitelist Itential's NAT IP, not the customer's on-prem IP. Confirm current NAT IP with cloud ops before any settings changes. |
| SSH to IAP nodes | Not available — IAP is Itential-managed. Cannot run `docker logs`, `pm2 logs`, or host-level commands on IAP nodes. Request logs from Itential cloud ops. |
| SSH to IAG | Available — IAG runs on customer on-prem. SSH targets in `.env` for cloud customers are IAG hosts. |
| Adapter settings API | Available — use platform API via `*.itential.io` URL as normal. |
| sampleProperties fetch | Available — use `npm show {package_id} dist-tags.latest` + package registry as normal. |

If the adapter was ONLINE and went OFFLINE without a customer-side change → lead with NAT IP rotation as the hypothesis before investigating credentials or host/port settings.

### Step 1a — Identify Adapter

If the user hasn't specified an adapter name, list all adapters with health:

```bash
curl -sk "{PLATFORM_URL}/health/adapters?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for a in d.get('results', []):
    state = a.get('state','?')
    conn  = a.get('connection',{}).get('state','?')
    pkg   = a.get('package_id','?')
    flag  = '🔴' if conn != 'ONLINE' else '✅'
    print(f'{flag} {a[\"id\"]}: {state}/{conn}  ({pkg})')
"
```

### Step 1b — Get Full Adapter Settings

```bash
ADAPTER_NAME="{ADAPTER_NAME}"

curl -sk "{PLATFORM_URL}/adapters/${ADAPTER_NAME}?token={TOKEN}" \
  -o /tmp/${ADAPTER_NAME}_settings.json

# Parse and display key fields
# Settings shape: response.data.properties.properties (double-nested)
python3 -c "
import sys, json
d = json.load(open('/tmp/{ADAPTER_NAME}_settings.json'))
data  = d.get('data', d)
props = data.get('properties',{}).get('properties',{})
auth  = props.get('authentication',{})
hc    = props.get('healthcheck',{})
ssl   = props.get('ssl',{})
log   = props.get('loggerProps',{})

print('=== Adapter Settings: {ADAPTER_NAME} ===')
print('package_id (model):', data.get('model','?'))
print('host:              ', props.get('host'))
print('port:              ', props.get('port'))
print('protocol:          ', props.get('protocol'))
print('base_path:         ', props.get('base_path'))
print('stub:              ', props.get('stub'))
print()
print('--- Auth ---')
print('auth_method:       ', auth.get('auth_method'))
print('token_timeout:     ', auth.get('token_timeout'))
print('auth_field:        ', auth.get('auth_field'))
print('auth_field_format: ', auth.get('auth_field_format'))
print('auth_logging:      ', auth.get('auth_logging'))
print()
print('--- Healthcheck ---')
print('type:              ', hc.get('type'))
print('URI_Path:          ', hc.get('URI_Path'))
print('frequency:         ', hc.get('frequency'))
print()
print('--- SSL ---')
print('ssl.enabled:       ', ssl.get('enabled'))
print('accept_invalid_cert:', ssl.get('accept_invalid_cert'))
print()
print('--- Logging ---')
print('console_level:     ', log.get('console_level'))
print('log_level:         ', log.get('log_level') or log.get('logLevel'))
"
```

### Step 1c — Fetch sampleProperties (Source of Truth)

```bash
# Derive repo name from package_id
REPO_NAME=$(python3 -c "
import json
d = json.load(open('/tmp/{ADAPTER_NAME}_settings.json'))
model = d.get('data',{}).get('model','')
print(model.split('/')[-1])
")

echo "Fetching sampleProperties for: ${REPO_NAME}"
# Use detected default_branch from Step 5a if available, otherwise try master then main
BRANCH="${ADAPTER_DEFAULT_BRANCH:-master}"
curl -s "https://gitlab.com/itentialopensource/adapters/${REPO_NAME}/-/raw/${BRANCH}/sampleProperties.json" \
  -o /tmp/{ADAPTER_NAME}_sample.json 2>/dev/null
# Fallback: if file is empty/HTML (adapter uses 'main'), retry with 'main'
if ! python3 -c "import json; json.load(open('/tmp/{ADAPTER_NAME}_sample.json'))" 2>/dev/null; then
  curl -s "https://gitlab.com/itentialopensource/adapters/${REPO_NAME}/-/raw/main/sampleProperties.json" \
    -o /tmp/{ADAPTER_NAME}_sample.json 2>/dev/null
fi

# Verify we got valid JSON (not a GitLab redirect page)
head -c 100 /tmp/{ADAPTER_NAME}_sample.json
python3 -c "
import json
try:
    json.load(open('/tmp/{ADAPTER_NAME}_sample.json'))
    print('sampleProperties: ✅ fetched')
except:
    print('sampleProperties: ⚠️ not available (private adapter or network issue)')
" 2>/dev/null
```

> **GitLab deeper inspection available:** If `sampleProperties` is unavailable, incomplete, or the gather report leaves root cause unclear after Step 1d, run **Phase 5 (GitLab Source Inspection)** to fetch and analyze `error.json`, `package.json`, and the adapter source directly from `https://gitlab.com/itentialopensource/adapters/{REPO_NAME}`. Phase 5 produces derived findings only — no code is saved or shared.

### Step 1d — Compare Live Settings vs sampleProperties

```bash
python3 -c "
import json

live = json.load(open('/tmp/{ADAPTER_NAME}_settings.json'))
live_p = live.get('data',{}).get('properties',{}).get('properties',{})
live_auth = live_p.get('authentication',{})

try:
    sample = json.load(open('/tmp/{ADAPTER_NAME}_sample.json'))
    sample_p = sample.get('properties',{})
    sample_auth = sample_p.get('authentication',{})
except:
    print('sampleProperties not available — manual review only')
    sample_p = {}
    sample_auth = {}

print('=== Critical Checks ===')

# Stub mode
stub = live_p.get('stub')
if stub:
    print('🔴 CRITICAL: stub=true — no real API calls are made')
else:
    print('✅ stub: false')

# Auth method
live_am = live_auth.get('auth_method')
samp_am = sample_auth.get('auth_method')
if samp_am and live_am != samp_am:
    print(f'🔴 MISMATCH auth_method: live={live_am}  expected={samp_am}')
else:
    print(f'✅ auth_method: {live_am}')

# Host
host = live_p.get('host','')
if not host or host in ('localhost','127.0.0.1',''):
    print(f'⚠️  host={repr(host)} — may not be pointed at target system')
else:
    print(f'✅ host: {host}')

# Protocol/SSL match
proto = live_p.get('protocol','')
ssl_en = live_p.get('ssl',{}).get('enabled', False)
if proto == 'https' and not ssl_en:
    print('🔴 MISMATCH: protocol=https but ssl.enabled=false — TLS errors will occur')
elif proto == 'http' and ssl_en:
    print('⚠️  protocol=http but ssl.enabled=true — verify intent')
else:
    print(f'✅ protocol={proto} ssl.enabled={ssl_en}')

# Base path
live_bp = live_p.get('base_path','')
samp_bp = sample_p.get('base_path','')
if samp_bp and live_bp != samp_bp:
    print(f'⚠️  base_path: live={repr(live_bp)}  expected={repr(samp_bp)}')

# Auth field/format
for f in ('auth_field','auth_field_format'):
    lv = live_auth.get(f)
    sv = sample_auth.get(f)
    if sv and lv != sv:
        print(f'⚠️  MISMATCH {f}: live={repr(lv)}  expected={repr(sv)}')

# Token timeout
tt = live_auth.get('token_timeout')
if str(tt) == '-1':
    print('⚠️  token_timeout=-1: No auto token refresh — adapter requires restart to re-authenticate after token expiry')
elif tt and int(tt) > 0:
    print(f'✅ token_timeout={tt}ms ({int(tt)//60000} min refresh)')

print()
print('=== AWS-Specific Checks ===')
# AWS temporary credentials
for k in ('aws_access_key','accessKeyId','access_key_id'):
    v = live_p.get(k) or live_auth.get(k,'')
    if v and v.startswith('ASIA'):
        print(f'⚠️  {k} starts with ASIA = STS temporary credentials (expire in 1-12h). Use long-lived IAM key (AKIA) or automate rotation.')
" 2>/dev/null
```

### Step 1e — Save Gather Report

Save to `{project_path}/data/{TIMESTAMP}/{ADAPTER_NAME}/gather_report.md`:

```markdown
# Adapter Gather Report: {ADAPTER_NAME}
**Generated:** {YYYY-MM-DD HH:MM:SS UTC} | **Platform:** {PLATFORM_URL}
**Status:** {STATE} / {CONNECTION} | **Package:** {package_id}

## Issues Found
- 🔴 **Critical**: {issue} — {why it matters}
- 🟡 **Warning**: {issue} — {why it matters}
- 🟢 **Info**: {note}

## Mismatched Settings
| Setting | Current | Expected | Impact |
|---------|---------|----------|--------|

## Recommendations
1. {Highest-impact fix first}
2. {If connectivity issue persists → proceed to Phase 2 Debug Mode (with user consent)}
```

If the gather report shows a clear misconfiguration (stub=true, wrong host, wrong auth_method), **present findings and ask the user if they want to fix it** before proceeding to debug mode.

**Fix path — apply corrective settings (with engineer approval):**

When root cause is identified from Phase 1 gather and the engineer approves the fix:

```bash
# Use the already-fetched settings as the base (avoid a redundant GET)
# Only modify the identified misconfigured fields — never touch other fields
python3 << 'EOF'
import json

with open('/tmp/{ADAPTER_NAME}_settings.json') as f:
    settings = json.load(f)

# Apply targeted fixes — only the fields identified in the gather report:
# Examples (uncomment the applicable one(s)):
# settings['stub'] = False                    # stub=true disabling
# settings['token_timeout'] = 3600000         # -1 means never refresh
# settings['ssl'] = {**settings.get('ssl',{}), 'enabled': True}
# settings['host'] = '{CORRECT_HOST}'
# settings['port'] = {CORRECT_PORT}

with open('/tmp/{ADAPTER_NAME}_fixed.json', 'w') as f:
    json.dump(settings, f)
print("Fixed settings written — confirm fields before PUT:")
for k in ['stub', 'host', 'port', 'ssl', 'auth_method', 'token_timeout']:
    if k in settings:
        print(f"  {k}: {settings[k]}")
EOF

# PUT the corrected settings (full replacement — confirm with engineer first)
curl -sk -X PUT "{PLATFORM_URL}/adapters/{ADAPTER_NAME}?token={TOKEN}" \
  -H "Content-Type: application/json" \
  -d @/tmp/{ADAPTER_NAME}_fixed.json \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
status = d.get('status', d.get('data', {}).get('status', '?'))
print('Adapter status after PUT:', status)
"

# Restart adapter (requires explicit consent — prompt before running)
curl -sk -X PUT "{PLATFORM_URL}/adapter-manager/adapters/{ADAPTER_NAME}/restart?token={TOKEN}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Restart:', d.get('message','?'))"

# Verify ONLINE after restart (poll up to 25s — 5 attempts × 5s)
for i in $(seq 1 5); do
  STATUS=$(curl -sk "{PLATFORM_URL}/adapter-manager/adapters/{ADAPTER_NAME}?token={TOKEN}" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))")
  echo "[$i/5] Adapter status: $STATUS"
  [ "$STATUS" = "ONLINE" ] && break || sleep 5
done
```

> **Safe fields to fix without extra caution:** `stub`, `token_timeout`, `ssl.enabled`, `protocol`, `base_path`
>
> **Fields requiring extra confirmation:** `host`, `port`, `auth_method`, `credentials`, `ssl.ca` — pause and confirm each before applying

**Docs reference (if not already in triage docs_references.md):**

If the adapter is an OSS adapter (`adapter-*` package other than IAG) and `docs_references.md` does not yet have an Adapters section, run a targeted lookup now:

```
WebSearch("{adapter package name} configuration site:docs.itential.com/adapters")
WebSearch("{adapter package name} authentication site:docs.itential.com/adapters")
```

For `adapter-nso` or Cisco NSO–related adapters, also check:
```
WebSearch("{symptom or config key} site:docs.itential.com/cisco-nso")
```

Append any new findings to `{project_path}/data/{TIMESTAMP}/{TICKET_KEY}/docs_references.md` under the appropriate section. Use the live documentation to validate expected authentication configuration, required properties, and known adapter-specific behaviors before comparing settings in Step 1d.

---

## Phase 2: Debug Mode — Live Log Capture

**Only run this phase after Phase 1 gather if root cause is still unclear.**

Run steps in this exact order:
1. Start log watcher (Step 2a) FIRST
2. Enable debug settings (Step 2b) — requires user consent
3. Restart adapter (Step 2c) — requires user consent
4. Analyze captured logs (Step 2d)
5. Always run Phase 3 (Cleanup) when done

### Step 2a — Start Live Log Watcher (Before Restart)

**Docker:**
```bash
mkdir -p {project_path}/data/{TIMESTAMP}/{ADAPTER_NAME}/

docker logs -f platform 2>&1 \
  | grep -i "{ADAPTER_NAME}\|auth\|token\|health\|error\|ECONNREFUSED\|EHOSTUNREACH" \
  | tee {project_path}/data/{TIMESTAMP}/{ADAPTER_NAME}/live_logs.txt &

echo "Log watcher PID: $!"
echo "Now proceed to Step 2b to enable debug settings."
```

**Kubernetes** (if `KUBE_NAMESPACE` in `.env`):
```bash
mkdir -p {project_path}/data/{TIMESTAMP}/{ADAPTER_NAME}/

timeout 300 stern -n {KUBE_NAMESPACE} {KUBE_POD_PATTERN} --since 1s 2>&1 \
  | tee {project_path}/data/{TIMESTAMP}/{ADAPTER_NAME}/live_logs.txt \
  | head -1000 &

echo "Stern log watcher PID: $!"
```

### Step 2b — Enable Debug Logging (User Consent Required)

> **Confirm with user before running: "I will enable auth_logging=true and console_level=debug on {ADAPTER_NAME} and restart it. This will log credential details. Proceed?"**

**CRITICAL: PUT does NOT support partial updates — always GET → modify → PUT full body.**

```bash
# 1. Re-fetch current settings
curl -sk "{PLATFORM_URL}/adapters/{ADAPTER_NAME}?token={TOKEN}" \
  -o /tmp/{ADAPTER_NAME}_settings.json

# 2. Build PUT body — enable debug settings
# Body must be wrapped in {"properties":{...}} stripping the metadata prefix
cat /tmp/{ADAPTER_NAME}_settings.json \
  | sed 's/{"metadata".*"type":"Adapter",/{"properties":{/' \
  | sed 's/"auth_logging":false/"auth_logging":true/' \
  | sed 's/"console_level":"error"/"console_level":"debug"/' \
  | sed 's/"console_level":"warn"/"console_level":"debug"/' \
  | sed 's/"console_level":"info"/"console_level":"debug"/' \
  > /tmp/{ADAPTER_NAME}_debug_body.json

# 3. Verify body before sending
grep -o '"auth_logging":true' /tmp/{ADAPTER_NAME}_debug_body.json && echo "auth_logging ✅"
grep -o '"console_level":"debug"' /tmp/{ADAPTER_NAME}_debug_body.json && echo "console_level ✅"
head -c 60 /tmp/{ADAPTER_NAME}_debug_body.json  # must start with {"properties":

# 4. PUT the modified settings
curl -sk -X PUT "{PLATFORM_URL}/adapters/{ADAPTER_NAME}?token={TOKEN}" \
  -H "Content-Type: application/json" \
  -d @/tmp/{ADAPTER_NAME}_debug_body.json | head -c 200
```

**Log level guide:**
| Level | Use When |
|-------|----------|
| `trace` | Maximum detail — every internal step, full request/response bodies |
| `debug` | Detailed — auth flows, API calls, responses **(default choice)** |
| `info` | Normal operation — insufficient for debugging |

### Step 2c — Restart Adapter (User Consent Required)

> **Confirm with user: "I will restart the {ADAPTER_NAME} adapter. It will be briefly unavailable. Proceed?"**

```bash
curl -sk -X PUT "{PLATFORM_URL}/adapters/{ADAPTER_NAME}/restart?token={TOKEN}"

echo "Adapter restarting. Waiting 15 seconds..."
sleep 15

# Quick health check post-restart
curl -sk "{PLATFORM_URL}/health/adapters?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for a in d.get('results',[]):
    if '{ADAPTER_NAME}' in a.get('id',''):
        state = a['state']
        conn = a.get('connection',{}).get('state','?')
        flag = '✅' if conn == 'ONLINE' else '⚠️ still OFFLINE'
        print(f'{a[\"id\"]}: {state}/{conn} {flag}')
"
```

### Step 2d — Analyze Captured Logs

```bash
# All captured logs
cat {project_path}/data/{TIMESTAMP}/{ADAPTER_NAME}/live_logs.txt | head -300

# Auth-specific patterns
grep -i "auth\|token\|credential\|401\|403\|password\|login" \
  {project_path}/data/{TIMESTAMP}/{ADAPTER_NAME}/live_logs.txt | head -100

# Connectivity errors
grep -i "ECONNREFUSED\|ETIMEDOUT\|ENOTFOUND\|EHOSTUNREACH\|certificate\|ssl\|tls" \
  {project_path}/data/{TIMESTAMP}/{ADAPTER_NAME}/live_logs.txt | head -100

# General errors
grep -i "error\|Error\|ERROR" \
  {project_path}/data/{TIMESTAMP}/{ADAPTER_NAME}/live_logs.txt | head -100
```

**Key log patterns:**

| Pattern | Root Cause |
|---------|-----------|
| `401` / `Unauthorized` | Wrong credentials or expired token |
| `403` / `Forbidden` | Credentials valid but insufficient permissions |
| `ECONNREFUSED` | Target host/port not reachable (host up, port closed) |
| `EHOSTUNREACH` / `ETIMEDOUT` | No routing to host — container/VM not running or wrong IP |
| `ENOTFOUND` | DNS resolution failure — wrong hostname |
| `certificate` / `ssl` / `tls` | TLS misconfiguration |
| `stub` in log | Adapter still in stub mode |
| `getToken: success` + still OFFLINE | Auth works but healthcheck URI wrong or returning non-200 |
| `token expired` / `jwt expired` | Token refresh issue — check `token_timeout` |

Save analysis to: `{project_path}/data/{TIMESTAMP}/{ADAPTER_NAME}/log_analysis.md`

**Fix path — apply confirmed root cause fix before cleanup (with engineer approval):**

When log analysis confirms a specific root cause, offer to apply the fix immediately — before Phase 3 cleanup — so the adapter comes back ONLINE in the same session:

```bash
# Determine fix by log pattern:

# ── 401 / token expired ──────────────────────────────────────────────────────
# Update credentials or fix token_timeout: GET → modify → PUT
curl -sk "{PLATFORM_URL}/adapters/{ADAPTER_NAME}?token={TOKEN}" \
  | python3 -c "
import sys, json
s = json.load(sys.stdin)
# Fix: update auth credentials or set token_timeout to 3600000
s['token_timeout'] = 3600000   # or update s['credentials'] fields
print(json.dumps(s))
" | curl -sk -X PUT "{PLATFORM_URL}/adapters/{ADAPTER_NAME}?token={TOKEN}" \
  -H "Content-Type: application/json" -d @-

# ── ECONNREFUSED / EHOSTUNREACH ───────────────────────────────────────────────
# Verify host/port, update if wrong
curl -sk "{PLATFORM_URL}/adapters/{ADAPTER_NAME}?token={TOKEN}" \
  | python3 -c "
import sys, json
s = json.load(sys.stdin)
s['host'] = '{CORRECT_HOST}'
s['port'] = {CORRECT_PORT}
print(json.dumps(s))
" | curl -sk -X PUT "{PLATFORM_URL}/adapters/{ADAPTER_NAME}?token={TOKEN}" \
  -H "Content-Type: application/json" -d @-

# After fix PUT: restart adapter (with consent) and verify ONLINE
curl -sk -X PUT "{PLATFORM_URL}/adapter-manager/adapters/{ADAPTER_NAME}/restart?token={TOKEN}"
sleep 10
curl -sk "{PLATFORM_URL}/adapter-manager/adapters/{ADAPTER_NAME}?token={TOKEN}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Status:', d.get('status','?'))"
```

> Apply only the single field confirmed by the log. Then proceed to **Phase 3 Cleanup** as normal — it will reset `auth_logging` and `console_level` regardless of whether a fix was applied here.

---

## Phase 3: Cleanup — Reset After Debugging

**Always run this after Phase 2.** Leaving `auth_logging: true` and debug log levels in production:
- Exposes credentials in log files (security risk)
- Generates high log volume (disk pressure)
- May trigger security alerts

> **Confirm with user: "I will reset auth_logging=false and console_level=error on {ADAPTER_NAME} and restart it. Proceed?"**

### Step 3a — Reset Adapter Settings

```bash
# 1. Get current settings (may have been modified in Phase 2)
curl -sk "{PLATFORM_URL}/adapters/{ADAPTER_NAME}?token={TOKEN}" \
  -o /tmp/{ADAPTER_NAME}_settings.json

# 2. Build PUT body — reverse debug changes
cat /tmp/{ADAPTER_NAME}_settings.json \
  | sed 's/{"metadata".*"type":"Adapter",/{"properties":{/' \
  | sed 's/"auth_logging":true/"auth_logging":false/' \
  | sed 's/"console_level":"debug"/"console_level":"error"/' \
  | sed 's/"console_level":"trace"/"console_level":"error"/' \
  > /tmp/{ADAPTER_NAME}_cleanup_body.json

# 3. Verify
grep -o '"auth_logging":false' /tmp/{ADAPTER_NAME}_cleanup_body.json && echo "auth_logging ✅"
grep -o '"console_level":"error"' /tmp/{ADAPTER_NAME}_cleanup_body.json && echo "console_level ✅"

# 4. PUT back (requires user consent)
curl -sk -X PUT "{PLATFORM_URL}/adapters/{ADAPTER_NAME}?token={TOKEN}" \
  -H "Content-Type: application/json" \
  -d @/tmp/{ADAPTER_NAME}_cleanup_body.json | head -c 200
```

### Step 3b — Restart and Verify

```bash
curl -sk -X PUT "{PLATFORM_URL}/adapters/{ADAPTER_NAME}/restart?token={TOKEN}"
sleep 15

curl -sk "{PLATFORM_URL}/health/adapters?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for a in d.get('results',[]):
    if '{ADAPTER_NAME}' in a.get('id',''):
        conn = a.get('connection',{}).get('state','?')
        flag = '✅ ONLINE' if conn == 'ONLINE' else '⚠️ still OFFLINE — root cause persists'
        print(f\"{a['id']}: {a['state']}/{conn} {flag}\")
"
```

**Cleanup verification:**
| Check | Expected |
|-------|---------|
| `auth_logging` | `false` |
| `console_level` | `error` |
| Adapter state | `RUNNING` |
| Connection state | `ONLINE` (if root cause resolved) |

If adapter is still OFFLINE after cleanup: the underlying issue (wrong host, bad credentials, target unreachable) persists. Return to gather findings and investigate the target system directly.

---

## Phase 4: Kafka Adapter Diagnostics

Run this phase when the IAP Kafka adapter is OFFLINE or consumer lag is growing. Kafka adapters do not follow the settings GET → PUT debug cycle (they have no `auth_logging` or `console_level` in sampleProperties), so this phase replaces Phase 1–3 for Kafka.

**Prerequisites:** `KAFKA_BOOTSTRAP`, `KAFKA_CONSUMER_GROUP`, and `KAFKA_TOPIC` must be set in `.env` (or confirm values with the engineer if absent).

---

### Step 4a — Identify Kafka Adapter State in IAP

```bash
# Check Kafka adapter health
curl -sk "{PLATFORM_URL}/health/adapters?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
results = d if isinstance(d, list) else d.get('results', [])
found = False
for a in results:
    if 'kafka' in a.get('package_id','').lower() or 'kafka' in a.get('id','').lower():
        conn = a.get('connection', {}).get('state', '?')
        flag = '🔴' if conn != 'ONLINE' else '✅'
        print(f\"{flag} {a['id']}: {a['state']}/{conn}  ({a.get('package_id','?')})\")
        found = True
if not found:
    print('No Kafka adapters found in /health/adapters response')
"
```

Look for: `RUNNING/OFFLINE` (adapter process up but broker unreachable) vs `STOPPED` (adapter process not running — check IAP logs).

---

### Step 4b — Broker Connectivity

```bash
# TCP reachability to each broker in the bootstrap list
# KAFKA_BOOTSTRAP may be comma-separated: broker1:9092,broker2:9092
for broker in $(echo "${KAFKA_BOOTSTRAP}" | tr ',' '\n'); do
  host="${broker%%:*}"
  port="${broker##*:}"
  result=$(echo | timeout 5 nc -zv "${host}" "${port}" 2>&1)
  echo "${broker}: ${result}"
done
```

**What to look for:**
- `Connection refused` → Kafka broker not running at that address
- `No route to host` → network path missing (firewall, VPC, security group)
- `Connection timed out` → port blocked upstream

If broker is unreachable, the adapter will stay OFFLINE regardless of settings. Escalate to customer infra team.

---

### Step 4c — Consumer Group Lag

```bash
# Check consumer group lag — run from the Kafka host (SSH) or via docker exec
# SSH deployment
ssh "${KAFKA_SSH_USER}@${KAFKA_SSH_HOST}" \
  "kafka-consumer-groups.sh --bootstrap-server ${KAFKA_BOOTSTRAP} \
   --describe --group ${KAFKA_CONSUMER_GROUP} 2>/dev/null"

# Docker deployment
docker exec apache-kafka kafka-consumer-groups.sh \
  --bootstrap-server "${KAFKA_BOOTSTRAP}" \
  --describe --group "${KAFKA_CONSUMER_GROUP}" 2>/dev/null \
  | awk 'NR==1 || /TOPIC/' | head -30
```

**Lag thresholds:**

| LAG value | Status | Action |
|---|---|---|
| 0 | ✅ IAP keeping up | No action needed |
| Steady non-zero | ⚠️ IAP behind | Check IAP CPU/memory — may need scaling |
| Growing rapidly | 🔴 IAP not consuming | Adapter OFFLINE or consumer thread crashed |
| > 10,000 | 🔴 Critical backlog | Urgent — jobs/events are accumulating |

---

### Step 4d — Topic Partition Details

```bash
# SSH deployment
ssh "${KAFKA_SSH_USER}@${KAFKA_SSH_HOST}" \
  "kafka-topics.sh --bootstrap-server ${KAFKA_BOOTSTRAP} \
   --describe --topic ${KAFKA_TOPIC} 2>/dev/null"

# Docker deployment
docker exec apache-kafka kafka-topics.sh \
  --bootstrap-server "${KAFKA_BOOTSTRAP}" \
  --describe --topic "${KAFKA_TOPIC}" 2>/dev/null
```

Check: leader assignment (no `-1` leader → broker election in progress), replication factor, ISR count. A partition with no ISR or leader = data unavailable.

---

### Step 4e — Kafka Adapter Logs in IAP

```bash
# Pull IAP application log lines for the Kafka adapter
# Docker deployment
docker logs iap-app 2>&1 \
  | grep -i "kafka\|consumer\|broker\|lag\|connect" \
  | tail -50

# SSH deployment — adjust log path from /health/applications
grep -i "kafka\|consumer\|broker\|lag" \
  /var/log/itential/platform.log 2>/dev/null | tail -50
```

Look for: connection refused, authentication errors, SSL handshake failures, consumer group rebalance loops.

---

### Step 4f — Fix & Verify

**Adapter OFFLINE due to broker unreachable:**
- Confirm broker address/port in IAP adapter settings matches what `nc` tested
- GET adapter settings → update `host`/`bootstrap.servers`/`port` → PUT (engineer approval required) → restart adapter (engineer approval required)

**Consumer lag growing, adapter ONLINE:**
- Lag growing = IAP consuming but not fast enough → check IAP CPU/memory via `/troubleshoot-infra`
- Lag stuck (no consumption) = adapter thread issue → restart the Kafka adapter (engineer approval required)

**Restart Kafka adapter:**
```bash
# Confirm with engineer before running
curl -sk -X PUT "{PLATFORM_URL}/adapters/{ADAPTER_NAME}/restart?token={TOKEN}" | jq .

# Verify state after ~15s
curl -sk "{PLATFORM_URL}/health/adapters?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
results = d if isinstance(d, list) else d.get('results', [])
for a in results:
    if 'kafka' in a.get('id','').lower():
        conn = a.get('connection', {}).get('state', '?')
        flag = '✅ ONLINE' if conn == 'ONLINE' else '⚠️ still OFFLINE'
        print(f\"{a['id']}: {a['state']}/{conn} {flag}\")
"
```

**No cleanup step for Kafka** — Kafka adapters have no debug settings (`auth_logging`, `console_level`) to reverse. Once the adapter is back ONLINE and lag is draining, the investigation is complete.

---

## Common Adapter Failure Patterns — Quick Reference

| Symptom | Root Cause | Step |
|---------|-----------|------|
| `RUNNING/OFFLINE` at startup | Network, wrong host/port, or auth failure | Phase 1 gather |
| `stub: true` | No real API calls — silent fake mode | Step 1d — set stub=false |
| `auth_method` mismatch | Wrong auth type fails every call | Step 1d |
| `EHOSTUNREACH` | Container/VM not running at configured IP | Check host/port config |
| `ECONNREFUSED` | Service running but wrong port or not bound | Check port config |
| `401` in debug logs | Wrong credentials | Check username/password/token fields |
| `403` in debug logs | Credentials valid, insufficient permissions | Check IAP user roles on target system |
| `certificate` error | TLS mismatch | Set `accept_invalid_cert: true` or fix cert |
| `token_timeout: -1` | No auto-refresh; goes OFFLINE after session expires | Update `token_timeout` to positive ms value |
| AWS `ASIA` key prefix | Temporary STS creds (expire 1-12h) | Use long-lived IAM key (`AKIA`) |
| `getToken: success` but OFFLINE | Healthcheck URI wrong | Correct `healthcheck.URI_Path` |
| **Kafka OFFLINE, broker reachable** | Consumer thread crashed | Restart Kafka adapter (Phase 4f) |
| **Kafka lag growing, adapter ONLINE** | IAP CPU/memory insufficient or thread blocked | Check infra (Phase 4f) |
| **Kafka lag stuck at non-zero** | IAP consumer not pulling messages | Restart Kafka adapter (Phase 4f) |
| **Kafka broker `Connection refused`** | Broker down or wrong bootstrap address | Phase 4b — update adapter settings |
| **Unknown error code in logs** | Error not in Quick Reference | Phase 5b — fetch error.json from GitLab |
| **sampleProperties unavailable** | Private adapter or npm fetch failed | Phase 5 — fetch direct from GitLab |
| **Settings correct but OFFLINE** | Subtle code-level auth/path mismatch | Phase 5d — inspect adapter source |
| **Need repro steps for ENG ticket** | Requires min-config + trigger steps | Phase 5e — construct reproduction steps |
| **Adapter repo name unclear / not found** | package_id doesn't map cleanly to a repo | Phase 5g — fuzzy search GitLab group |
| **Need to inspect helper files** | Auth logic in helpers/, not main entry | Phase 5h — browse repo tree, request specific file |
| **"Install this adapter"** | New adapter needed for troubleshooting/testing | Phase 6a → 6c (clone from GitLab → install → restart with permission) → 6d |
| **"Create a sample adapter instance"** | Engineer wants a ready-to-use config | Phase 6b — build config from ticket + user input; 6d — POST instance |

---

## Device Simulation Options (when no real device is available)

Use this section when an ISD ticket involves device commands (IOS-XR, Cisco IOS, NX-OS, Juniper, etc.) via IAG4 or IAG5, and the engineer has no real device to test against.

**IAG4 vs IAG5 — which gateway is the customer using?**

| Signal in ticket | Gateway | Connection model |
|---|---|---|
| "AGManager", "automation_gateway adapter", "iag4", `/api/v2.0/` | **IAG4** | Platform polls IAG via REST; adapter type = `automation_gateway` |
| "GatewayManager", "cluster", "mTLS", "iag5", "cluster_id", "iag5-service" | **IAG5** | IAG initiates outbound mTLS WebSocket to Platform; registered as a Gateway Manager cluster |

For IAG4: `IAG_VERSION=4` in `.env`; IAG4 uses `/api/v2.0/services`, `/api/v2.0/jobs`  
For IAG5: `IAG_URL` points to Gateway Manager; cluster registered at `GET {PLATFORM_URL}/api/v2/gateway-manager/clusters`

**Options for simulating a device (ranked by setup speed):**

### Option 1 — Mock IAG service (fastest, no device OS needed)

Create a Python service in IAG that returns static mock output for the command being tested. This reproduces adapter/workflow logic without an actual device.

```python
# save as mock_iosxr_service.py on the IAG host
def handler(params):
    command = params.get("command", "")
    if "show version" in command:
        return {"output": "Cisco IOS XR Software, Version 7.5.2\n..."}
    if "show ip interface" in command:
        return {"output": "GigabitEthernet0/0/0/0 is up, line protocol is up\n..."}
    return {"output": f"% Unknown command: {command}"}
```

For IAG4: register the file as a Python service in IAG (`/api/v2.0/scripts`).  
For IAG5: deploy as a Gateway service; it will appear in the service catalogue automatically.

**Limitation:** No real IOS-XR OS — device-specific error codes and timing behaviour will not match. Use only to reproduce adapter parsing logic bugs.

### Option 2 — Containerlab + Cisco XRd (IOS-XR only, near-real OS)

Cisco XRd containers run a real IOS-XR control plane in Docker. Requires a Cisco XRd license (request via Cisco DevNet).

```yaml
# topology.yaml for containerlab
name: xrd-lab
topology:
  nodes:
    xrd-1:
      kind: cisco_xrd
      image: ios-xr/xrd-control-plane:7.9.2
      env:
        XR_INTERFACES: MgmtEth0/RP0/CPU0/0:linux:mgmt
```

```bash
# Deploy
containerlab deploy -t topology.yaml
# Connect IAG to the XRd management interface IP
```

Connect IAG4 or IAG5 to the XRd management IP on port 22 using SSH credentials configured at XRd boot. Node attributes for Inventory Manager:
```json
{ "itential_host": "172.20.20.2", "itential_platform": "iosxr", "cluster_id": "local-cluster" }
```

**Limitation:** Requires XRd license; data-plane forwarding not available in XRd control-plane image.

### Option 3 — CML (Cisco Modeling Labs)

Full Cisco simulation platform supporting IOS-XR, IOS, NX-OS, ASA, and more. Requires a corporate CML license (contact Cisco account team or check if PE team has shared CML).

- Devices run full Cisco OS images (not containers)
- Best for feature-specific parity (MPLS, segment routing, etc.)
- Once a device is booted in CML, connect IAG4/IAG5 to its management IP

**Limitation:** License required; boot times ~5–10 min per device; not available on every engineer's laptop.

### Option 4 — GNS3 / EVE-NG (multi-vendor, open source)

Open-source network emulators that support many vendor images via KVM/QEMU.

- **GNS3** — easier to set up on macOS/Linux; good for Cisco IOS images
- **EVE-NG** — better multi-vendor support; runs as a VM
- Requires importing legal device disk images (IOS, NX-OS, JunOS) — obtain from your network team

Once devices are booted, connect IAG4/IAG5 to their management IPs as you would for real devices.

**Limitation:** Requires vendor disk images; performance is lower than real hardware; some features not supported in emulation.

### Option 5 — netmiko test double (adapter logic only)

For testing adapter SSH command parsing without any device or IAG:

```python
# pip install netmiko
from unittest.mock import patch, MagicMock

with patch('netmiko.ConnectHandler') as mock_ssh:
    mock_conn = MagicMock()
    mock_conn.send_command.return_value = "Cisco IOS XR Software, Version 7.5.2"
    mock_ssh.return_value.__enter__ = lambda s: mock_conn
    # run your adapter test logic here
```

**Limitation:** Tests Python logic only — does not exercise IAG, adapter settings, or platform integration.

---

## Phase 5: GitLab Source Inspection

**When to run:**
- Phase 1 gather + sampleProperties compare did NOT resolve the root cause
- Error string in logs does not match any known pattern in the Quick Reference table
- Engineer asks to build reproduction steps or confirm expected adapter behavior
- Settings look correct but adapter is still OFFLINE or returning wrong data

**CRITICAL PRIVACY RULES — enforce for every step in this phase:**
- **Never save any code** (function bodies, imports, class definitions, file contents) to any file on disk
- **Never include code in Jira comments, engineer messages, or customer communications**
- **Never paste code excerpts into gather_report.md, diagnostic_report.md, or any output file**
- `sampleProperties.json` values (config schema, not code) **may** be saved and referenced — they are expected adapter configuration, appropriate to compare against live settings
- `error.json` error-code-to-description mappings **may** be saved (they are error catalog entries, not code)
- Everything else from adapter source files — analyze in memory only; record only the derived insight, not the source

---

### Step 5a — Resolve GitLab Repository

Derive the adapter's GitLab repo path from the package_id extracted in Step 1b:

```python
# Derive GitLab repo path from package_id
# @itentialopensource/adapter-servicenow → adapter-servicenow
import re
package_id = "{package_id}"   # e.g. @itentialopensource/adapter-servicenow
repo_name = package_id.split("/")[-1]   # adapter-servicenow
gitlab_base = f"https://gitlab.com/itentialopensource/adapters/{repo_name}"
api_base    = f"https://gitlab.com/api/v4/projects/itentialopensource%2Fadapters%2F{repo_name}"
print(f"GitLab repo: {gitlab_base}")
print(f"API base:    {api_base}")
```

Verify the repo exists:
```bash
curl -s "https://gitlab.com/api/v4/projects/itentialopensource%2Fadapters%2F{REPO_NAME}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'id' in d:
    print(f'✅ Found: {d[\"name\"]}  (id={d[\"id\"]}, default_branch={d[\"default_branch\"]})')
    print(f'   URL: {d[\"web_url\"]}')
    print(f'   Last activity: {d.get(\"last_activity_at\",\"unknown\")}')
else:
    print('❌ Repo not found — may be private or wrong package name')
    print(d)
"
```

Store the project `id` and `default_branch` for subsequent file fetches. Use `default_branch` (typically `master` or `main`) in all raw URL patterns.

---

### Step 5b — Fetch and Analyze `error.json`

`error.json` maps internal error codes to human-readable descriptions. Use it to map the customer's error string to a root cause. This file is **config/catalog data, not code** — its content may be referenced in analysis.

```bash
curl -s "https://gitlab.com/itentialopensource/adapters/{REPO_NAME}/-/raw/{DEFAULT_BRANCH}/error.json" \
  -o /tmp/{ADAPTER_NAME}_errors.json 2>/dev/null

python3 -c "
import json, sys

try:
    errors = json.load(open('/tmp/{ADAPTER_NAME}_errors.json'))
except Exception as e:
    print(f'error.json not available: {e}')
    sys.exit(0)

# Search for the customer's error term in the error catalog
search_terms = ['{ERROR_TERM}', '{SYMPTOM_KEYWORD}']   # fill from ticket_context
print(f'== error.json: {len(errors)} error codes defined ==')
matches = []
for code, entry in errors.items():
    haystack = json.dumps(entry).lower()
    if any(t.lower() in haystack for t in search_terms if t):
        matches.append((code, entry))

if matches:
    print(f'MATCHING error codes ({len(matches)} found):')
    for code, entry in matches:
        print(f'  {code}')
        print(f'    summary:     {entry.get(\"summary\",\"\")}')
        print(f'    description: {entry.get(\"description\",\"\")}')
        print(f'    category:    {entry.get(\"category\",\"\")}')
        print()
else:
    print('No matching error codes found for search terms')
    # List all category buckets to guide investigation
    cats = {}
    for code, entry in errors.items():
        c = entry.get('category','unknown')
        cats[c] = cats.get(c, 0) + 1
    print('Error categories in this adapter:', cats)
"
```

Save only the matched error-code entries (not the full file) to the gather report supplement — they are diagnostic catalog data, not code.

---

### Step 5c — Fetch and Analyze `package.json`

`package.json` reveals the adapter's declared version, Node.js engine requirement, and dependencies. Dependency versions can explain compatibility failures.

```bash
curl -s "https://gitlab.com/itentialopensource/adapters/{REPO_NAME}/-/raw/{DEFAULT_BRANCH}/package.json" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Name:     {d.get(\"name\")}')
print(f'Version:  {d.get(\"version\")}')
print(f'Engine:   {d.get(\"engines\",{}).get(\"node\",\"any\")}')
print(f'Main:     {d.get(\"main\",\"index.js\")}')
print()
print('Key dependencies:')
for dep, ver in (d.get('dependencies') or {}).items():
    if any(k in dep for k in ('axios','got','node-fetch','request','https','tls','ssl','oauth','jwt')):
        print(f'  {dep}: {ver}')
print()
print('Adapter entry point (for Step 5d):', d.get('main','adapter.js'))
" 2>/dev/null
```

**Do not save the full package.json.** Note the entry point filename for Step 5d.

---

### Step 5d — Fetch and Inspect Adapter Source (In-Memory Only)

Fetch the main adapter source file and inspect it entirely in memory. Extract only derived facts — authentication patterns, supported auth methods, required properties, connection logic. **Never write source code to any file or output.**

```bash
ENTRY_POINT="${ADAPTER_MAIN_FILE:-adapter.js}"   # from Step 5c

curl -s "https://gitlab.com/itentialopensource/adapters/{REPO_NAME}/-/raw/{DEFAULT_BRANCH}/${ENTRY_POINT}" \
  | python3 -c "
import sys, re

src = sys.stdin.read()
lines = src.splitlines()
total = len(lines)
print(f'Source loaded: {total} lines (not saved, not recorded)')
print()

# ── 1. Auth method detection ─────────────────────────────────────────────
auth_methods = re.findall(
    r'auth_method['\''\"]\s*[=:=!]+\s*['\''\"]([\w_]+)['\''\"]\s*[|)\]}]',
    src, re.IGNORECASE)
auth_switch  = re.findall(
    r'case\s+['\''\"]([\w_]+)[''\"].*?:',
    src)
all_am = sorted(set(auth_methods + auth_switch))
print(f'Detected auth_method values: {all_am}')

# ── 2. Required property detection ───────────────────────────────────────
req = re.findall(
    r'(?:required|mandatory)\s*[=:]+\s*(?:true|\[([^\]]+)\])',
    src, re.IGNORECASE)
req_fields = re.findall(
    r'if\s*\(!\s*(?:this\.)?(?:props?\.)?([a-zA-Z_][\w.]+)\s*\)',
    src)
print(f'Likely required properties: {sorted(set(req_fields))[:20]}')

# ── 3. Base path / endpoint patterns ─────────────────────────────────────
paths = re.findall(
    r'(?:base_path|basePath|baseUrl|url)\s*[+=]+\s*['\''\"](/[^\s'\''\";]+)['\''\"']',
    src)
print(f'API path patterns detected: {sorted(set(paths))[:10]}')

# ── 4. Token refresh pattern ──────────────────────────────────────────────
has_refresh = bool(re.search(r'refresh.?token|token.?refresh|getToken|renewToken', src, re.IGNORECASE))
print(f'Token refresh logic present: {has_refresh}')

# ── 5. SSL/TLS handling ───────────────────────────────────────────────────
ssl_patterns = re.findall(
    r'(?:rejectUnauthorized|accept_invalid_cert|tlsOptions|agentOptions)\s*[=:]+\s*(\S+)',
    src)
print(f'SSL/TLS options detected: {ssl_patterns}')

# ── 6. Suspicious patterns ────────────────────────────────────────────────
hard_timeout = re.findall(r'timeout\s*[=:]+\s*(\d+)', src)
if hard_timeout:
    print(f'Hardcoded timeouts (ms): {hard_timeout[:5]}')

swallowed = len(re.findall(r'catch\s*\([^)]*\)\s*\{[^}]*\}', src))
if swallowed > 3:
    print(f'Silent catch blocks: {swallowed} — errors may be swallowed without logging')

print()
print('Code inspection complete. No source recorded.')
"
```

Capture only the printed derived findings. Do not write the source file to disk, do not pipe it to a log file, do not include raw code in any output.

---

### Step 5e — Construct Reproduction Steps

Using the derived findings from Steps 5b–5d plus the live settings from Step 1b, generate concrete reproduction steps. These steps are safe to include in investigation notes and engineer communication — they describe configuration and workflow actions, not code.

```markdown
## Adapter Reproduction Steps — {ADAPTER_NAME}
**Generated from:** GitLab source analysis (Phase 5) + live settings (Phase 1)
**Adapter version:** {package version from Step 5c}
**Auth method (source):** {detected from Step 5d}

### Minimum Configuration to Reproduce
1. Deploy IAP {IAP_VERSION} with adapter package `{package_id}` installed
2. Configure adapter with these minimum properties:
   ```json
   {
     "host": "{customer host or placeholder}",
     "port": {port},
     "authentication": {
       "auth_method": "{detected auth_method}",
       "username": "{placeholder}",
       "password": "{placeholder}"
     },
     "ssl": { "enabled": {true|false} },
     "stub": false
   }
   ```
   ← Required properties identified from source analysis: {req_fields}

### Steps to Trigger the Issue
1. Start the adapter — confirm it reaches ONLINE state
2. Run a workflow task that calls `{affected endpoint or action}`
3. Expected: {correct behavior from docs/sampleProperties}
4. Actual (per ticket): {customer's observed error}

### Validation
- Check adapter logs for: {matched error codes from Step 5b}
- Compare token_timeout: {live value} vs expected: {sample value}
- Verify auth_method: {live value} vs detected in source: {Step 5d value}
```

**Do not include** code snippets, function names, internal class names, or file paths from the adapter source in reproduction steps sent to engineering or customers.

---

### Step 5f — Update Gather Report with Source Findings

Append to `{project_path}/data/{TIMESTAMP}/{TICKET_KEY}/gather_report.md`:

```markdown
## Phase 5 — GitLab Source Analysis
**Repo:** https://gitlab.com/itentialopensource/adapters/{REPO_NAME}
**Version inspected:** {package.json version}
**Analysis timestamp:** {YYYY-MM-DD HH:MM UTC}

### Auth Method Support (from source)
Detected supported auth_method values: {list from Step 5d}
Live adapter auth_method: {from Step 1b}
→ {match / mismatch / unsupported value}

### Error Code Match (from error.json)
{Matched error code entries — icode, summary, category only}
→ Root cause signal: {derived conclusion}

### Required Properties (from source)
Identified required fields: {list from Step 5d}
Missing in live config: {cross-referenced against Step 1b settings}

### Source Flags
- Token refresh logic: {yes/no}
- Silent catch blocks: {count — if > 3, note as potential log-suppression risk}
- Hardcoded timeouts: {list, if any}
- SSL/TLS options: {detected values}

### Reproduction Steps
{Content from Step 5e — no code, configuration and actions only}
```

**Remind:** No source code is written to this file. Only the derived findings above.

---

### Step 5g — Adapter Search by Name (When Repo Not Found)

Use when Step 5a returns "❌ Repo not found" or when the adapter's package_id does not cleanly map to a GitLab repo name (unusual naming, third-party adapters, whitelabel packages).

```bash
# Search the itentialopensource/adapters group by keyword
SEARCH_QUERY=$(python3 -c "
s = '{ADAPTER_NAME_OR_KEYWORD}'.lower()
# strip common prefixes for better search results
for prefix in ['adapter-', '@itentialopensource/', 'itential-']:
    s = s.replace(prefix, '')
print(s)
")
echo "Searching for: ${SEARCH_QUERY}"
curl -s "https://gitlab.com/api/v4/groups/itentialopensource%2Fadapters/projects?search=${SEARCH_QUERY}&per_page=10" \
  | python3 -c "
import sys, json
projects = json.load(sys.stdin)
if not projects:
    print('No matching adapter repos found.')
else:
    for i, p in enumerate(projects):
        print(f'  [{i+1}] {p[\"name\"]}  (branch={p[\"default_branch\"]}, updated={p[\"last_activity_at\"][:10]})')
        print(f'       {p[\"description\"] or \"(no description)\"}')
        print(f'       {p[\"web_url\"]}')
"
```

Present the numbered list to the engineer. Ask them to confirm which repo matches. Once selected:
- Store the project's `id` as `ADAPTER_PROJECT_ID`
- Store `default_branch` as `ADAPTER_DEFAULT_BRANCH`
- Re-derive `REPO_NAME` from the project's `path` field
- Continue with Step 5b using the confirmed repo

---

### Step 5h — Directory Browse & File Inspection

Use after Step 5a or 5g when the root cause may be in a helper or utility file not captured by the main entry point analysis in Step 5d. Also useful when the engineer wants to explore the adapter's test fixtures or configuration samples.

```bash
# List the adapter's full file structure
curl -s "https://gitlab.com/api/v4/projects/${ADAPTER_PROJECT_ID}/repository/tree?ref=${ADAPTER_DEFAULT_BRANCH}&recursive=true&per_page=100" \
  | python3 -c "
import sys, json
files = json.load(sys.stdin)
# Group by directory
dirs = {}
for f in files:
    if f['type'] == 'blob':
        d = f['path'].rsplit('/', 1)[0] if '/' in f['path'] else '.'
        dirs.setdefault(d, []).append(f['path'].rsplit('/', 1)[-1])
for d in sorted(dirs):
    print(f'  {d}/')
    for fn in sorted(dirs[d]):
        print(f'    {fn}')
"
```

Identify files that may be relevant (auth helpers, request utilities, error handlers). Ask the engineer: "Would you like me to inspect any of these files?" For each file the engineer requests:

```bash
# Fetch and analyze a specific file (in-memory only — apply same privacy rules as Step 5d)
FILE_PATH="helpers/authentication.js"   # or whichever file was requested
curl -s "https://gitlab.com/itentialopensource/adapters/${REPO_NAME}/-/raw/${ADAPTER_DEFAULT_BRANCH}/${FILE_PATH}" \
  | python3 -c "
import sys, re
src = sys.stdin.read()
# Apply same extraction logic as Step 5d: auth patterns, required props, timeouts, SSL options
# RECORD derived findings only — do not print raw source
auth_patterns   = re.findall(r'auth_method[\"\']\s*:\s*[\"\'](.*?)[\"\'|,]', src)
required_props  = re.findall(r'if\s*\(!this\.props\.(\w+)\)', src)
timeout_values  = re.findall(r'timeout\s*[:=]\s*(\d+)', src)
tls_flags       = re.findall(r'(rejectUnauthorized|strictSSL|ca:|cert:)[^;]{0,40}', src)
silent_catches  = src.count('catch') - src.count('catch.*console') if 'catch' in src else 0
print('File:', '${FILE_PATH}')
print('Auth patterns detected:', auth_patterns or 'none')
print('Required props found:', required_props or 'none')
print('Timeout values:', timeout_values or 'none')
print('TLS/SSL flags:', tls_flags or 'none')
print('catch blocks without logging:', silent_catches)
"
```

Same rule applies: **no file content is saved or included in any report**. Only the derived findings (patterns detected, required props, flags) are recorded.

---

## Phase 6: Adapter Installation & Sample Instance Creation

**When to invoke:** Engineer explicitly requests "install this adapter", "create a sample instance", or "set up adapter X for testing". Also runs when Phase 1 shows the adapter is absent from `/health/adapters`.

**Safety rules:**
- Any POST to IAP (creating a new adapter instance) requires showing the full request body to the engineer and waiting for explicit "yes" before sending
- Never embed actual passwords, tokens, or secrets in the configuration — use `<YOUR_SECRET_HERE>` placeholders
- All npm install commands must be shown and approved before running via SSH

---

### Step 6a — Identify Adapter Package

Run Step 5a (or 5g) to resolve the GitLab repo. Then fetch package.json to extract the npm package name and version:

```bash
curl -s "https://gitlab.com/itentialopensource/adapters/${REPO_NAME}/-/raw/${ADAPTER_DEFAULT_BRANCH}/package.json" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('npm package name:', d.get('name'))
print('Current version:', d.get('version'))
print('Required Node.js:', d.get('engines', {}).get('node', 'not specified'))
"
```

Check if the adapter is already installed:
```bash
curl -s "${PLATFORM_URL}/health/adapters?token=${TOKEN}" \
  | python3 -c "
import sys, json
adapters = json.load(sys.stdin)
for a in adapters:
    name = a.get('id','')
    if '{ADAPTER_KEYWORD}' in name.lower():
        print(f'  Already installed: {name}  state={a[\"state\"]}  connection={a[\"connection\"]}')
"
```

If already installed: confirm with engineer whether to proceed with configuration only (skip to Step 6b) or upgrade.

---

### Step 6b — Generate Sample Adapter Configuration

Build the configuration from three layers, in order:

**Layer 1 — Extract from ticket_context.md (automatic):**

Read `{project_path}/data/{TIMESTAMP}/{TICKET_KEY}/ticket_context.md` and extract:
- `host` or target system hostname/IP (look for connection strings, error messages with hostnames)
- `port` (look for port references in error messages or description)
- `auth_method` (infer from ticket keywords: "401" → basic/oauth2, "token" → token auth, "API key" → header-based)
- `protocol` (infer from "SSL error" → https, "connection refused 80" → http)
- `base_path` or API root path (look for endpoint references like "/api/v2/")

```python
import re, sys

ticket_context = open('{project_path}/data/{TIMESTAMP}/{TICKET_KEY}/ticket_context.md').read()

# Extract hints
host_match    = re.search(r'(?:host|endpoint|server)[:\s]+([a-zA-Z0-9._-]+\.[a-zA-Z]{2,})', ticket_context, re.I)
port_match    = re.search(r':(\d{2,5})\b', ticket_context)
auth_hint     = 'oauth2' if re.search(r'oauth|client.?id|access.?token', ticket_context, re.I) else \
                'basic'  if re.search(r'username|password|basic.?auth', ticket_context, re.I) else \
                'token'  if re.search(r'api.?key|bearer.?token|x-api-key', ticket_context, re.I) else 'unknown'
protocol_hint = 'https' if re.search(r'https|ssl|tls|443', ticket_context, re.I) else 'http'

extracted = {
    'host':        host_match.group(1) if host_match else None,
    'port':        int(port_match.group(1)) if port_match else (443 if protocol_hint=='https' else 80),
    'auth_method': auth_hint,
    'protocol':    protocol_hint,
}
print('Extracted from ticket:', extracted)
```

**Layer 2 — Prompt engineer for missing required fields:**

After Layer 1, identify which required fields are still missing. Ask the engineer in a single grouped prompt (do NOT ask one field at a time):

```
Building sample adapter configuration for {ADAPTER_NAME}.

Extracted from ticket:
  • host:        {extracted_host or "?"}
  • port:        {extracted_port or "?"}
  • auth_method: {inferred_auth or "?"}
  • protocol:    {inferred_protocol or "?"}

I need a few more values. Please confirm or correct:
  • host: {extracted_host} — correct, or enter the actual host?
  • port: {extracted_port} — correct, or enter the actual port?
  • auth_method: {inferred_auth} — correct? (options: basic | oauth2 | token | custom)
  • protocol: https — correct?
  • base_path: (leave blank to use sampleProperties default)
  • instance id: what should this adapter instance be called? (e.g. adapter-servicenow-test)

Note: Do NOT provide actual passwords, tokens, or secrets.
Those fields will be marked <YOUR_SECRET_HERE> — fill them in the IAP UI after creation.
```

**Layer 3 — Build and display the complete configuration:**

Merge: Layer 1 (auto-extracted) + Layer 2 (engineer-supplied) + sampleProperties defaults (for optional fields engineer didn't specify). Replace all credential fields with `<YOUR_SECRET_HERE>` placeholders.

```python
import json

# Load sampleProperties as the base template
sample = json.load(open('/tmp/{ADAPTER_NAME}_sample.json'))

# Override with extracted and engineer-supplied values
sample['id'] = '{ENGINEER_SUPPLIED_ID}'
if '{EXTRACTED_HOST}': sample['host'] = '{EXTRACTED_HOST}'
if '{EXTRACTED_PORT}': sample['port'] = {EXTRACTED_PORT}
sample['protocol'] = '{PROTOCOL}'
sample['stub'] = False   # always disable stub for real testing

# Replace credential fields with placeholders
def mask_credentials(obj, depth=0):
    if depth > 5: return obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            if any(x in k.lower() for x in ['password', 'secret', 'token', 'key', 'credential', 'client_secret']):
                obj[k] = '<YOUR_SECRET_HERE>'
            else:
                mask_credentials(v, depth+1)
    elif isinstance(obj, list):
        for item in obj: mask_credentials(item, depth+1)
    return obj

config = mask_credentials(sample)
print(json.dumps(config, indent=2))
```

Show the complete JSON to the engineer. They may correct any field before approving. Auth credential placeholders are clearly marked — the engineer fills real values in the IAP UI after creation.

---

### Step 6c — Adapter Installation (If Not Already Installed)

**Prerequisite check:** Is SSH access available to the IAP server? Check `.env` for `SSH_HOST_N` with role `iap`.

**If no SSH access available:**
Present the full sequence of manual commands below and ask the engineer to run them on the IAP server, then return here after completion for the restart gate (Step 6c-iii).

---

#### Step 6c-i — Stage adapter package files

> **No engineer consent required for this step** — writing files to disk does not make
> the adapter live. Consent gates come at Step 6c-iii (platform restart) and Step 6d
> (instance creation), enforced by `bash-safety-guard.py`.

Itential Platform stores each adapter as its own directory under
`/opt/itential/platform/services/` (VM deployments) or equivalent for Docker/K8s.
This is separate from `node_modules/` — adapters are service directories, not
npm dependencies of the platform process itself.

**Option A — npm pack + extract (preferred for any deployment):**
```bash
ADAPTER_NAME=adapter-{name}                        # e.g. adapter-servicenow
PACKAGE_ID=@itentialopensource/adapter-{name}      # e.g. @itentialopensource/adapter-servicenow
VERSION=$(npm show ${PACKAGE_ID} dist-tags.latest 2>/dev/null || echo "{VERSION_FROM_STEP_6A}")

# On the IAP server via SSH:
mkdir -p /tmp/adapter-install-work && cd /tmp/adapter-install-work
npm pack ${PACKAGE_ID}@${VERSION}

sudo mkdir -p /opt/itential/platform/services/${ADAPTER_NAME}
sudo tar -xzf ${PACKAGE_ID##*/}-${VERSION}.tgz \
  -C /opt/itential/platform/services/${ADAPTER_NAME} --strip-components=1

cd /opt/itential/platform/services/${ADAPTER_NAME}
sudo -u itential npm install --omit=dev --no-audit --no-fund
sudo chown -R itential:itential /opt/itential/platform/services/${ADAPTER_NAME}

rm -rf /tmp/adapter-install-work
```

**Option B — Git clone + transfer (if server has no npm registry access):**
```bash
# On the engineer's machine (local):
git clone https://gitlab.com/itentialopensource/adapters/adapter-{name}.git /tmp/adapter-{name}
cd /tmp/adapter-{name} && npm install --omit=dev
tar czf /tmp/adapter-{name}.tar.gz -C /tmp adapter-{name}

# Transfer to IAP server:
scp /tmp/adapter-{name}.tar.gz {SSH_USER}@{SSH_HOST}:/tmp/

# On the IAP server via SSH:
sudo mkdir -p /opt/itential/platform/services/adapter-{name}
sudo tar -xzf /tmp/adapter-{name}.tar.gz \
  -C /opt/itential/platform/services/adapter-{name} --strip-components=1
sudo chown -R itential:itential /opt/itential/platform/services/adapter-{name}
```

**Option C — Docker / Kubernetes (if platform runs containerised):**
```bash
# Docker:
docker exec iap-app bash -c \
  "npm pack @itentialopensource/adapter-{name}@{VERSION} && \
   mkdir -p /opt/itential/platform/services/adapter-{name} && \
   tar -xzf adapter-{name}-{VERSION}.tgz -C /opt/itential/platform/services/adapter-{name} --strip-components=1 && \
   cd /opt/itential/platform/services/adapter-{name} && npm install --omit=dev"

# Kubernetes:
IAP_POD=$(kubectl get pods -n {KUBE_NAMESPACE} -l app=iap -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n {KUBE_NAMESPACE} ${IAP_POD} -- bash -c "... same as Docker above ..."
```

After staging, verify the package is in place before proceeding to the restart:
```bash
ssh {SSH_USER}@{SSH_HOST} \
  "ls /opt/itential/platform/services/adapter-{name}/package.json" \
  && echo "PASS: package staged" || echo "FAIL: package missing — check install"
```

---

#### Step 6c-ii — Verify adapter is visible to platform (pre-restart check)

Before restarting, confirm the package directory structure looks correct:
```bash
ssh {SSH_USER}@{SSH_HOST} \
  "find \$(find /opt /usr/src -type d -name '@itentialopensource' 2>/dev/null | head -1)/{NPM_PACKAGE_BASENAME} \
   -maxdepth 1 -name 'package.json' -o -name '*.js' | head -10"
```

Expect to see `package.json` and at least one `.js` file. If the directory is empty or missing `package.json`, the install did not complete — resolve before restarting the platform.

---

#### Step 6c-iii — Platform restart (required; explicit permission gate)

IAP must be restarted to load the newly installed adapter package. **This causes a brief
service interruption.** Present the following confirmation and wait for explicit "yes":

```
The adapter package has been staged. IAP must be restarted to load the new adapter model.

  Adapter staged    : adapter-{name}@{VERSION}
  Server            : {SSH_HOST_N}
  Restart method    : {systemctl restart itential-platform / docker restart iap-app / kubectl rollout restart ...}
  Expected downtime : ~60–120 seconds

⚠️  This will interrupt any running workflows or active adapter connections.

Shall I restart the IAP platform now? (yes / no)
```

Only restart after explicit "yes". The restart command must be prefixed with
`RESTART_APPROVED=yes` — this is the bypass marker checked by
`.claude/hooks/bash-safety-guard.py`. Never add this prefix without a clear "yes" from
the engineer in the current conversation.

```bash
# VM / bare-metal (systemd):
RESTART_APPROVED=yes ssh {SSH_USER}@{SSH_HOST} "sudo systemctl restart itential-platform"

# Docker:
RESTART_APPROVED=yes docker restart iap-app

# Kubernetes (rolling restart — zero-downtime if replicas > 1):
RESTART_APPROVED=yes kubectl rollout restart deployment/iap -n {KUBE_NAMESPACE}
```

**Wait for platform to come back up** — poll `/health` up to 5 times (25s total):
```bash
for i in $(seq 1 5); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${PLATFORM_URL}/health" 2>/dev/null)
  if [ "${STATUS}" = "200" ]; then
    echo "Platform is up (attempt ${i}/5)"
    break
  fi
  echo "Waiting... (attempt ${i}/5, status=${STATUS})"
  sleep 5
done
if [ "${STATUS}" != "200" ]; then
  echo "WARN: platform did not respond after 5 attempts — check server logs before proceeding"
fi
```

After platform is up, confirm the new adapter is now visible:
```bash
curl -s "${PLATFORM_URL}/health/adapters?token=${TOKEN}" \
  | python3 -c "
import sys, json
adapters = json.load(sys.stdin)
names = [a.get('id','') for a in adapters]
matches = [n for n in names if '{ADAPTER_KEYWORD}' in n.lower()]
if matches:
    print('Adapter package loaded by platform:', matches)
else:
    print('Adapter not yet visible in /health/adapters — it will appear after Step 6d instance creation')
"
```

> If the adapter package is absent from `/health/adapters` even after restart, check
> that the install path matches where IAP loads adapters from. The platform only loads
> packages it finds in its configured `node_modules/@itentialopensource/` path.

---

### Step 6d — Create Adapter Instance

> **Use `POST /adapters/import` (importAdapter), not `POST /adapters` (createAdapter).**
>
> `POST /adapters` rejects any adapter whose `pronghorn.json` declares no `brokers` array
> — common for pure REST and custom-method adapters (e.g. `adapter-aws_s3`,
> `adapter-aws_cloudformation`, `adapter-servicenow`) — returning:
> `"Currently this feature is not supported for services of type: {type}."`
> in ~2ms with no server-side log entry. `POST /adapters/import` accepts the **identical**
> request schema with no such restriction. Default to `importAdapter` for any adapter
> you haven't confirmed declares `brokers` in its `pronghorn.json`.

**Show the complete request to the engineer before sending:**

```
I'll create the adapter instance with this request:

POST {PLATFORM_URL}/adapters/import?token=****
Content-Type: application/json
Body:
{FULL_CONFIGURATION_JSON_FROM_STEP_6B}

This will register the adapter instance in IAP (state will be DEAD until started).
Shall I proceed? (yes / no)
```

Wait for explicit "yes". The POST must be prefixed with `ADAPTER_CREATE_APPROVED=yes` —
the bypass marker checked by `.claude/hooks/bash-safety-guard.py`. Never add this prefix
without a clear "yes" from the engineer in the current conversation.

```bash
ADAPTER_CREATE_APPROVED=yes curl -s -X POST \
  "${PLATFORM_URL}/adapters/import?token=${TOKEN}" \
  -H "Content-Type: application/json" \
  -d @/tmp/{ADAPTER_NAME}_new_config.json
```

**Expected state after creation: `DEAD` / `OFFLINE`** — this is normal. A freshly
created instance has not been started yet. Starting it is a separate explicit action:
present the start plan and get engineer consent before running
`PUT /adapters/{instance_id}/start` (also gated by the existing PUT consent rule in
`bash-safety-guard.py`).

If the POST returns 404 → the `/adapters/import` endpoint is not available in this IAP
version. Present the configuration JSON for manual entry via IAP Admin UI → Adapters →
Add and note the missing endpoint for ENG investigation.

---

### Step 6e — Verify New Instance

```bash
# Confirm instance appears in the adapters list
curl -s "${PLATFORM_URL}/adapters?token=${TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
matches = [a['name'] for a in d.get('data', []) if '{INSTANCE_NAME}' in a.get('name','')]
print('Registered instances:', matches if matches else 'NOT FOUND')
"

# Check health state (expect DEAD/OFFLINE until started)
curl -s "${PLATFORM_URL}/health/adapters?token=${TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for a in d.get('results', []):
    if '{INSTANCE_NAME}' in a.get('id', ''):
        print(f'{a[\"id\"]}: state={a[\"state\"]}  connection={a.get(\"connection\",{}).get(\"state\",\"?\")}'  )
"
```

- **`DEAD` / `OFFLINE`** — expected on a new instance. Remind the engineer to:
  1. Fill in `<YOUR_SECRET_HERE>` credential fields via IAP Admin UI → Adapters → {instance id} → Edit
  2. Start the adapter with explicit consent: `PUT /adapters/{instance_id}/start`
- **Instance not found** → the import POST may have failed silently — check the response body from Step 6d
- **`RUNNING` / `OFFLINE`** (started but can't connect) → proceed directly to Phase 1d (settings comparison)

