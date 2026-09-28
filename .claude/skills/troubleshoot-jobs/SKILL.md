---
name: troubleshoot-jobs
description: Troubleshoot stuck, errored, or slow IAP jobs. Follows the full execution chain: Pronghorn Core → Operations Manager → WorkflowEngine → Redis/Bull queues → MongoDB → OS resources. Compares slow jobs against historical baseline runs of the same workflow, traverses the full parent-child job chain for errors, verifies true task stalls, correlates adapter/application health, and cross-references platform/gateway/Mongo/Redis logs around the incident window.
argument-hint: "[job ID or workflow name]"
---

# Troubleshoot Jobs (Stuck, Errored, or Slow)

**Owns:** Stuck job diagnosis, slow job root-cause analysis (including historical comparison against prior runs of the same job), errored-job root-cause analysis across the full parent-child chain, WFE health, Redis/Bull queue depth, MongoDB job collection performance and concurrency load, Prometheus/Grafana metrics for WFE and Pronghorn Core, adapter/application health correlation, and cross-component log correlation.

**Use when:** Jobs are running slower than expected, jobs are stuck (never completing), a job or one of its child jobs has errored, high job queue depth, or job status is `running` with no task progress.

## Purposes & Investigation Paths

This skill serves two distinct investigation goals. Identify which one applies before choosing which phases to run:

**Purpose A — Why is this job (and its children) running slow?**
Run Phase 1 (job profile) → Phase 5b/5c (historical baseline comparison + concurrency load at the time the job ran) → Phase 6 (Prometheus, if available) → Phase 9 (root cause matrix). The key technique is differential: don't just look at the slow job in isolation — pull a prior healthy run of the *same* workflow from MongoDB and diff task-by-task timings, then check how many other jobs/tasks were executing concurrently during each run's window. A regression on one specific task points at that task's target (adapter/IAG/DB); a uniform slowdown with high concurrent load points at resource contention (WFE workers, Mongo connections, Redis).

**Purpose B — Why has this job errored, or why is it stuck in `running`?**
Run Phase 1 (job profile) → Phase 7 (full parent-child error chain + true-stall verification + adapter/application health + cross-component log correlation) → Phase 9. The key technique is elimination: confirm whether the job is *actually* stalled (no task progress over time) or just slow; walk every child/grandchild job in the chain for errors even if the parent shows no error; rule out (or confirm) that a dependent adapter or application is unhealthy; and correlate logs from IAP, IAG/gateway, MongoDB, and Redis in the incident window to find the true origin of the failure.

---

## CRITICAL SAFETY RULES

- **GET and read-only queries only** — no PUT, DELETE, PATCH without explicit user consent
- **No MongoDB writes** — `db.jobs.createIndex()` recommendations are informational only; do not apply without consent
- **No Redis writes** — no SET, DEL, FLUSHDB
- **Never restart WFE or services** without explicit user consent
- **Read `.env` for credentials** — never ask the user for credentials already in `.env`

---

## Auth Reuse

**Env file selection:** If the orchestrator already ran Step 3a and `.auth.json` holds a token less than 50 minutes old whose `platform_url` matches, reuse it directly.

If no valid cached token exists, run env discovery across the entire project tree (current folder, `environments/`, `repro/`, all subdirectories) and present the engineer with a numbered list of found `.env` files showing `PLATFORM_URL` and which variable groups each file contains. See `/troubleshoot` Step 3a for the full interactive selection flow (including mix-and-match from different files).

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

## Phase 1: Job Profile

### Step 1a — Profile the Specific Job

If the user provided a job ID:
```bash
curl -sk "{PLATFORM_URL}/operations-manager/jobs/{JOB_ID}?token={TOKEN}" \
  | python3 -c "
import sys, json, datetime
d = json.load(sys.stdin)
j = d if d.get('_id') else d.get('job', d)
print('Job ID:   ', j.get('_id'))
print('Workflow: ', j.get('name'))
print('Status:   ', j.get('status'))
start = j.get('start_time') or j.get('startTime') or ''
end   = j.get('end_time')   or j.get('endTime') or ''
print('Start:    ', start)
print('End:      ', end or '(still running)')
if start and end:
    s = datetime.datetime.fromisoformat(start.replace('Z','+00:00').replace('+00:00',''))
    e = datetime.datetime.fromisoformat(end.replace('Z','+00:00').replace('+00:00',''))
    print(f'Duration:  {(e-s).total_seconds():.1f}s')

# Task timing breakdown
tasks = j.get('tasks', {})
timings = []
for tid, t in tasks.items():
    if not isinstance(t, dict): continue
    ts = t.get('start_time') or t.get('startTime')
    te = t.get('end_time')   or t.get('endTime')
    name   = t.get('name', tid)
    status = t.get('status','?')
    if ts and te:
        s2 = datetime.datetime.fromisoformat(str(ts).replace('Z','+00:00').replace('+00:00',''))
        e2 = datetime.datetime.fromisoformat(str(te).replace('Z','+00:00').replace('+00:00',''))
        dur = (e2 - s2).total_seconds()
        timings.append((dur, name, status))
    elif ts and not te:
        timings.append((None, name, 'running (no end time)'))

if timings:
    print()
    print('--- Task Timing (slowest first) ---')
    for item in sorted([t for t in timings if t[0] is not None], reverse=True)[:15]:
        dur, name, status = item
        flag = '⚠️ SLOW' if dur > 30 else ''
        print(f'  {dur:>8.1f}s  [{status}]  {name}  {flag}')
    for dur, name, status in [t for t in timings if t[0] is None]:
        print(f'  {\"(still running)\":>10}  [{status}]  {name}  ⚠️')
"
```

