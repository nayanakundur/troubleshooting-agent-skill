---
name: troubleshoot-logs
description: Collect and analyze logs from IAP, IAG, MongoDB, Redis, and load balancers. Supports Docker, SSH/VM, Kubernetes/EKS, and AWS CloudWatch. Filters by incident time, reports error patterns, and masks sensitive values.
argument-hint: "[component: iap|iag|mongodb|redis|lb|all] [incident time]"
---

# Troubleshoot Logs

**Owns:** Log collection and error analysis across all Itential platform components. Covers IAP application logs, webserver access logs, IAG logs, MongoDB `mongod.log`, Redis logs, and load balancer access logs. Supports Docker, SSH multi-host, Kubernetes/EKS, and CloudWatch.
**Use when:** Investigating any incident that requires log evidence — error patterns, slow request analysis, auth failure storms, container crash context, or support escalation artifact collection.

---

## CRITICAL SAFETY RULES

- **Read-only** — log collection only; no file deletion, no log rotation, no service restarts
- **Mask sensitive values** — tokens, passwords, and API keys in log output: show first 6 + last 4 characters only (e.g., `abc123...xyz9`)
- **Env file discovery** — before sourcing any `.env`, run the project-wide discovery (current folder, `environments/`, `repro/`, all subdirectories). If `.auth.json` already has a valid token from the orchestrator's Step 3a, reuse it. If not, present the discovered files and let the engineer choose (or mix variables from different files). Never assume the root `.env` is the only source. See `/troubleshoot` Step 3a for the full interactive flow.

---

## Phase 1: Triage — What Logs Are Needed?

Before collecting, ask (or infer from context):
- **What component is suspect?** IAP, IAG, MongoDB, Redis, LB, or all?
- **What is the incident time?** (date + approximate time + timezone)
- **What identifier is known?** Job ID, workflow name, service name, adapter name, error string
- **What is the deployment type?** Docker, VM/SSH, Kubernetes/EKS
- **Was a log/error attachment already parsed by `/troubleshoot-triage`?** If `{REPORTED_ERROR_TIME}` and `{ATTACHED_LOG_PATH}` were handed off from Phase 0's Step 1a-attach, use them — they're an exact, evidence-backed anchor, not a customer's rough estimate. See Step 1a below.

