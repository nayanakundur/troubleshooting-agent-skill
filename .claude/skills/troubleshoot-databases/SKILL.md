---
name: troubleshoot-databases
description: Deep MongoDB and Redis diagnostics for Itential Platform. Covers connection pools, slow queries, index analysis, replica set health, Bull queue depth, eviction policy, and ElastiCache. Read-only queries only.
argument-hint: "[mongodb|redis|both]"
---

# Troubleshoot Databases

**Owns:** Deep diagnostics for MongoDB and Redis (including ElastiCache and Redis Sentinel). Covers connectivity, connection pool saturation, slow query analysis, index gaps, replica set lag, Bull queue health, eviction policy, and persistence state.
**Use when:** MongoDB is slow or returning connection errors, Redis eviction is occurring, Bull queues are backed up, job state writes are failing, or database-level latency is suspected as a root cause.

---

## CRITICAL SAFETY RULES

- **Read-only queries ONLY** — no `updateOne`, `deleteOne`, `$merge`, `FLUSHDB`, `SET`, `DEL` or any write operations
- **Use secondary MongoDB node** for reads where available (`readPreference=secondary` in `MONGO_URL`)
- **Do NOT enable MongoDB profiling** (profiling is an IAP DBA decision) — only read `system.profile` if already enabled
- **Index recommendations are informational** — share with user, do not apply without consent
- **Never restart MongoDB or Redis** without explicit user consent
- **Read `.env` for credentials** — never ask the user for credentials already in `.env`

---

## Auth Reuse (IAP API)

Some steps use the IAP API for application-layer context:

```bash
# Password auth
curl -sk -X POST "{PLATFORM_URL}/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "{USERNAME}", "password": "{PASSWORD}"}'
```

---

## Phase 1: MongoDB Diagnostics

### Step 1a — Connectivity

```bash
mongosh "{MONGO_URL}" --eval "
var p = db.adminCommand({ping:1});
print('ping:', p.ok === 1 ? '✅ OK' : '🔴 FAILED');
" 2>/dev/null || echo "🔴 Cannot connect to MongoDB — verify MONGO_URL in .env"
```

### Step 1b — Server Status (connections, opcounters, memory)

```bash
mongosh "{MONGO_URL}" --eval "
var s = db.adminCommand({serverStatus:1});
print('=== Connections ===');
print('current:  ', s.connections.current);
print('available:', s.connections.available);
var pct = Math.round(s.connections.current / (s.connections.current + s.connections.available) * 100);
print('pool used:', pct + '%', pct > 80 ? '⚠️ HIGH' : '✅ OK');

print()
print('=== Op Counters (since last restart) ===');
print('query:  ', s.opcounters.query);
print('insert: ', s.opcounters.insert);
print('update: ', s.opcounters.update);
print('delete: ', s.opcounters.delete);

print()
print('=== Memory ===');
print('resident MB:', s.mem.resident);
print('virtual MB: ', s.mem.virtual);
" 2>/dev/null
```

### Step 1c — Replica Set Status

```bash
mongosh "{MONGO_URL}" --eval "
try {
  var rs = rs.status();
  print('replicaSet:', rs.set);
  rs.members.forEach(function(m) {
    var lag = m.optimeDate ? (new Date() - m.optimeDate)/1000 : 'N/A';
    var flag = m.health !== 1 ? '🔴' : (m.stateStr !== 'PRIMARY' && m.stateStr !== 'SECONDARY' ? '⚠️' : '✅');
    print(flag + ' ' + m.name + ' [' + m.stateStr + ']  health=' + m.health + '  lag=' + lag + 's');
  });
} catch(e) {
  print('Standalone MongoDB — no replica set');
}
" 2>/dev/null
```

**Thresholds:**
- Secondary lag > 10s → replication falling behind; check oplog window and network
- Any member `health: 0` → member unreachable; replica set may lose quorum
- `RECOVERING` state → member catching up after a restart