If no job ID, find recent slow/stuck jobs:
```bash
# Running jobs with oldest start time (most likely to be stuck)
curl -sk "{PLATFORM_URL}/operations-manager/jobs?status=running&limit=20&token={TOKEN}" \
  | python3 -c "
import sys, json, datetime
d = json.load(sys.stdin)
now = datetime.datetime.utcnow()
for j in d.get('jobs', d.get('results', [])):
    start = j.get('start_time') or j.get('startTime') or ''
    if start:
        try:
            s = datetime.datetime.fromisoformat(start.replace('Z','+00:00').replace('+00:00',''))
            age_min = (now - s).total_seconds() / 60
            flag = '⚠️ STUCK?' if age_min > 60 else ''
            print(f'{age_min:>6.0f} min  {j[\"_id\"]}  {j.get(\"name\",\"?\")}  {flag}')
        except: pass
" 2>/dev/null | sort -rn | head -20
```

---

## Phase 2: Operations Manager Health

### Step 2a — OM App Status and Queue Depth

```bash
# OM application health
curl -sk "{PLATFORM_URL}/health/applications?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for a in d.get('results',[]):
    if 'operation' in a.get('id','').lower():
        print(f\"{a['id']}: {a['state']} | {a.get('connection',{}).get('state','')}\")
"

# Queue depth
curl -sk "{PLATFORM_URL}/operations-manager/jobs?status=queued&limit=1&token={TOKEN}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Queued jobs:', d.get('total', len(d.get('jobs',[]))))"

curl -sk "{PLATFORM_URL}/operations-manager/jobs?status=running&limit=1&token={TOKEN}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Running jobs:', d.get('total', len(d.get('jobs',[]))))"
```

**Flag if:**
- Queued > 50 → WFE workers saturated; jobs waiting
- Running > 100 → abnormally high concurrency
- OM not `RUNNING` → jobs cannot be started

---

## Phase 3: WorkflowEngine Health

### Step 3a — WFE App Status and Settings

```bash
curl -sk "{PLATFORM_URL}/health/applications?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for a in d.get('results',[]):
    if 'workflow' in a.get('id','').lower():
        props = a.get('properties',{}).get('properties',{})
        lvl = props.get('console_level','?')
        workers = props.get('workers') or props.get('concurrency','?')
        flag = '⚠️ HIGH I/O' if lvl in ('spam','debug','trace') else ''
        print(f\"{a['id']}: state={a['state']}  console_level={lvl} {flag}  workers={workers}\")
"

# Check WFE log file size (spam level causes huge logs = disk I/O pressure)
docker exec platform du -sh /var/log/itential/WorkFlowEngine*.log 2>/dev/null | sort -rh | head -5
```

**Flag if:**
- `console_level: spam` or `debug` → excessive log I/O slows WFE task processing
- Log file > 500MB → disk I/O competition with job state writes
- `workers` = 1 → serialized execution; all jobs queue behind each other
- WFE not `RUNNING` → no tasks are executing

### Step 3b — WFE Log Patterns

```bash
# Recent WFE log — look for errors, timeouts, worker saturation
docker exec platform tail -500 /var/log/itential/WorkFlowEngine.log 2>/dev/null \
  | grep -E "error|Error|ERROR|warn|WARN|timeout|stuck|worker|slow|saturate" \
  | tail -100
```

---

## Phase 4: Redis / Bull Queue Analysis

Run if `REDIS_HOST` is in `.env` or Redis is running in Docker:

```bash
# Connectivity
redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} PING 2>/dev/null

# Memory and eviction
redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} INFO memory 2>/dev/null \
  | grep -E "used_memory_human|maxmemory_human|maxmemory_policy|mem_fragmentation_ratio|evicted_keys"

# Blocked clients (BLPOP stall = Bull worker stuck waiting)
redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} INFO clients 2>/dev/null \
  | grep -E "connected_clients|blocked_clients"

# Bull queue names
redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} KEYS "bull:*" 2>/dev/null \
  | sort | head -30

# Depth of each Bull queue (wait/active/failed/delayed)
for Q in $(redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} KEYS "bull:*:wait" 2>/dev/null | sed 's/:wait//'); do
  WAIT=$(redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} LLEN "${Q}:wait" 2>/dev/null)
  ACTIVE=$(redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} LLEN "${Q}:active" 2>/dev/null)
  FAILED=$(redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} ZCARD "${Q}:failed" 2>/dev/null)
  DELAYED=$(redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} ZCARD "${Q}:delayed" 2>/dev/null)
  flag=""
  [ "${WAIT:-0}" -gt 100 ] 2>/dev/null && flag="⚠️ BACKLOG"
  [ "${FAILED:-0}" -gt 0 ] 2>/dev/null && flag="${flag} ⚠️ FAILURES"
  echo "${Q}:  wait=${WAIT}  active=${ACTIVE}  failed=${FAILED}  delayed=${DELAYED}  ${flag}"
done
```

**Redis health thresholds:**
| Metric | Concern | Action |
|--------|---------|--------|
| Bull `wait` > 100 | Task backlog | Increase WFE `workers`/`concurrency` |
| Bull `failed` growing | Silent task failures | Check failed job details |
| `evicted_keys` > 0 | Redis at memory limit | Increase Redis `maxmemory` |
| `blocked_clients` > 0 | Bull worker stall | Check WFE worker status |
| `mem_fragmentation_ratio` > 1.5 | Redis fragmentation latency | Consider Redis restart (with consent) |

---

## Phase 5: MongoDB Job Collection Performance

Run if `MONGO_URL` is in `.env`:

```bash
# Connectivity
mongosh "{MONGO_URL}" --eval "db.adminCommand({ping:1})" 2>/dev/null

# Server status: connections and slow ops
mongosh "{MONGO_URL}" --eval "
var s = db.adminCommand({serverStatus:1});
print('connections.current:', s.connections.current);
print('connections.available:', s.connections.available);
print('opcounters.query:', s.opcounters.query);
print('opcounters.update:', s.opcounters.update);
" 2>/dev/null

# Slow current operations (> 2s)
mongosh "{MONGO_URL}" --eval "
db.currentOp({secs_running: {'\$gt': 2}})
" 2>/dev/null | head -50

# Jobs collection document count
mongosh "{MONGO_URL}" --eval "
print('jobs count:', db.jobs.countDocuments({}));
" 2>/dev/null

# Jobs collection indexes
mongosh "{MONGO_URL}" --eval "db.jobs.getIndexes()" 2>/dev/null

# Query plan for jobs-by-status (critical IAP query — check for COLLSCAN)
mongosh "{MONGO_URL}" --eval "
var result = db.jobs.find({status:'running'}).explain('executionStats');
var stats = result.executionStats;
print('winning plan stage:', result.queryPlanner.winningPlan.stage);
print('docs examined:', stats.totalDocsExamined);
print('keys examined:', stats.totalKeysExamined);
print('execution time ms:', stats.executionTimeMillis);
if (result.queryPlanner.winningPlan.stage === 'COLLSCAN') {
  print('⚠️  COLLSCAN — missing index on {status}. All job-status queries do full collection scans.');
}
" 2>/dev/null

# Slow query profile (if profiling already enabled — level >= 1)
mongosh "{MONGO_URL}" --eval "
db.system.profile.find({ns: /jobs/, millis: {'\$gt': 100}})
  .sort({ts:-1}).limit(10)
  .forEach(function(p) {
    print(p.ts, p.op, p.ns, p.millis+'ms', JSON.stringify(p.command).substring(0,100));
  })
" 2>/dev/null

# Task start count summary (IAP-level job statistics)
mongosh "{MONGO_URL}" --eval "
var statuses = ['running','queued','complete','error','incomplete'];
statuses.forEach(function(s){
  try{ print(s + ':', db.jobs.countDocuments({status: s})); }catch(e){}
});
" 2>/dev/null
```