Set:
- `{INCIDENT_TIME}` = `{REPORTED_ERROR_TIME}` if provided by the caller, else the customer/engineer-stated incident time (e.g., `2026-04-18T10:30:00`)
- `{INCIDENT_DATE}` = date portion (e.g., `2026-04-18`)
- `{WINDOW_BEFORE}` = 30 minutes before incident (initial pass — see Step 1b for the staged backward lookback used when root cause isn't found in this window)
- `{WINDOW_AFTER}` = 30 minutes after incident

```bash
mkdir -p {project_path}/data/{TIMESTAMP}/
```

### Step 1a — Use an Attached Log/Error as the Search Anchor (if provided)

If the caller passed `{ATTACHED_LOG_PATH}` (a customer-provided log excerpt or error dump from the ISD ticket, downloaded and parsed in `/troubleshoot-triage` Step 1a-attach), do not treat it as read-once evidence — actively correlate it against the platform logs you're about to collect:

1. Copy it into this investigation's working directory so both are side by side:
   ```bash
   mkdir -p {project_path}/data/{TIMESTAMP}/customer_attachment/
   cp {ATTACHED_LOG_PATH} {project_path}/data/{TIMESTAMP}/customer_attachment/
   ```
2. Extract the exact error string/stack trace text around `{REPORTED_ERROR_TIME}` from the attachment — this is what you'll grep for verbatim in the platform's own logs in Phase 8, to find the matching entry (which usually has more surrounding context — thread IDs, correlation/job IDs, adjacent service logs — than the customer's excerpt alone).
3. Treat `{REPORTED_ERROR_TIME}` as the **end** of the search window, not the middle. A customer-reported error is a symptom; root cause commonly logs *before* it. Use the staged backward lookback in Step 1b instead of a flat symmetric ±30 min window.

### Step 1b — Staged Backward Lookback for Root Cause

When investigating a reported error (from an attachment or a stated incident time), don't stop at a single ±30 min window — root cause frequently precedes the visible symptom by more than that (e.g., a connection drop, a worker being disabled, a queue backing up hours earlier). Search backward in stages, widening only if the narrower window doesn't explain the symptom:

| Stage | Window searched | When to use |
|-------|-----------------|-------------|
| 1 | `{INCIDENT_TIME}` − 30 min → `{INCIDENT_TIME}` | Always run first — matches most transient errors (bad request, one-off timeout) |
| 2 | `{INCIDENT_TIME}` − 2 hours → `{INCIDENT_TIME}` − 30 min | Run if Stage 1 shows the error but no precursor (e.g., no preceding connection/auth failure) |
| 3 | `{INCIDENT_TIME}` − 24 hours → `{INCIDENT_TIME}` − 2 hours, or since last process/container restart if known | Run if Stage 2 is also clean — looking for a configuration change, restart, or a slow-building resource exhaustion (disk, memory, connection pool) |

Apply each stage using the same collection commands in Phases 2–7, just with `{WINDOW_BEFORE}` widened per stage (e.g., substitute `date -d "{INCIDENT_TIME} - 2 hours"` for the grep/date-range boundary instead of `- 30 minutes`). Stop widening as soon as you find a precursor event that plausibly explains the symptom — don't blindly run all 3 stages every time.

**Report which stage found the answer** in Phase 10's Correlation Summary — this tells the engineer how far back the real cause was hiding, which is itself diagnostic (a Stage 3 finding usually means a config/restart is the culprit, not a transient network blip).

---

## Phase 2: IAP Application Logs

### Step 2a — Discover Log Paths from App Properties

```bash
curl -sk "{PLATFORM_URL}/health/applications?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for a in d.get('results',[]):
    props = a.get('properties',{}).get('properties',{})
    log_dir  = props.get('log_directory','') or props.get('logDirectory','')
    log_file = props.get('log_filename','') or props.get('logFilename','')
    lvl      = props.get('console_level','?')
    if log_dir or log_file:
        flag = '⚠️ HIGH verbosity' if lvl in ('spam','debug','trace') else ''
        print(f'{a[\"id\"]}: {log_dir}/{log_file}  [{lvl}]  {flag}')
"
```

**Fallback — confirmed authoritative source on platforms where `/health/applications` doesn't expose `properties.properties.log_directory`** (observed on 6.5.x): use `GET /server/config` instead, which returns a flat list of `{name, origin, value}` config entries including the real on-disk paths:

```bash
curl -sk -H "Authorization: Bearer {TOKEN}" "{PLATFORM_URL}/server/config" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
cfg = {e['name']: e['value'] for e in d if isinstance(e, dict) and 'name' in e}
print('log_directory:', cfg.get('log_directory'))
print('log_filename:', cfg.get('log_filename'))
print('webserver_log_directory:', cfg.get('webserver_log_directory'))
print('webserver_log_filename:', cfg.get('webserver_log_filename'))
"
```

Note: on platforms using this config style, `log_filename` (e.g. `platform.log`) is typically a **single combined log for all apps and adapters** — there is no separate per-app/per-adapter log file. Don't go hunting for an app-specific file that doesn't exist; grep the combined file for the app/adapter name instead.

Default location: `/var/log/itential/`

### Step 2b — Collect IAP Logs (Docker)

```bash
# All IAP log files (rotated logs included)
docker exec platform find /var/log/itential -name "*.log" 2>/dev/null

# Collect and filter to incident window
docker exec platform grep -h "" /var/log/itential/*.log 2>/dev/null \
  | grep "{INCIDENT_DATE}" \
  > {project_path}/data/{TIMESTAMP}/iap_logs_raw.txt

echo "IAP log lines collected: $(wc -l < {project_path}/data/{TIMESTAMP}/iap_logs_raw.txt)"

# Filter for errors in window
grep -i "error\|ERROR\|WARN\|warn\|failed\|exception\|timeout" \
  {project_path}/data/{TIMESTAMP}/iap_logs_raw.txt \
  > {project_path}/data/{TIMESTAMP}/iap_logs_errors.txt

echo "Error lines: $(wc -l < {project_path}/data/{TIMESTAMP}/iap_logs_errors.txt)"
```

### Step 2c — Collect IAP Logs (SSH / VM)

```bash
# Collect from all hosts with SSH_ROLE_N=iap in parallel
for i in $(seq 1 20); do
  ROLE=$(grep "^SSH_ROLE_${i}=" {project_path}/.env 2>/dev/null | cut -d= -f2-)
  [ "$ROLE" != "iap" ] && continue
  HOST=$(grep "^SSH_HOST_${i}=" {project_path}/.env | cut -d= -f2-)
  USER=$(grep "^SSH_USER_${i}=" {project_path}/.env | cut -d= -f2- || echo ec2-user)
  KEY=$(grep "^SSH_KEY_PATH_${i}=" {project_path}/.env | cut -d= -f2- || echo ~/.ssh/id_rsa)
  PORT=$(grep "^SSH_PORT_${i}=" {project_path}/.env | cut -d= -f2- || echo 22)
  LABEL=$(grep "^SSH_LABEL_${i}=" {project_path}/.env | cut -d= -f2- || echo "iap-${i}")
  echo "Collecting IAP logs from $LABEL ($HOST)..."
  ssh -i "$KEY" -p "$PORT" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${USER}@${HOST}" \
    "sudo grep -h '' /var/log/itential/*.log 2>/dev/null \
     | grep '{INCIDENT_DATE}' \
     | grep -i 'error\|ERROR\|WARN\|failed\|exception' | head -500" \
    > {project_path}/data/{TIMESTAMP}/iap_logs_${LABEL}.txt 2>&1 &
done
wait
cat {project_path}/data/{TIMESTAMP}/iap_logs_*.txt \
  > {project_path}/data/{TIMESTAMP}/iap_logs_raw.txt 2>/dev/null
```

### Step 2d — Collect IAP Logs (Kubernetes / EKS)

```bash
# Using kubectl (requires KUBE_NAMESPACE)
kubectl logs -n {KUBE_NAMESPACE} deployment/iap --since=2h 2>&1 \
  | grep -i "error\|WARN\|{WORKFLOW_NAME}\|{ADAPTER_NAME}" \
  | head -1000 > {project_path}/data/{TIMESTAMP}/iap_k8s_logs.txt

# Using stern (if installed: brew install stern)
timeout 30 stern -n {KUBE_NAMESPACE} {KUBE_POD_PATTERN} --since 2h --no-follow 2>&1 \
  | grep -i "error\|warn\|{INCIDENT_DATE}" \
  | head -1000 > {project_path}/data/{TIMESTAMP}/iap_stern_logs.txt
```

### Step 2e — Collect IAP Logs (AWS CloudWatch)

```bash
# Requires AWS_ACCESS_KEY_ID and AWS_REGION in .env
aws logs filter-log-events \
  --log-group-name "/itential/platform" \
  --start-time $(date -d "{INCIDENT_TIME} -30min" +%s000 2>/dev/null) \
  --end-time   $(date -d "{INCIDENT_TIME} +30min" +%s000 2>/dev/null) \
  --filter-pattern "ERROR" \
  --region {AWS_REGION} \
  --output json 2>/dev/null \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for e in d.get('events',[]):
    print(e.get('timestamp'), e.get('message','')[:200])
" | head -500 > {project_path}/data/{TIMESTAMP}/iap_cloudwatch_logs.txt
```

---

## Phase 3: Webserver Access Log (IAP HTTP)

`webserver.log` records every HTTP request: method, URL, status code, response time, client IP. Critical for slow UI, auth failure storms, and error rate analysis.

```bash
WEBSERVER_LOG=${WEBSERVER_LOG_PATH:-/var/log/itential/webserver.log}

# Docker
docker exec platform cat "${WEBSERVER_LOG}" 2>/dev/null \
  > {project_path}/data/{TIMESTAMP}/webserver_raw.txt

# SSH / VM
for i in $(seq 1 20); do
  ROLE=$(grep "^SSH_ROLE_${i}=" {project_path}/.env 2>/dev/null | cut -d= -f2-)
  [ "$ROLE" != "iap" ] && continue
  HOST=$(grep "^SSH_HOST_${i}=" {project_path}/.env | cut -d= -f2-)
  USER=$(grep "^SSH_USER_${i}=" {project_path}/.env | cut -d= -f2- || echo ec2-user)
  KEY=$(grep "^SSH_KEY_PATH_${i}=" {project_path}/.env | cut -d= -f2- || echo ~/.ssh/id_rsa)
  LABEL=$(grep "^SSH_LABEL_${i}=" {project_path}/.env | cut -d= -f2- || echo "iap-${i}")
  ssh -i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${USER}@${HOST}" \
    "sudo cat \${WEBSERVER_LOG_PATH:-/var/log/itential/webserver.log} 2>/dev/null \
     || sudo cat /home/pronghorn/logs/webserver.log 2>/dev/null" \
    > {project_path}/data/{TIMESTAMP}/webserver_raw_${LABEL}.txt 2>&1 &
done
wait
cat {project_path}/data/{TIMESTAMP}/webserver_raw_*.txt \
  > {project_path}/data/{TIMESTAMP}/webserver_raw.txt 2>/dev/null

# Kubernetes
kubectl exec -n {KUBE_NAMESPACE} deployment/iap -- \
  cat /var/log/itential/webserver.log 2>/dev/null \
  > {project_path}/data/{TIMESTAMP}/webserver_raw.txt
```

### Analyze Webserver Log

```bash
# Filter to incident window
grep "{INCIDENT_DATE}" {project_path}/data/{TIMESTAMP}/webserver_raw.txt \
  > {project_path}/data/{TIMESTAMP}/webserver_window.txt

# HTTP errors (4xx / 5xx)
grep -E '" [45][0-9]{2} ' {project_path}/data/{TIMESTAMP}/webserver_window.txt \
  | sort | uniq -c | sort -rn | head -30

# Slowest requests (JSON log format)
python3 -c "
import json
lines = open('{project_path}/data/{TIMESTAMP}/webserver_window.txt').readlines()
reqs = []
for ln in lines:
    try:
        r = json.loads(ln)
        rt = float(r.get('responseTime') or r.get('response_time') or r.get('duration') or 0)
        reqs.append((rt, r.get('method',''), r.get('url') or r.get('path',''), r.get('status','')))
    except: pass
for rt, m, u, s in sorted(reqs, reverse=True)[:20]:
    flag = '⚠️' if rt > 2000 else ''
    print(f'{rt:>8.0f}ms  {s}  {m}  {u[:80]}  {flag}')
" 2>/dev/null

# Top endpoints by request count
python3 -c "
import json, collections
lines = open('{project_path}/data/{TIMESTAMP}/webserver_window.txt').readlines()
paths = collections.Counter()
for ln in lines:
    try:
        r = json.loads(ln)
        paths[(r.get('method','?'), (r.get('url') or r.get('path','?'))[:60])] += 1
    except: pass
for (m,p),c in paths.most_common(15):
    print(f'{c:>6}x  {m}  {p}')
" 2>/dev/null

# Auth failures (401/403)
echo "401 responses: $(grep -c '\" 401 ' {project_path}/data/{TIMESTAMP}/webserver_window.txt 2>/dev/null)"
echo "403 responses: $(grep -c '\" 403 ' {project_path}/data/{TIMESTAMP}/webserver_window.txt 2>/dev/null)"
```

**Key patterns:**
| Pattern | Meaning |
|---------|---------|
| Spike in 5xx responses | IAP process error — correlate with app logs |
| Many 401 responses | Auth token expiry storm |
| Response time > 5s on `/operations-manager/jobs` | MongoDB slow query on jobs collection |
| Sudden drop in request count | IAP crashed or LB stopped routing |

---

## Phase 4: IAG Logs

### Step 4a — IAG Container Logs (Docker)

```bash
IAG_CONTAINER="${IAG_CONTAINER_NAME:-iag}"

docker logs --since "{INCIDENT_TIME}" "${IAG_CONTAINER}" 2>&1 \
  | grep -i "error\|warn\|failed\|exception\|service\|job\|token\|auth" \
  | head -500 > {project_path}/data/{TIMESTAMP}/iag_container_logs.txt

# IAG internal log files
docker exec "${IAG_CONTAINER}" find /var/log /opt -name "*.log" 2>/dev/null | head -20
docker exec "${IAG_CONTAINER}" tail -200 /var/log/iag/iag.log 2>/dev/null \
  >> {project_path}/data/{TIMESTAMP}/iag_container_logs.txt

# IAG adapter log in IAP (IAP's view of the IAG connection)
docker exec platform tail -300 /var/log/itential/{IAG_ADAPTER_NAME}.log 2>/dev/null \
  | grep -i "error\|offline\|token\|EHOSTUNREACH\|ECONNREFUSED\|auth" \
  > {project_path}/data/{TIMESTAMP}/iag_adapter_iap_log.txt
```

### Step 4b — IAG on VM (SSH, role `iag`)

```bash
for i in $(seq 1 20); do
  ROLE=$(grep "^SSH_ROLE_${i}=" {project_path}/.env 2>/dev/null | cut -d= -f2-)
  [ "$ROLE" != "iag" ] && continue
  HOST=$(grep "^SSH_HOST_${i}=" {project_path}/.env | cut -d= -f2-)
  USER=$(grep "^SSH_USER_${i}=" {project_path}/.env | cut -d= -f2- || echo ec2-user)
  KEY=$(grep "^SSH_KEY_PATH_${i}=" {project_path}/.env | cut -d= -f2- || echo ~/.ssh/id_rsa)
  LABEL=$(grep "^SSH_LABEL_${i}=" {project_path}/.env | cut -d= -f2- || echo "iag-${i}")
  ssh -i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${USER}@${HOST}" \
    "sudo tail -500 /var/log/iag/*.log 2>/dev/null \
     || sudo journalctl -u iag --since '1 hour ago' --no-pager 2>/dev/null" \
    > {project_path}/data/{TIMESTAMP}/iag_vm_${LABEL}.txt 2>&1 &
done
wait
```

### Step 4c — IAG on Kubernetes

```bash
kubectl logs -n {KUBE_NAMESPACE} deployment/iag --since=2h 2>&1 \
  | grep -i "error\|warn\|service\|job\|auth\|token" | head -500 \
  > {project_path}/data/{TIMESTAMP}/iag_k8s_logs.txt
```

---

## Phase 5: MongoDB Logs (mongod.log)

```bash
MONGO_LOG=${MONGO_LOG_PATH:-/var/log/mongodb/mongod.log}
MONGO_CTR=${MONGO_LOG_CONTAINER:-mongodb}

# Docker
docker exec "${MONGO_CTR}" cat "${MONGO_LOG}" 2>/dev/null \
  > {project_path}/data/{TIMESTAMP}/mongod_raw.txt

# If not found, discover log path
if [ ! -s {project_path}/data/{TIMESTAMP}/mongod_raw.txt ]; then
  echo "Discovering MongoDB log path..."
  docker exec "${MONGO_CTR}" sh -c \
    "ps aux | grep mongod | grep -o '\-\-logpath [^ ]*' | awk '{print \$2}'" 2>/dev/null
  docker exec "${MONGO_CTR}" cat /etc/mongod.conf 2>/dev/null | grep -i "path\|log"
fi

# SSH / VM (role `mongodb` targets)
for i in $(seq 1 20); do
  ROLE=$(grep "^SSH_ROLE_${i}=" {project_path}/.env 2>/dev/null | cut -d= -f2-)
  [ "$ROLE" != "mongodb" ] && continue
  HOST=$(grep "^SSH_HOST_${i}=" {project_path}/.env | cut -d= -f2-)
  USER=$(grep "^SSH_USER_${i}=" {project_path}/.env | cut -d= -f2- || echo ec2-user)
  KEY=$(grep "^SSH_KEY_PATH_${i}=" {project_path}/.env | cut -d= -f2- || echo ~/.ssh/id_rsa)
  LABEL=$(grep "^SSH_LABEL_${i}=" {project_path}/.env | cut -d= -f2- || echo "mongo-${i}")
  ssh -i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${USER}@${HOST}" \
    "sudo cat \${MONGO_LOG_PATH:-/var/log/mongodb/mongod.log}" \
    > {project_path}/data/{TIMESTAMP}/mongod_raw_${LABEL}.txt 2>&1 &
done
wait
cat {project_path}/data/{TIMESTAMP}/mongod_raw_*.txt \
  > {project_path}/data/{TIMESTAMP}/mongod_raw.txt 2>/dev/null
```

### Analyze mongod.log

```bash
# Slow queries in incident window
grep -i "SLOW_QUERY\|planSummary" {project_path}/data/{TIMESTAMP}/mongod_raw.txt \
  | grep "{INCIDENT_DATE}" \
  | head -100 > {project_path}/data/{TIMESTAMP}/mongod_slow.txt

# Parse durations (MongoDB 4.4+ JSON format)
python3 -c "
import json
for ln in open('{project_path}/data/{TIMESTAMP}/mongod_slow.txt'):
    try:
        r = json.loads(ln)
        attr = r.get('attr',{})
        dur  = attr.get('durationMillis', 0)
        ns   = attr.get('ns','?')
        plan = attr.get('planSummary','?')
        cmd  = str(attr.get('command',{}))[:80]
        flag = '🔴' if dur > 1000 else '⚠️'
        print(f'{flag} {dur:>6}ms  {ns}  plan={plan}  {cmd}')
    except: pass
" 2>/dev/null | sort -rn | head -20

# Errors and warnings
grep -E '"s":"E"|"s":"W"|ERROR|WARNING|FAILED|exception' \
  {project_path}/data/{TIMESTAMP}/mongod_raw.txt \
  | grep "{INCIDENT_DATE}" | head -100

# Connection events
grep -i "connection accepted\|connection ended\|too many" \
  {project_path}/data/{TIMESTAMP}/mongod_raw.txt \
  | grep "{INCIDENT_DATE}" | wc -l

# Replication lag
grep -i "replication\|oplog\|PRIMARY\|SECONDARY\|lag" \
  {project_path}/data/{TIMESTAMP}/mongod_raw.txt \
  | grep "{INCIDENT_DATE}" | tail -30
```

**Key mongod.log patterns:**
| Pattern | Meaning |
|---------|---------|
| Slow queries > 100ms on `jobs` | Missing index; common with high job volume |
| `planSummary: COLLSCAN` | Full collection scan — index missing |
| Connection count spike | IAP connection pool leak or restart storm |
| `too many open connections` | Connection pool limit hit |
| Replication lag > 10s | Secondary falling behind |

---

## Phase 6: Redis Logs

```bash
REDIS_LOG=${REDIS_LOG_PATH:-/var/log/redis/redis-server.log}
REDIS_CTR=${REDIS_LOG_CONTAINER:-redis}

# Docker
docker exec "${REDIS_CTR}" cat "${REDIS_LOG}" 2>/dev/null \
  > {project_path}/data/{TIMESTAMP}/redis_raw.txt

# Fall back to docker logs (Redis stdout mode)
if [ ! -s {project_path}/data/{TIMESTAMP}/redis_raw.txt ]; then
  docker logs "${REDIS_CTR}" 2>&1 > {project_path}/data/{TIMESTAMP}/redis_raw.txt
  echo "Used docker logs (Redis stdout mode)"
fi

# SSH / VM (role `redis` targets)
for i in $(seq 1 20); do
  ROLE=$(grep "^SSH_ROLE_${i}=" {project_path}/.env 2>/dev/null | cut -d= -f2-)
  [ "$ROLE" != "redis" ] && continue
  HOST=$(grep "^SSH_HOST_${i}=" {project_path}/.env | cut -d= -f2-)
  USER=$(grep "^SSH_USER_${i}=" {project_path}/.env | cut -d= -f2- || echo ec2-user)
  KEY=$(grep "^SSH_KEY_PATH_${i}=" {project_path}/.env | cut -d= -f2- || echo ~/.ssh/id_rsa)
  LABEL=$(grep "^SSH_LABEL_${i}=" {project_path}/.env | cut -d= -f2- || echo "redis-${i}")
  ssh -i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${USER}@${HOST}" \
    "sudo cat \${REDIS_LOG_PATH:-/var/log/redis/redis-server.log} 2>/dev/null \
     || sudo journalctl -u redis --since '{INCIDENT_TIME}' --no-pager 2>/dev/null" \
    > {project_path}/data/{TIMESTAMP}/redis_raw_${LABEL}.txt 2>&1 &
done
wait
cat {project_path}/data/{TIMESTAMP}/redis_raw_*.txt \
  > {project_path}/data/{TIMESTAMP}/redis_raw.txt 2>/dev/null
```

### Analyze Redis Logs

```bash
# Memory / eviction warnings
grep -i "memory\|evict\|maxmemory\|OOM\|out of memory" \
  {project_path}/data/{TIMESTAMP}/redis_raw.txt | head -50

# Persistence events
grep -i "RDB\|AOF\|saving\|saved\|bgsave\|rewrite\|fork" \
  {project_path}/data/{TIMESTAMP}/redis_raw.txt | head -20

# Connection events
grep -i "connection\|client\|accepted\|closed\|refused" \
  {project_path}/data/{TIMESTAMP}/redis_raw.txt | tail -30

# Slow log (from redis-cli)
redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a ${REDIS_PASSWORD}} \
  SLOWLOG GET 20 2>/dev/null > {project_path}/data/{TIMESTAMP}/redis_slowlog.txt
```

---

## Phase 7: Load Balancer Logs

Detect `LB_TYPE` from `.env`. Run the appropriate option.

### Nginx / HAProxy

```bash
LB_CTR=${LB_CONTAINER:-nginx}

# Docker
docker exec "${LB_CTR}" cat "${LB_LOG_PATH:-/var/log/nginx/access.log}" 2>/dev/null \
  > {project_path}/data/{TIMESTAMP}/lb_access_raw.txt

# SSH / VM (role `lb` targets)
for i in $(seq 1 20); do
  ROLE=$(grep "^SSH_ROLE_${i}=" {project_path}/.env 2>/dev/null | cut -d= -f2-)
  [ "$ROLE" != "lb" ] && continue
  HOST=$(grep "^SSH_HOST_${i}=" {project_path}/.env | cut -d= -f2-)
  USER=$(grep "^SSH_USER_${i}=" {project_path}/.env | cut -d= -f2- || echo ec2-user)
  KEY=$(grep "^SSH_KEY_PATH_${i}=" {project_path}/.env | cut -d= -f2- || echo ~/.ssh/id_rsa)
  LABEL=$(grep "^SSH_LABEL_${i}=" {project_path}/.env | cut -d= -f2- || echo "lb-${i}")
  ssh -i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${USER}@${HOST}" \
    "sudo cat \${LB_LOG_PATH:-/var/log/nginx/access.log} 2>/dev/null \
     || sudo cat /var/log/haproxy.log 2>/dev/null" \
    > {project_path}/data/{TIMESTAMP}/lb_raw_${LABEL}.txt 2>&1 &
done
wait
cat {project_path}/data/{TIMESTAMP}/lb_raw_*.txt \
  > {project_path}/data/{TIMESTAMP}/lb_access_raw.txt 2>/dev/null
```

### AWS ALB (from S3)

```bash
# ALB access logs land in S3 — uses existing AWS_* credentials
aws s3 ls "s3://${LB_S3_BUCKET}/${LB_S3_PREFIX}" \
  --region {AWS_REGION} 2>/dev/null | tail -20

aws s3 cp "s3://${LB_S3_BUCKET}/${LB_S3_PREFIX}{DATE_PATH}" - \
  --region {AWS_REGION} 2>/dev/null | gunzip -c \
  | grep "{INCIDENT_DATE}" | head -500 \
  > {project_path}/data/{TIMESTAMP}/lb_alb_raw.txt
```

### Analyze LB Logs

```bash
# Filter to incident window
grep "{INCIDENT_DATE}" {project_path}/data/{TIMESTAMP}/lb_access_raw.txt \
  > {project_path}/data/{TIMESTAMP}/lb_window.txt

# 5xx errors (upstream IAP errors)
grep -E '" 5[0-9]{2} ' {project_path}/data/{TIMESTAMP}/lb_window.txt \
  | sort | uniq -c | sort -rn | head -20

# Slow requests (nginx combined log: $request_time at end)
awk '{print $(NF-1), $0}' {project_path}/data/{TIMESTAMP}/lb_window.txt \
  | sort -rn | head -20

# 502 Bad Gateway (upstream IAP not responding)
grep '" 502 ' {project_path}/data/{TIMESTAMP}/lb_window.txt | wc -l

# Health check failures
grep -i "health\|check\|upstream" {project_path}/data/{TIMESTAMP}/lb_window.txt | tail -20
```

**Key LB log patterns:**
| Pattern | Meaning |
|---------|---------|
| Many `502` responses | IAP not responding — crashed or overloaded |
| Many `504` responses | IAP responding too slowly — timeout at LB |
| Spike in 5xx | IAP process error or restart |
| Health check failures | LB stopped routing to IAP — check IAP health |
| No requests in window | LB routing issue or complete IAP outage |

---

## Phase 8: Cross-Component Log Correlation

After collecting all log sets, correlate by timestamp around the incident.

**If a customer attachment was provided (Step 1a),** first confirm you've found the matching entry in the platform's own logs — grep verbatim for the exact error text from the attachment, don't rely on timestamp proximity alone (clocks can drift, and the customer's copy/paste may be truncated):

```bash
grep -F "{REPORTED_ERROR_LINE excerpt}" {project_path}/data/{TIMESTAMP}/iap_logs_raw.txt \
  {project_path}/data/{TIMESTAMP}/webserver_raw.txt 2>/dev/null
```

Once matched, walk backward from that exact line in the platform log (which has full context the customer's excerpt may lack) rather than only using the time window — this is the most reliable way to find the actual precursor event, since it's anchored to the confirmed same occurrence, not just "something in the same 30-minute window."

```bash
# Merge all error lines with source labels
python3 -c "
import os, glob

LOG_FILES = {
    'IAP':   '{project_path}/data/{TIMESTAMP}/iap_logs_errors.txt',
    'WS':    '{project_path}/data/{TIMESTAMP}/webserver_window.txt',
    'IAG':   '{project_path}/data/{TIMESTAMP}/iag_container_logs.txt',
    'MONGO': '{project_path}/data/{TIMESTAMP}/mongod_raw.txt',
    'REDIS': '{project_path}/data/{TIMESTAMP}/redis_raw.txt',
    'LB':    '{project_path}/data/{TIMESTAMP}/lb_window.txt',
}

lines = []
for label, path in LOG_FILES.items():
    if os.path.exists(path):
        for ln in open(path):
            ln = ln.strip()
            if ln and any(k in ln.lower() for k in ['error','warn','fail','exception','timeout','refused','unreachable']):
                lines.append((ln[:20], label, ln[:200]))

lines.sort(key=lambda x: x[0])
for ts, label, msg in lines[:100]:
    print(f'[{label}] {msg}')
" 2>/dev/null
```

---

## Phase 9: Mask Sensitive Values

Before displaying any log content to the user, mask sensitive values:

```bash
python3 -c "
import re, sys
for ln in open('{LOG_FILE}'):
    # Mask tokens: show first 6 + last 4 chars
    ln = re.sub(r'(token|Token|TOKEN|password|Password|secret|key)[\"'\'':\s]+([A-Za-z0-9+/=_\-]{10,})',
                lambda m: m.group(1) + ': [MASKED]',
                ln)
    # Mask bearer tokens
    ln = re.sub(r'Bearer\s+[A-Za-z0-9+/=_\-\.]+',
                'Bearer [MASKED]',
                ln)
    print(ln, end='')
" 2>/dev/null
```

---

## Phase 10: Log Report

Save to `{project_path}/data/{TIMESTAMP}/log_report.md`:

```markdown
# Log Analysis Report
**Generated:** {YYYY-MM-DD HH:MM:SS UTC}
**Incident Time:** {INCIDENT_TIME} ± 30 min
**Components Collected:** {list}

## IAP Application Logs
- Lines collected: {N}
- Error lines: {N}
- Top error patterns:
  - `{pattern}`: {N} occurrences
  - `{pattern}`: {N} occurrences

## Webserver Access Log
- Total requests in window: {N}
- HTTP 5xx errors: {N}
- HTTP 4xx errors: {N}
- Slowest endpoint: `{endpoint}` at {X}ms

## IAG Logs
- Lines collected: {N}
- Key errors: {list or "None"}

## MongoDB Log
- Slow queries (> 100ms): {N}
- Worst query: {Xms} on {collection}
- Connection events: {N}
- Error lines: {N}

## Redis Log
- Memory/eviction warnings: {N}
- Persistence events: {N}

## Load Balancer Log
- 5xx responses: {N}
- 4xx responses: {N}
- Slowest upstream time: {X}s

## Correlation Summary
{Narrative: what happened, when, which component first showed errors}

## Recommended Next Steps
{Specific follow-up actions based on findings}

## Artifacts Collected
| File | Size | Description |
|------|------|-------------|
| iap_logs_raw.txt | {X}KB | IAP application logs |
| webserver_raw.txt | {X}KB | IAP HTTP access log |
| iag_container_logs.txt | {X}KB | IAG container logs |
| mongod_raw.txt | {X}KB | MongoDB logs |
| redis_raw.txt | {X}KB | Redis logs |
| lb_access_raw.txt | {X}KB | Load balancer access log |
```

---

## Access Gaps

After reading `.env`, report what was not collected and what credentials would unlock it:

| Missing | What It Unlocks |
|---------|----------------|
| `SSH_HOST_N` / `SSH_HOST` | IAP, MongoDB, Redis, LB logs from VMs |
| `KUBE_NAMESPACE` + kubectl | IAP, IAG logs from EKS pods |
| `AWS_ACCESS_KEY_ID` | CloudWatch log groups, ALB S3 log access |
| `IAG_URL` / `IAG_CONTAINER_NAME` | IAG container or API logs |
| `MONGO_LOG_CONTAINER` | MongoDB container log access |
| `LB_CONTAINER` / `LB_HOST` | Load balancer log access |