### Step 1d — Current Slow Operations

```bash
mongosh "{MONGO_URL}" --eval "
var ops = db.adminCommand({currentOp: true, secs_running: {'\$gt': 2}});
var slow = ops.inprog.filter(function(o){ return o.secs_running > 2; });
print('Slow ops (> 2s):', slow.length);
slow.forEach(function(o){
  print('  ns:', o.ns, ' op:', o.op, ' secs_running:', o.secs_running, ' planSummary:', o.planSummary || 'N/A');
  print('  command:', JSON.stringify(o.command || {}).substring(0,150));
});
" 2>/dev/null
```

**If any op with `secs_running` > 30:** this is likely blocking other operations. Report to user; offer to kill with consent.

### Step 1e — Key IAP Collection Sizes

```bash
mongosh "{MONGO_URL}" --eval "
var cols = ['jobs','workflows','operations','adapters','tasks','users','sessions','transformations'];
cols.forEach(function(c){
  try {
    var count = db.getCollection(c).countDocuments({});
    var flag = (c === 'jobs' && count > 500000) ? ' ⚠️ HIGH — query performance risk' : '';
    print(c + ': ' + count + flag);
  } catch(e) {}
});
" 2>/dev/null
```

### Step 1f — Jobs Collection Index Analysis

The `jobs` collection is the most frequently queried — IAP reads it on every task state transition.

```bash
mongosh "{MONGO_URL}" --eval "
print('=== jobs collection indexes ===');
db.jobs.getIndexes().forEach(function(idx) {
  print('  ' + JSON.stringify(idx.key) + '  name=' + idx.name);
});
" 2>/dev/null

# Query plan for jobs-by-status (critical IAP query)
mongosh "{MONGO_URL}" --eval "
var result = db.jobs.find({status:'running'}).explain('executionStats');
var stats  = result.executionStats;
var plan   = result.queryPlanner.winningPlan.stage;
print('Query: db.jobs.find({status:\"running\"})');
print('  winning plan:', plan);
print('  docs examined:', stats.totalDocsExamined);
print('  keys examined:', stats.totalKeysExamined);
print('  execution ms: ', stats.executionTimeMillis);
if (plan === 'COLLSCAN') {
  print('  🔴 COLLSCAN — missing index on {status}. Every job query does a full collection scan.');
  print('  Recommended: db.jobs.createIndex({ status: 1 })   [DO NOT apply without consent]');
}
" 2>/dev/null
```

### Step 1g — Collections with No Extra Indexes

```bash
mongosh "{MONGO_URL}" --eval "
db.getCollectionNames().forEach(function(c){
  var idxs = db.getCollection(c).getIndexes();
  if (idxs.length <= 1) print('  No extra indexes: ' + c);
});
" 2>/dev/null | head -20
```

### Step 1h — Slow Query Profile (If Already Enabled)

```bash
# DO NOT enable profiling here — only read if already on (level >= 1)
mongosh "{MONGO_URL}" --eval "
var level = db.getProfilingStatus();
print('profiling level:', level.was, '  slowms:', level.slowms);
if (level.was >= 1) {
  print('Reading slow query log:');
  db.system.profile.find({millis: {'\$gt': 100}}).sort({ts:-1}).limit(15).forEach(function(p){
    print(p.ts, p.op, p.ns, p.millis+'ms', JSON.stringify(p.command).substring(0,100));
  });
} else {
  print('Profiling not enabled (level=0). Enable via IAP DBA if needed.');
}
" 2>/dev/null
```

### Step 1i — Stuck Running Jobs in MongoDB

```bash
mongosh "{MONGO_URL}" --eval "
db.jobs.find({status:'running'}, {name:1, start_time:1, _id:1})
  .sort({start_time:1}).limit(10)
  .forEach(function(j){
    var age = j.start_time ? Math.round((new Date() - j.start_time)/60000) : '?';
    var flag = age > 60 ? ' ⚠️ STUCK?' : '';
    print(age + 'min running  ' + j._id + '  ' + j.name + flag);
  });
" 2>/dev/null
```