**MongoDB thresholds:**
| Metric | Concern | Action |
|--------|---------|--------|
| `jobs` COLLSCAN | Missing index on `status` | Recommend `db.jobs.createIndex({status:1})` (with consent) |
| `jobs` > 1M documents + COLLSCAN | Severe scan penalty | Also recommend archival of old jobs |
| `secs_running` > 30 | Long-running blocking query | Kill with user consent; identify collection + index |
| Profile `millis` > 1000ms on jobs | Confirmed slow query | Add compound index `{status:1, name:1}` |
| `connections.current` > 80% available | Connection pool saturation | Check IAP pool size settings |

**Index recommendations** (informational — do not apply without user consent):
```javascript
db.jobs.createIndex({ status: 1 })
db.jobs.createIndex({ name: 1, status: 1 })
db.jobs.createIndex({ start_time: -1 })
```

---

### Step 5b — Historical Baseline Comparison (Purpose A: slow job)

Run when the question is "why is *this* run slower than usual?" Pull prior completed runs of the **same workflow** (`JOB_NAME` from Phase 1) and diff task timings against the job in question.

```bash
# Find recent completed runs of the same workflow, excluding the job in question
mongosh "{MONGO_URL}" --eval "
db.jobs.find({name: '{JOB_NAME}', status: 'complete', _id: {'\$ne': '{JOB_ID}'}})
  .sort({start_time: -1}).limit(10)
  .forEach(function(j){
    var dur = (new Date(j.end_time) - new Date(j.start_time)) / 1000;
    print(j._id + '  start=' + j.start_time + '  duration=' + dur + 's');
  });
" 2>/dev/null
```

Pick a `BASELINE_JOB_ID` — ideally the most recent run with a duration that looked "normal" (not itself flagged as slow/stuck). Then diff task-by-task:

```bash
mongosh "{MONGO_URL}" --eval "
function taskTimings(id){
  var j = db.jobs.findOne({_id: id});
  var out = {};
  if (!j || !j.tasks) return out;
  Object.keys(j.tasks).forEach(function(tid){
    var t = j.tasks[tid];
    if (t.start_time && t.end_time) {
      out[t.name || tid] = (new Date(t.end_time) - new Date(t.start_time)) / 1000;
    }
  });
  return out;
}
var current  = taskTimings('{JOB_ID}');
var baseline = taskTimings('{BASELINE_JOB_ID}');
print('Task                          Current(s)   Baseline(s)   Delta');
Object.keys(current).forEach(function(name){
  var c = current[name];
  var b = baseline[name];
  if (b === undefined) { print(name + '  cur=' + c + 's  (no baseline task by this name)'); return; }
  var delta = c - b;
  var flag = (delta > b * 0.5 && delta > 5) ? '⚠️ REGRESSION' : '';
  print(name + '  cur=' + c + 's  base=' + b + 's  delta=' + delta.toFixed(1) + 's  ' + flag);
});
" 2>/dev/null
```

**Interpretation:**
- One task regressed sharply, others match baseline → bottleneck is that task's target (adapter, IAG service, external DB/API) — investigate that integration specifically, not the platform as a whole
- All tasks uniformly slower by a similar percentage → platform-layer issue (WFE CPU/queue, Mongo/Redis contention) — see Step 5c below
- No baseline run exists → workflow may be new, or historical jobs already archived/purged; note this as a gap in the report

---

### Step 5c — Concurrency / Contention Analysis at Incident Time (Purpose A: slow job)

Determine whether the slow run coincided with unusually high concurrent load — this is the most common cause of a uniform (not task-specific) slowdown.

```bash
# Jobs started within +/-5 min of the incident job's start time
mongosh "{MONGO_URL}" --eval "
var job = db.jobs.findOne({_id: '{JOB_ID}'});
var start = new Date(job.start_time);
var windowStart = new Date(start.getTime() - 5*60000);
var windowEnd   = new Date(start.getTime() + 5*60000);
var overlapping = db.jobs.countDocuments({ start_time: {'\$gte': windowStart, '\$lte': windowEnd} });
print('Jobs started within +/-5min of incident job start:', overlapping);
if (overlapping > 20) print('⚠️ HIGH CONCURRENT LOAD — possible resource contention (WFE workers, Mongo connections, Redis)');
" 2>/dev/null

# Same window measurement for the baseline (healthy) run, for an apples-to-apples comparison
mongosh "{MONGO_URL}" --eval "
var job = db.jobs.findOne({_id: '{BASELINE_JOB_ID}'});
var start = new Date(job.start_time);
var windowStart = new Date(start.getTime() - 5*60000);
var windowEnd   = new Date(start.getTime() + 5*60000);
var overlapping = db.jobs.countDocuments({ start_time: {'\$gte': windowStart, '\$lte': windowEnd} });
print('Jobs started within +/-5min of baseline job start:', overlapping);
" 2>/dev/null
```

**Interpretation:** if the incident window's concurrent job count is significantly higher than the baseline window's, the slowdown is most likely contention-driven (not a regression in the workflow itself) — correlate with Phase 4 (Bull queue depth) and Phase 6 (WFE CPU/event-loop lag) for confirming evidence before recommending a fix.

---

## Phase 6: Prometheus / Grafana Metrics

Only run if `PROMETHEUS_URL` or `GRAFANA_URL` is in `.env`.

```bash
# Compute incident time range
START=$(date -d "{INCIDENT_TIME} -30min" +%s 2>/dev/null || date -j -f "%Y-%m-%dT%H:%M:%S" "{INCIDENT_TIME}" +%s 2>/dev/null)
END=$(date -d "{INCIDENT_TIME} +30min" +%s 2>/dev/null || echo $((START + 3600)))
STEP=30
```

### Step 6a — WFE CPU Usage

```bash
curl -sG "${PROMETHEUS_URL}/api/v1/query_range" \
  --data-urlencode 'query=rate(process_cpu_seconds_total{job=~"iap.*|itential.*|WorkFlowEngine.*"}[5m]) * 100' \
  --data-urlencode "start=${START}" --data-urlencode "end=${END}" --data-urlencode "step=${STEP}" \
  | python3 -c "
import sys,json
d=json.load(sys.stdin)
for r in d.get('data',{}).get('result',[]):
    lbl = r['metric'].get('job') or r['metric'].get('instance','unknown')
    vals = [float(v[1]) for v in r['values']]
    peak = round(max(vals),2) if vals else 0
    flag = '⚠️ HIGH' if peak > 80 else '✅'
    print(f'{lbl}: peak CPU={peak}%  {flag}')
"
```

### Step 6b — Heap Memory

```bash
# Heap used
curl -sG "${PROMETHEUS_URL}/api/v1/query_range" \
  --data-urlencode 'query=nodejs_heap_size_used_bytes{job=~"iap.*|itential.*"}' \
  --data-urlencode "start=${START}" --data-urlencode "end=${END}" --data-urlencode "step=${STEP}" \
  | python3 -c "
import sys,json
d=json.load(sys.stdin)
for r in d.get('data',{}).get('result',[]):
    lbl = r['metric'].get('job') or r['metric'].get('instance','unknown')
    vals = [float(v[1]) for v in r['values']]
    if vals: print(f'{lbl}: peak heap used = {round(max(vals)/1024/1024,1)} MB')
"

# Heap total
curl -sG "${PROMETHEUS_URL}/api/v1/query_range" \
  --data-urlencode 'query=nodejs_heap_size_total_bytes{job=~"iap.*|itential.*"}' \
  --data-urlencode "start=${START}" --data-urlencode "end=${END}" --data-urlencode "step=${STEP}" \
  | python3 -c "
import sys,json
d=json.load(sys.stdin)
for r in d.get('data',{}).get('result',[]):
    lbl = r['metric'].get('job') or r['metric'].get('instance','unknown')
    vals = [float(v[1]) for v in r['values']]
    if vals: print(f'{lbl}: peak heap total = {round(max(vals)/1024/1024,1)} MB')
"
```

### Step 6c — Event Loop Lag

```bash
curl -sG "${PROMETHEUS_URL}/api/v1/query_range" \
  --data-urlencode 'query=nodejs_eventloop_lag_seconds{job=~"iap.*|itential.*"}' \
  --data-urlencode "start=${START}" --data-urlencode "end=${END}" --data-urlencode "step=${STEP}" \
  | python3 -c "
import sys,json
d=json.load(sys.stdin)
for r in d.get('data',{}).get('result',[]):
    lbl = r['metric'].get('job') or r['metric'].get('instance','unknown')
    vals = [float(v[1])*1000 for v in r['values']]
    if vals:
        peak_ms = round(max(vals),1)
        avg_ms  = round(sum(vals)/len(vals),1)
        flag = '⚠️ HIGH' if peak_ms > 100 else '✅'
        print(f'{lbl}: peak={peak_ms}ms avg={avg_ms}ms  {flag}')
"
```
**Thresholds:** < 10ms healthy | 10–100ms elevated | > 100ms degraded

### Step 6d — GC Overhead