### Step 1j — Index Recommendations (Informational)

Based on analysis, share these recommendations with the user. **Do not apply without consent.**

```javascript
// Recommended IAP MongoDB indexes
db.jobs.createIndex({ status: 1 })
db.jobs.createIndex({ name: 1, status: 1 })
db.jobs.createIndex({ start_time: -1 })
db.sessions.createIndex({ expires: 1 }, { expireAfterSeconds: 0 })  // TTL for sessions
```

### Step 1k — Platform Skill: MongoDB Full Life Report

**When to invoke** — any one of these conditions is true:
- Step 1c shows a member in non-PRIMARY / RECOVERING / STARTUP state, or secondary lag > 30s
- Step 1b shows connection pool used > 80% or resident memory near system limit
- Elections detected recently (check `rs.status().members[*].electionDate`)
- Replica set members are UNKNOWN or quorum is at risk
- Engineer explicitly requests a scored comprehensive assessment of the MongoDB cluster

**Prerequisite:** `MONGO_URL` must be set in `.env`.

**Invoke:**
```
/mongodb
```

The `/mongodb` skill authenticates using `MONGO_URL`, runs a full replica set life
report, and outputs a scored **HEALTHY / DEGRADED / CRITICAL** summary covering:
- Replica set topology, member states, and oplog window
- WiredTiger cache hit ratio and eviction pressure
- Write contention and active slow operations
- Slow query profiler (if already enabled)
- Database and collection sizes, index inventory
- Transparent Huge Pages (THP) check
- Recent log anomalies

**Offline / air-gapped environments:** if mongosh cannot reach the database from the
engineer's workstation but SSH access to the MongoDB nodes is available, the skill
provides offline collection scripts at `.claude/skills/mongodb/`:
- `offline-collect-cluster.sh` — runs mongosh diagnostics on the primary and saves
  output to `/tmp/mongo-life-cluster.txt` for retrieval
- `offline-collect-node.sh` — OS and service diagnostics per node
- `OFFLINE-COLLECTION.md` — full step-by-step offline workflow (engineer reference)
- `CUSTOMER-DATA-COLLECTION.md` — customer-shareable runbook for what to capture

---

## Phase 2: Redis Diagnostics

### Step 2a — Connectivity

```bash
redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} PING 2>/dev/null \
  && echo "✅ Redis reachable" || echo "🔴 Redis not reachable"
```

### Step 2b — Memory and Eviction

```bash
redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} INFO memory 2>/dev/null \
  | grep -E "used_memory_human|maxmemory_human|maxmemory_policy|mem_fragmentation_ratio|evicted_keys|mem_allocator"
```

**Thresholds:**
| Metric | Concern | Action |
|--------|---------|--------|
| `used_memory` approaching `maxmemory` | Eviction imminent | Increase `maxmemory` or reduce key TTLs |
| `evicted_keys` > 0 | Keys already being evicted | Review eviction policy; Bull queue items may be lost |
| `mem_fragmentation_ratio` > 1.5 | High fragmentation adding latency | Redis restart may help (with consent) |
| `maxmemory_policy: noeviction` | Will return errors when full, not evict | Risk: IAP gets Redis write errors |

### Step 2c — Connection and Client Stats

```bash
redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} INFO clients 2>/dev/null \
  | grep -E "connected_clients|blocked_clients|maxclients"
```

| Metric | Concern |
|--------|---------|
| `blocked_clients` > 0 | Bull workers stalled on BLPOP — queue processing stalled |
| `connected_clients` near `maxclients` | Connection pool exhaustion |

### Step 2d — Keyspace and Cache Efficiency

```bash
redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} INFO stats 2>/dev/null \
  | grep -E "keyspace_hits|keyspace_misses|evicted_keys|expired_keys|total_commands_processed"

redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} INFO keyspace 2>/dev/null
```