```bash
curl -sG "${PROMETHEUS_URL}/api/v1/query_range" \
  --data-urlencode 'query=rate(nodejs_gc_duration_seconds_sum{job=~"iap.*|itential.*"}[2m])' \
  --data-urlencode "start=${START}" --data-urlencode "end=${END}" --data-urlencode "step=${STEP}" \
  | python3 -c "
import sys,json
d=json.load(sys.stdin)
for r in d.get('data',{}).get('result',[]):
    lbl = r['metric'].get('job') or r['metric'].get('instance','unknown')
    gc_type = r['metric'].get('kind','unknown')
    vals = [float(v[1]) for v in r['values']]
    if vals: print(f'{lbl} [{gc_type}]: peak GC overhead = {round(max(vals)*100,1)}%')
"
```
**Threshold:** > 10% = concern | > 25% = severe GC pressure

### Step 6e — Firing Alerts

```bash
curl -s "${PROMETHEUS_URL}/api/v1/alerts" \
  | python3 -c "
import sys,json
alerts = [a for a in json.load(sys.stdin).get('data',{}).get('alerts',[]) if a.get('state')=='firing']
if not alerts: print('No firing alerts ✅')
else:
    for a in alerts:
        print(f\"FIRING: {a['labels'].get('alertname','')} | {a['labels'].get('job','')} — {a.get('annotations',{}).get('summary','')}\")
"
```

**Prometheus/Grafana gap:** If not in `.env`, note:
> "To enable process-level performance metrics, add `PROMETHEUS_URL=http://<host>:9090` to `.env`."

---

## Phase 7: Stuck / Errored Job Investigation — Parent-Child Chain + Health Correlation

Run this phase for **Purpose B** (job errored, or job shows `running` with no visible progress). Work through the steps in order — each one narrows down whether the root cause is in the job chain itself, a dependent adapter/app, or the underlying infrastructure.

### Step 7a — Discover the Child Job Chain

```bash
# Get parent job task outputs to find child job IDs
curl -sk "{PLATFORM_URL}/operations-manager/jobs/{PARENT_JOB_ID}?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
j = d if d.get('_id') else d.get('job', d)
tasks = j.get('tasks', {})
child_ids = []
for tid, t in tasks.items():
    if not isinstance(t, dict): continue
    output = t.get('output', {})
    if isinstance(output, dict):
        child_id = output.get('jobId') or output.get('job_id') or output.get('id')
        if child_id:
            child_ids.append((t.get('name',tid), child_id))
if child_ids:
    print('ChildJob IDs found:')
    for name, cid in child_ids:
        print(f'  Task: {name}  →  ChildJob: {cid}')
else:
    print('No child job IDs found in task outputs')
"
```

### Step 7b — Walk Every Job in the Chain for Errors (recursive)

Fetch each child job's status/errors. **If a child job itself spawned children (nested childJob tasks), repeat Step 7a against that child job ID** — walk the full chain down to the leaf jobs. A parent showing `running`/no-error does not mean the chain is healthy; a deeply nested child can be the one that actually errored.

```bash
for CHILD_ID in {CHILD_JOB_IDS}; do
  curl -sk "{PLATFORM_URL}/operations-manager/jobs/${CHILD_ID}?token={TOKEN}" \
    | python3 -c "
import sys, json
d = json.load(sys.stdin)
j = d if d.get('_id') else d.get('job', d)
print(f'ChildJob: {j.get(\"_id\")}  Workflow: {j.get(\"name\")}  Status: {j.get(\"status\")}')
errors = j.get('error',[]) if isinstance(j.get('error'), list) else [j.get('error',{})]
for e in errors:
    if isinstance(e, dict) and e:
        iap = e.get('IAPerror', e)
        print(f'  ⚠️ Error: {iap.get(\"displayString\",\"?\")}')
if not any(errors):
    print('  No error reported at this level — check its own children if any (repeat Step 7a with this ID)')
"
done
```

**Note:** every `job.error` field is an **array** — iterate all entries, never just check the first. A job can accumulate multiple errors across retries/branches.

### Step 7c — Verify True Stall vs. Slow-but-Progressing

A job showing `status: running` is not necessarily stuck — it may simply be slow. Confirm by polling twice and diffing task state:

```bash
curl -sk "{PLATFORM_URL}/operations-manager/jobs/{JOB_ID}?token={TOKEN}" > /tmp/job_poll1.json
sleep 60
curl -sk "{PLATFORM_URL}/operations-manager/jobs/{JOB_ID}?token={TOKEN}" > /tmp/job_poll2.json

python3 -c "
import json
j1 = json.load(open('/tmp/job_poll1.json')); j1 = j1 if j1.get('_id') else j1.get('job', j1)
j2 = json.load(open('/tmp/job_poll2.json')); j2 = j2 if j2.get('_id') else j2.get('job', j2)
t1, t2 = j1.get('tasks',{}), j2.get('tasks',{})
changed = False
for tid, t in t2.items():
    prev = t1.get(tid, {})
    if t.get('status') != prev.get('status') or t.get('end_time') != prev.get('end_time'):
        changed = True
        print(f'Task {t.get(\"name\",tid)} changed: {prev.get(\"status\")} -> {t.get(\"status\")}')
if not changed:
    print('⚠️ NO TASK PROGRESS in 60s — job is truly stuck, not just slow')
else:
    print('✅ Task state advanced — job is progressing, just slow (treat as Purpose A instead)')
"
```

If truly stuck, note which specific task is the one not advancing — that task's target (adapter, IAG service, external API/DB) is the next thing to check.

### Step 7d — Adapter & Application Health Correlation

Before assuming the job/workflow logic is at fault, rule out (or confirm) that the stalled/errored task's dependency is unhealthy.

```bash
curl -sk "{PLATFORM_URL}/health/adapters?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
offline = [a for a in d.get('results',[]) if a.get('state') != 'ONLINE' or a.get('connection',{}).get('state') != 'ONLINE']
if offline:
    print('⚠️ OFFLINE/DEGRADED ADAPTERS:')
    for a in offline:
        print(f\"  {a['id']}: state={a.get('state')} connection={a.get('connection',{}).get('state')}\")
else:
    print('All adapters ONLINE ✅')
"

curl -sk "{PLATFORM_URL}/health/applications?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
bad = [a for a in d.get('results',[]) if a.get('state') != 'RUNNING']
if bad:
    print('⚠️ APPS NOT RUNNING:')
    for a in bad:
        print(f\"  {a['id']}: {a.get('state')}\")
else:
    print('All applications RUNNING ✅')
"
```

Cross-reference the stalled/errored task's `app`/`adapter_id` (from the job's task definition) against this list. If the adapter/app was OFFLINE or restarting at/around the stall time, that is very likely the root cause — not the workflow itself. **For deep adapter diagnosis (auth failures, sampleProperties mismatch, debug logging), hand off to `/troubleshoot-adapters {ADAPTER_NAME}`** rather than duplicating that investigation here.

### Step 7e — Cross-Component Log Correlation

Pull logs from every relevant component in a tight window around the stall/error timestamp (use the last-advancing task's timestamp from Step 7c, or the job's `error` timestamp) and look for overlapping failures.

```bash
INCIDENT_TIME="{TASK_LAST_ACTIVITY_TIME or JOB_ERROR_TIME}"

# Platform (IAP webserver + application logs)
docker logs platform --since "${INCIDENT_TIME} -5 minutes" --until "${INCIDENT_TIME} +5 minutes" 2>&1 \
  | grep -iE "error|timeout|exception|refused|econnrefused" | tail -50

# IAG / Gateway logs (if IAG is in the workflow's task chain)
docker logs iag --since "${INCIDENT_TIME} -5 minutes" --until "${INCIDENT_TIME} +5 minutes" 2>&1 \
  | grep -iE "error|timeout|exception" | tail -50

# MongoDB logs
docker logs mongodb --since "${INCIDENT_TIME} -5 minutes" --until "${INCIDENT_TIME} +5 minutes" 2>&1 \
  | grep -iE "error|slow|connection|timed out" | tail -50

# Redis logs
docker logs redis --since "${INCIDENT_TIME} -5 minutes" --until "${INCIDENT_TIME} +5 minutes" 2>&1 \
  | grep -iE "error|oom|evict|warning" | tail -50
```

**What to look for:** overlapping error/exception timestamps across two or more components (e.g., a MongoDB "connection refused" at the exact second the stalled task stopped advancing) is strong evidence of the true root cause, versus a coincidental job-layer symptom. This is a fast first-pass correlation only — **for full log collection across VM/Kubernetes/CloudWatch deployments, delegate to `/troubleshoot-logs {component} {incident_time}`** rather than duplicating collection logic here.

---

## Phase 8: Webserver Log — Job Request Timing

```bash
docker exec platform cat /var/log/itential/webserver.log 2>/dev/null \
  | python3 -c "
import json, sys
for ln in sys.stdin:
    try:
        r = json.loads(ln.strip())
        url = r.get('url') or r.get('path','')
        meth = r.get('method','')
        rt   = float(r.get('responseTime') or r.get('response_time') or 0)
        if 'jobs' in url or 'operations-manager' in url:
            flag = '⚠️ SLOW' if rt > 2000 else ''
            print(f'{rt:>8.0f}ms  {r.get(\"status\",\"\")}  {meth}  {url[:80]}  {flag}')
    except: pass
" 2>/dev/null | sort -rn | head -30
```

---

## Phase 9: Root Cause — Decision Matrix

**Purpose A — slow job:**