### Step 2e — Replication Status

```bash
redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} INFO replication 2>/dev/null \
  | grep -E "role|connected_slaves|master_repl_offset|slave_repl_offset|lag"
```

| Metric | Concern |
|--------|---------|
| Replication lag > 1s | Sentinel/replica falling behind |
| `connected_slaves: 0` on primary with replicas expected | Replica disconnected |

### Step 2f — Bull Queue Depth (IAP Job Queues)

```bash
# Discover all Bull queue names
redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} KEYS "bull:*" 2>/dev/null \
  | sort | head -30

# Depth of each queue
for Q in $(redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} KEYS "bull:*:wait" 2>/dev/null | sed 's/:wait//'); do
  WAIT=$(redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} LLEN "${Q}:wait" 2>/dev/null)
  ACTIVE=$(redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} LLEN "${Q}:active" 2>/dev/null)
  FAILED=$(redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} ZCARD "${Q}:failed" 2>/dev/null)
  DELAYED=$(redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} ZCARD "${Q}:delayed" 2>/dev/null)
  flag=""
  [ "${WAIT:-0}" -gt 100 ] 2>/dev/null && flag="⚠️ BACKLOG"
  [ "${FAILED:-0}" -gt 0 ] 2>/dev/null && flag="${flag} ⚠️ FAILURES"
  echo "${Q##bull:}:  wait=${WAIT}  active=${ACTIVE}  failed=${FAILED}  delayed=${DELAYED}  ${flag}"
done
```

### Step 2g — Slow Command Log

```bash
redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} SLOWLOG GET 20 2>/dev/null
```

Slow log entries > 10ms on `LLEN`, `ZADD`, `LRANGE` indicate Bull queue operation latency.

### Step 2h — Persistence State (RDB / AOF)

```bash
redis-cli -h {REDIS_HOST} -p {REDIS_PORT} ${REDIS_PASSWORD:+-a $REDIS_PASSWORD} INFO persistence 2>/dev/null \
  | grep -E "rdb_last_save_time|rdb_last_bgsave_status|aof_enabled|aof_last_write_status|aof_last_rewrite_time_sec"
```

| Pattern | Meaning |
|---------|---------|
| `rdb_last_bgsave_status: err` | Last RDB save failed — check disk space |
| `aof_last_write_status: err` | AOF write failed — check disk space |
| AOF rewrite > 60s | Disk I/O pressure |

### Step 2i — Platform Skill: Redis Full Life Report

**When to invoke** — any one of these conditions is true:
- Step 2b shows `used_memory` > 80% of `maxmemory`, or `evicted_keys` > 0
- Step 2e shows sentinel topology issues or replication lag > 1s
- `blocked_clients` > 0 persists after checking Bull queue depth (Step 2f)
- Multiple Bull queue `failed` entries growing steadily
- Engineer explicitly requests a scored comprehensive assessment of the Redis cluster

**Prerequisites:** `REDIS_HOST`, `REDIS_PORT` in `.env`. Add `REDIS_PASSWORD` if the
cluster requires auth.

**Invoke:**
```
/redis
```

The `/redis` skill resolves the current Sentinel primary, authenticates using credentials
from `.env`, and outputs a scored **HEALTHY / DEGRADED / CRITICAL** summary covering:
- Redis Sentinel cluster topology (all sentinels, quorum state)
- Replication lag across all replicas
- Memory usage vs `maxmemory`, eviction policy, fragmentation ratio
- Keyspace hit/miss ratio
- Connected clients, blocked clients, max clients
- AOF and RDB persistence state
- Slow command log (last 20 commands > 10ms)
- ACL user inventory
- Recent log anomalies

Additional tasks available once invoked: SCAN-based key search, session cache flush
(`FLUSHDB` — requires explicit engineer approval), per-user password rotation, and
no-TTL key deletion via server-side Lua (avoids exhausting `maxclients`).

---

## Phase 3: ElastiCache (AWS Managed Redis)

Run if `REDIS_HOST` is an ElastiCache endpoint and `AWS_ACCESS_KEY_ID` is in `.env`:

```bash
# Cluster status
aws elasticache describe-cache-clusters \
  --region {AWS_REGION} \
  --show-cache-node-info --output json 2>/dev/null \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for c in d.get('CacheClusters', []):
    print(c.get('CacheClusterId'), c.get('CacheClusterStatus'), c.get('Engine'), c.get('EngineVersion'))
    for n in c.get('CacheNodes', []):
        print('  Node:', n.get('CacheNodeId'), 'Status:', n.get('CacheNodeStatus'),
              'Endpoint:', n.get('Endpoint',{}).get('Address','?') + ':' + str(n.get('Endpoint',{}).get('Port','?')))
"

# CloudWatch metrics (1 hour window)
for METRIC in FreeableMemory Evictions CurrConnections CacheHits CacheMisses; do
  echo "=== ${METRIC} ==="
  aws cloudwatch get-metric-statistics \
    --namespace AWS/ElastiCache \
    --metric-name "${METRIC}" \
    --dimensions Name=CacheClusterId,Value={CLUSTER_ID} \
    --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v -1H +%Y-%m-%dT%H:%M:%SZ) \
    --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
    --period 300 --statistics Average Sum Maximum \
    --region {AWS_REGION} --output json 2>/dev/null \
    | python3 -c "
import sys, json
d = json.load(sys.stdin)
for p in sorted(d.get('Datapoints',[]), key=lambda x: x['Timestamp'])[-5:]:
    val = p.get('Average') or p.get('Sum') or p.get('Maximum', 0)
    print(f\"  {p['Timestamp'][:19]}: {round(val,1)}\")
"
done
```

---

## Phase 4: MongoDB Log Analysis

Collect and analyze `mongod.log` for slow queries and errors around the incident time.

```bash
# Docker
MONGO_LOG=${MONGO_LOG_PATH:-/var/log/mongodb/mongod.log}
MONGO_CTR=${MONGO_LOG_CONTAINER:-mongodb}

docker exec "${MONGO_CTR}" cat "${MONGO_LOG}" 2>/dev/null \
  > {project_path}/data/{TIMESTAMP}/mongod_raw.txt

# If not found, discover log path
if [ ! -s {project_path}/data/{TIMESTAMP}/mongod_raw.txt ]; then
  docker exec "${MONGO_CTR}" sh -c \
    "ps aux | grep mongod | grep -o '\-\-logpath [^ ]*' | awk '{print \$2}'" 2>/dev/null
fi

# Analyze slow queries (SLOW_QUERY filter)
grep -i "SLOW_QUERY\|planSummary" \
  {project_path}/data/{TIMESTAMP}/mongod_raw.txt \
  | grep "{INCIDENT_DATE}" \
  | head -100 > {project_path}/data/{TIMESTAMP}/mongod_slow.txt

# Parse durations (MongoDB 4.4+ JSON log format)
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

# Errors and connection events
grep -E '\"s\":\"E\"|\"s\":\"W\"|ERROR|WARNING|FAILED|too many' \
  {project_path}/data/{TIMESTAMP}/mongod_raw.txt \
  | grep "{INCIDENT_DATE}" | head -100

# Replication lag
grep -i "replication\|oplog\|PRIMARY\|SECONDARY\|lag" \
  {project_path}/data/{TIMESTAMP}/mongod_raw.txt \
  | grep "{INCIDENT_DATE}" | tail -30
```

---

## Phase 5: Redis Log Analysis