| Symptom | Bottleneck Layer | Recommended Fix |
|---------|-----------------|----------------|
| `POST /jobs/start` slow (> 2s) | MongoDB / OM | Check `jobs.createIndex`, `currentOp` |
| Many jobs in `queued` state | WFE worker pool | Increase `workers`/`concurrency` in WFE |
| One task accounts for > 80% of duration | That task's target (adapter/IAG/DB) | Investigate adapter or IAG service |
| All tasks slow uniformly | Redis queue or WFE process | Check Bull depth, WFE CPU, log I/O |
| `COLLSCAN` on jobs collection | Missing MongoDB index | Add `{status:1}` index (with consent) |
| WFE log file > 500MB | Disk I/O from logging | Lower `console_level` from `spam` to `error` |
| Platform CPU > 80% sustained | WFE CPU-bound | Profile hot workflow/task path |
| Redis evictions during window | Redis memory pressure | Increase `maxmemory`, review TTL policy |
| Event loop lag > 100ms | Node.js saturated | Check long JSTs, sync DB calls, GC pauses |
| One task regressed vs. baseline, others match (Step 5b) | That task's specific target | Investigate the adapter/IAG/DB behind that task, not the platform |
| All tasks regressed uniformly vs. baseline (Step 5b) + high concurrency at incident window (Step 5c) | Resource contention (WFE/Mongo/Redis) | Scale workers, connections, or stagger scheduled jobs |
| No baseline run found | Insufficient history | Note as investigation gap; monitor going forward |

**Purpose B — stuck / errored job:**

| Symptom | Bottleneck Layer | Recommended Fix |
|---------|-----------------|----------------|
| Task state unchanged across two polls (Step 7c) | True stall at that task's target | Investigate the adapter/IAG/DB/API that task calls |
| Task state advancing between polls | Not actually stuck — slow | Re-route to Purpose A (Steps 5b/5c) |
| Parent shows no error but nested child job errored (Step 7b) | Missing error propagation in parent workflow | Add error transition/handling so parent surfaces child errors |
| Adapter/App OFFLINE or restarting during stall window (Step 7d) | Adapter/app outage, not workflow logic | Fix/restart the adapter or app (with consent); no workflow change needed |
| `Job has no available transitions` | No error transition on adapter/external task | Add `"state": "error"` transition to that task |
| Overlapping errors across IAP/Mongo/Redis/IAG logs at same timestamp (Step 7e) | Systemic/infra-level outage | Escalate to `/troubleshoot-infra` or `/troubleshoot-databases` |
| Errors isolated to platform log only | Job/workflow-level issue | Continue with `/troubleshoot-workflows` for JST/task-level root cause |

---

## Phase 10: Report

Save to `{project_path}/data/{TIMESTAMP}/job_analysis.md`. Include the sections relevant to whichever purpose (A and/or B) was investigated.

```markdown
# Slow/Stuck/Errored Job Analysis
**Job ID:** {JOB_ID} | **Workflow:** {WORKFLOW_NAME}
**Duration:** {Xs} (expected: ~{Ys}) | **Incident Time:** {INCIDENT_TIME}
**Investigation Purpose:** A — Slow job | B — Stuck/Errored job | Both

## Job Task Profile (slowest first)
| Task | Duration | Status |
|------|----------|--------|

## Layer-by-Layer Findings
### Pronghorn Core / Webserver
### Operations Manager
- Running: {N} / Queued: {N}
### WorkflowEngine
- State: {state} | Workers: {N} | log_level: {level}
### Redis / Bull Queues
### MongoDB
- jobs count: {N} | Indexes: {list} | COLLSCAN: {yes/no}
### Prometheus Metrics
- Peak CPU: {X}% | Peak Heap: {X}MB | Peak Event Loop Lag: {X}ms

## Purpose A — Historical Comparison (if slow job)
- Baseline job: {BASELINE_JOB_ID} | Baseline duration: {Ys} | Current duration: {Xs}
- Task-by-task regression: {task name(s) that regressed, with delta}
- Concurrent jobs at incident window: {N} | Concurrent jobs at baseline window: {N}

## Purpose B — Parent-Child Chain & Health Correlation (if stuck/errored job)
- Chain walked: {parent → child → grandchild IDs, with status/error at each level}
- True stall verification: {stuck confirmed / progressing, just slow}
- Adapter/App health at incident time: {list any OFFLINE/degraded found, or "all healthy"}
- Cross-component log correlation: {overlapping errors found across which components, or "none found"}

## Root Cause
{One-sentence bottleneck summary}

## Recommended Actions
1. {Highest-impact fix}
2. {Secondary fix}
3. {Monitoring recommendation}
```