```bash
REDIS_LOG=${REDIS_LOG_PATH:-/var/log/redis/redis-server.log}
REDIS_CTR=${REDIS_LOG_CONTAINER:-redis}

docker exec "${REDIS_CTR}" cat "${REDIS_LOG}" 2>/dev/null \
  > {project_path}/data/{TIMESTAMP}/redis_raw.txt

# Redis often logs to stdout — fall back
if [ ! -s {project_path}/data/{TIMESTAMP}/redis_raw.txt ]; then
  docker logs "${REDIS_CTR}" 2>&1 > {project_path}/data/{TIMESTAMP}/redis_raw.txt
fi

# Memory / eviction events
grep -i "memory\|evict\|maxmemory\|OOM\|out of memory\|BGSAVE\|fork" \
  {project_path}/data/{TIMESTAMP}/redis_raw.txt | head -50

# Connection events
grep -i "connection\|client\|accepted\|closed" \
  {project_path}/data/{TIMESTAMP}/redis_raw.txt | tail -30

# AOF / RDB persistence
grep -i "RDB\|AOF\|saving\|saved\|bgsave\|rewrite" \
  {project_path}/data/{TIMESTAMP}/redis_raw.txt | head -20
```

**Key Redis log patterns:**
| Pattern | Meaning |
|---------|---------|
| `Can't save in background: fork: Cannot allocate memory` | Host memory too low for RDB fork — latency spike |
| `maxmemory policy eviction` | Redis at memory limit; keys (including Bull queue items) being evicted |
| `BGSAVE failed` | Persistence failure — check disk space |
| AOF rewrite > 60s | Disk I/O pressure slowing Redis write latency |
| Many `connection closed` in burst | Client disconnect storm — IAP restart or timeout cascade |

---

## Phase 6: DB Report

Save to `{project_path}/data/{TIMESTAMP}/database_report.md`:

```markdown
# Database Diagnostic Report
**Generated:** {YYYY-MM-DD HH:MM:SS UTC} | **Platform:** {PLATFORM_URL}

## MongoDB
| Metric | Value | Status |
|--------|-------|--------|
| Connectivity | OK/FAILED | ✅/🔴 |
| Connection pool | {current}/{available} ({%}) | ✅/⚠️ |
| Replica set | Standalone / {members} | ✅/⚠️ |
| jobs count | {N} | ✅/⚠️ |
| jobs index (status) | Present/Missing | ✅/🔴 |
| Slow queries (> 100ms) | {N} | ✅/⚠️ |
| Stuck running jobs | {N} | ✅/⚠️ |

## Redis
| Metric | Value | Status |
|--------|-------|--------|
| Connectivity | OK/FAILED | ✅/🔴 |
| Memory used | {X} / {max} | ✅/⚠️ |
| Evicted keys | {N} | ✅/⚠️ |
| Blocked clients | {N} | ✅/⚠️ |
| Bull queue wait | {N} items | ✅/⚠️ |
| Bull queue failed | {N} items | ✅/⚠️ |

## Root Cause
{One-sentence diagnosis}

## Recommended Actions
1. {Highest-impact fix — with consent flag if requires write}
2. {Secondary fix}
```

---

## Common Database Failure Patterns

| Symptom | Root Cause | Action |
|---------|-----------|--------|
| Jobs slow across the board | COLLSCAN on jobs collection | Add `{status:1}` index (consent required) |
| `connections.current` > 80% | Connection pool saturation | Check IAP pool settings, reduce pool size |
| `secs_running` > 30 on any op | Blocking query | Identify; kill with user consent |
| Redis evictions | `maxmemory` too low | Increase `maxmemory`; check eviction policy |
| Bull `failed` queue growing | Silent WFE task failures | Inspect failed job details |
| Bull `wait` > 100 | Task backlog | Increase WFE worker concurrency |
| `blocked_clients` > 0 | Bull worker BLPOP stall | Check WFE health |
| Replica set secondary lag > 10s | Replication falling behind | Check secondary oplog window, network |
| `COLLSCAN` on sessions | Missing TTL index on sessions | Add TTL index on `expires` (consent) |
| MongoDB `too many connections` | IAP connection pool too large | Reduce pool size in IAP config |
