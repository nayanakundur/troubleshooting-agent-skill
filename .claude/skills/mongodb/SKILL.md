---
name: mongodb
description: Administer the MongoDB replica set — health checks, replica set status, user management, diagnostics, password rotation, performance troubleshooting, and full life report
allowed-tools: Bash, Read
---

# MongoDB Admin Skill

## When to Use

- Full health snapshot before/after a change or incident
- Performance investigation (slow queries, COLLSCAN, write contention, exporter load)
- Replica set administration (status, config, service management)
- Credential rotation (admin or IAP user)

---

## Step 1 — Identify Target Environment

If the user specified an environment (e.g. `/mongodb production`), use `$ARGUMENTS`.
Otherwise ask: "Which environment? (production / development / test)"

---

## Step 1a — Resolve SSH Key Path

1. Check persistent memory for `ssh_key_path`. If found, set `SSH_KEY_PATH` and proceed.
2. If not found, ask: "What is the path to your SSH private key? (e.g., `~/.ssh/id_rsa`)"
3. Save the answer to persistent memory as `ssh_key_path`.
4. Use `ssh -i $SSH_KEY_PATH` for all SSH commands.

---

## Step 2 — Load Environment Context

Read `environments/<env-name>/CLAUDE.md` and extract:

- `MONGO_NODES` — all replica set member hostnames
- `MONGO_PORT` — MongoDB port (default `27017`)
- `MONGO_RS` — replica set name
- `VAULT_ADDR` — HashiCorp Vault address
- `PLATFORM_VAULT_PATH` — Vault path for platform credentials
- `MONGO_SSH_USER` — SSH user for node access
- `MONGO_TLS_ENABLED` — `true` or `false`
- `MONGO_TLS_CA` — CA certificate path (required when TLS is enabled)

---

## Step 3 — Retrieve Credentials and Resolve Primary

```bash
source vault-env.sh
```

If `VAULT_TOKEN` is empty after sourcing, stop and ask the user to check `vault-env.sh`.

```bash
PLATFORM=$(curl -s -H "X-Vault-Token: $VAULT_TOKEN" \
  $VAULT_ADDR/<platform-vault-path> | jq -r '.data.data')

MONGO_ADMIN_PASS=$(echo "$PLATFORM" | jq -r '.mongoDbAdmin')
MONGO_IAP_PASS=$(echo "$PLATFORM" | jq -r '.mongoDb')
```

User model — Vault stores **passwords only**, usernames are fixed:

| Variable | Username | Auth DB | Vault Key | Purpose |
| --- | --- | --- | --- | --- |
| `MONGO_ADMIN_PASS` | `admin` | `admin` | `mongoDbAdmin` | Root admin — use for all skill operations |
| `MONGO_IAP_PASS` | `itential` | `itential` | `mongoDb` | App user, readWrite on `itential` database |

Set TLS flags once:

```bash
if [ "$MONGO_TLS_ENABLED" = "true" ]; then
  TLS_FLAGS="--tls --tlsCAFile $MONGO_TLS_CA"
else
  TLS_FLAGS=""
fi
```

Resolve primary (never assume — always resolve at runtime):

```bash
MONGO_PRIMARY=$(mongosh \
  "mongodb://admin:$MONGO_ADMIN_PASS@<any-node>:$MONGO_PORT/?authSource=admin" \
  $TLS_FLAGS --quiet \
  --eval "rs.isMaster().primary" 2>/dev/null | tr -d '"' | cut -d: -f1)

MONGODB_URI="mongodb://admin:$MONGO_ADMIN_PASS@$MONGO_PRIMARY:$MONGO_PORT/?authSource=admin&replicaSet=$MONGO_RS"
```

---

## Step 4 — Execute Requested Task

If the user has not stated a task, present this menu:

```
Full Report
  1. Full life report (all sections below)

Quick Checks
  2. Replica set health
  3. Replica set configuration
  4. Identify current primary
  5. List databases
  6. List collections in a database
  7. View current connections
  8. Slow query report (profiler)
  9. Search MongoDB logs

Performance Diagnostics
  10. Write contention and locks
  11. Active operations (currentOp)
  12. Explain a query (executionStats)
  13. Index inventory (jobs + tasks)
  14. Transparent Huge Pages (THP) check

Service & Config
  15. View mongod configuration
  16. mongod service management        (status / start / stop / restart)

Destructive
  17. Change admin password            [requires confirmation]
  18. Change IAP service account password  [requires confirmation]
  19. Compact a collection             [requires confirmation]
  20. Cancel running jobs (overload recovery)  [requires confirmation]
  21. Fast resync a member (physical file-copy seed)  [requires confirmation]
```

---

### Task 1: Full Life Report

Run Sections 1–11 in sequence and present as a single structured report.
After all sections, produce the scored summary (Step 5) and offer follow-up options (Step 6).

---

### Section 1 — Replica Set Health

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval "
  JSON.stringify(rs.status(), null, 2)
" | jq '{
    set: .set,
    date: .date,
    myState: .myState,
    term: .term,
    members: [.members[] | {
      name,
      stateStr,
      health,
      uptime,
      lastHeartbeatMessage,
      syncSourceHost
    }]
  }'
```

All members should show `health: 1`. Flag any member with `health: 0`, `stateStr: RECOVERING`, or `stateStr: UNKNOWN`.

---

### Section 2 — Replica Set Configuration

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval "
  const cfg = rs.conf();
  print(JSON.stringify({
    name: cfg._id,
    version: cfg.version,
    members: cfg.members.map(m => ({
      host: m.host,
      priority: m.priority,
      votes: m.votes,
      hidden: m.hidden,
      slaveDelay: m.slaveDelay || m.secondaryDelaySecs,
      tags: m.tags
    }))
  }, null, 2));
"
```

Flag any member with `priority: 0` or `hidden: true` — affects election eligibility.

---

### Section 3 — System Resource Health (all nodes)

```bash
for node in $MONGO_NODES; do
  echo "=== $node — CPU / Memory ==="
  ssh -i $SSH_KEY_PATH $MONGO_SSH_USER@$node \
    'vmstat 1 5; echo "---"; free -h' 2>/dev/null

  echo "=== $node — Disk I/O ==="
  ssh -i $SSH_KEY_PATH $MONGO_SSH_USER@$node \
    'iostat -x 1 5 2>/dev/null || echo "iostat not available"'
done
```

- High `r` (run queue) relative to CPU count → CPU saturation.
- `wa` consistently above ~5% → disk wait.
- Swap > 0 → memory pressure; MongoDB should never swap.
- High disk `await` or `%util` → I/O bottleneck.

---

### Section 4 — MongoDB Memory and WiredTiger Cache

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval "
  const mem = db.serverStatus().mem;
  const cache = db.serverStatus().wiredTiger.cache;
  print(JSON.stringify({
    memory: {
      resident_MB: mem.resident,
      virtual_MB: mem.virtual,
      mapped_MB: mem.mapped
    },
    wiredTiger: {
      bytes_in_cache: cache['bytes currently in the cache'],
      max_bytes_configured: cache['maximum bytes configured'],
      cache_full_pct: (cache['bytes currently in the cache'] /
        cache['maximum bytes configured'] * 100).toFixed(1) + '%',
      operations_timed_out: cache['operations timed out waiting for space in cache'],
      eviction_unable_to_reach_goal: cache['eviction server unable to reach eviction goal'],
      pages_evicted_modified: cache['modified pages evicted']
    }
  }, null, 2));
"
```

Flag `operations_timed_out` > 0 (cache starvation) and `cache_full_pct` > 95%.
Do not recommend changing WiredTiger cache size unless these stats clearly justify it.

---

### Section 5 — Write Contention and Locks

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval "
  print(JSON.stringify({
    writeConflicts: db.serverStatus().metrics.operation.writeConflicts,
    locks: db.serverStatus().locks
  }, null, 2));
"
```

Rising `writeConflicts` → multiple workers updating the same document (application pattern problem, not storage). Correlate with `currentOp` to find the responsible namespace.

---

### Section 6 — Active Operations

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval "
  const slow = db.currentOp({ secs_running: { \$gt: 1 } });
  const agg = db.currentOp({ 'command.aggregate': { \$exists: true } });
  print(JSON.stringify({
    total_active: db.currentOp().inprog.length,
    slow_ops_over_1s: slow.inprog.map(op => ({
      opid: op.opid,
      op: op.op,
      ns: op.ns,
      secs_running: op.secs_running,
      planSummary: op.planSummary,
      client: op.client
    })),
    active_aggregations: agg.inprog.map(op => ({
      opid: op.opid,
      ns: op.ns,
      secs_running: op.secs_running,
      planSummary: op.planSummary
    }))
  }, null, 2));
"
```

**COLLSCAN distinction:**
- `tailable: true`, `awaitData: true`, `$changeStream`, oplog readers → **normal**, even if they show `COLLSCAN`. Do not flag these.
- `op: "command"` + `aggregate: "jobs"/"tasks"` + `secs_running > 1` repeatedly → **bad** pattern. Flag and investigate with `explain`.

---

### Section 7 — Slow Query History (Profiler)

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval "
  const db_name = 'itential';
  const level = db.getSiblingDB(db_name).getProfilingStatus();
  if (level.was === 0) {
    print('Profiler is OFF — no slow query history.');
    print('Enable: db.getSiblingDB(\"itential\").setProfilingLevel(1, {slowms: 100})');
  } else {
    db.getSiblingDB(db_name).system.profile
      .find({}, { ns: 1, millis: 1, op: 1, 'command.aggregate': 1, 'command.find': 1 })
      .sort({ millis: -1 })
      .limit(20)
      .forEach(doc => print(JSON.stringify(doc)));
  }
"
```

---

### Section 8 — Database Sizes and Connections

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval "
  const dbs = db.adminCommand({ listDatabases: 1 });
  const conn = db.serverStatus().connections;
  print(JSON.stringify({
    connections: {
      current: conn.current,
      available: conn.available,
      totalCreated: conn.totalCreated
    },
    databases: dbs.databases
      .sort((a, b) => b.sizeOnDisk - a.sizeOnDisk)
      .map(d => ({
        name: d.name,
        sizeOnDisk_MB: (d.sizeOnDisk / 1024 / 1024).toFixed(1)
      }))
  }, null, 2));
"
```

---

### Section 9 — Index Inventory (jobs and tasks)

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval "
  const itential = db.getSiblingDB('itential');
  print('=== jobs indexes ===');
  itential.jobs.getIndexes().forEach(i => print(JSON.stringify(i)));
  print('=== tasks indexes ===');
  itential.tasks.getIndexes().forEach(i => print(JSON.stringify(i)));
"
```

Index existence does not confirm usefulness. If a query shows `COLLSCAN` despite an index, or if `totalDocsExamined` is near collection size with `IXSCAN`, the index is not helping. Use `explain("executionStats")` to confirm.

---

### Section 10 — Transparent Huge Pages (THP)

MongoDB 8 supports THP — any of `[never]`, `[madvise]`, or `[always]` is acceptable. Record the current setting for the report; do not recommend disabling.

```bash
for node in $MONGO_NODES; do
  echo "=== $node — THP ==="
  ssh -i $SSH_KEY_PATH $MONGO_SSH_USER@$node \
    'cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || echo "path not found";
     cat /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null || echo "path not found"' 2>/dev/null
done
```

---

### Section 11 — Recent Log Anomalies (all nodes)

```bash
for node in $MONGO_NODES; do
  echo "=== $node — recent errors / warnings ==="
  ssh -i $SSH_KEY_PATH $MONGO_SSH_USER@$node \
    'sudo grep -E "\"s\":\"E\"|\"s\":\"W\"|Slow query|NETWORK|REPL|assertion|INITSYNC" \
     /var/log/mongodb/mongod.log | tail -30' 2>/dev/null
done
```

Flag: `"s":"E"` (error), `"s":"W"` (warning), `REPL` (elections/state changes), `assertion` (serious, may precede crash), `INITSYNC` (member catching up).

---

## Step 5 — Scored Summary

After all sections, produce this summary:

```
## MongoDB Life Report — <environment> — <timestamp>

### Overall Status: [HEALTHY / DEGRADED / CRITICAL]

| Area                  | Status | Finding |
|-----------------------|--------|---------|
| Replica Set Health    | ✓/✗   | <summary> |
| System Resources      | ✓/✗   | <summary> |
| WiredTiger Cache      | ✓/✗   | <summary> |
| Write Contention      | ✓/✗   | <summary> |
| Active Operations     | ✓/✗   | <summary> |
| Slow Query History    | ✓/✗   | <summary> |
| Connections           | ✓/✗   | <summary> |
| Indexes               | ✓/✗   | <summary> |
| THP Configuration     | ℹ      | <current setting — informational; any value OK on Mongo 8> |
| Log Anomalies         | ✓/✗   | <summary> |

### Recommended Actions (ranked by impact / risk)
1. <lowest-risk action>
2. ...

### Items Requiring Confirmation Before Action
- <any destructive or high-risk finding>
```

---

## Step 6 — Follow-Up Tasks

After the report, offer these follow-up options based on findings:

```
Follow-up options:
  A. Run explain("executionStats") on a specific query
  B. Enable slow query profiler
  C. View full log output for a specific node
  D. mongod service management (status / start / stop / restart)
  E. View mongod configuration
  F. Change admin password                  [destructive]
  G. Change IAP service account password    [destructive]
  H. Compact a collection                   [destructive]
  I. Cancel running jobs (overload recovery)  [destructive]
  J. Fast resync a member (physical file-copy seed)  [destructive]
```

---

### Task 10: Write Contention and Locks (standalone)

Same as Section 5. Collect `writeConflicts` and `locks` from `serverStatus`.

Decision rules:
- `writeConflicts` high or rising → multiple workers updating the same document → application pattern fix, not hardware.
- Recommend schema changes (append-only events, pre-aggregated counters) over hardware scaling.
- Only recommend more CPU after query shape, contention, and polling frequency are already addressed.

---

### Task 11: Active Operations (standalone)

Same as Section 6. Use targeted filters:

```bash
# Long-running ops
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet \
  --eval "print(JSON.stringify(db.currentOp({ secs_running: { \$gt: 1 } }), null, 2))"

# Aggregations on jobs/tasks
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet \
  --eval "print(JSON.stringify(db.currentOp({ 'command.aggregate': { \$in: ['jobs', 'tasks'] } }), null, 2))"
```

---

### Task 12: Explain a Query

Ask the user for the collection and query/aggregation pipeline.

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval "
  print(JSON.stringify(
    db.getSiblingDB('itential').<collection>.explain('executionStats').aggregate([...]),
    null, 2
  ));
"
```

Interpret results:
- `COLLSCAN` + `totalDocsExamined` near collection size → bad query shape. Fix query or add index.
- `IXSCAN` but `totalDocsExamined` still near collection size → index not materially helping.
- `IXSCAN` + low `totalDocsExamined` → good.

Do not recommend adding an index if a similar leading key already exists.

---

### Task 13: Index Inventory (standalone)

Same as Section 9.

---

### Task 14: THP Check (standalone)

Same as Section 10.

---

### Task 15: View mongod Configuration

Ask which node if not specified; default to current primary.

```bash
ssh -i $SSH_KEY_PATH $MONGO_SSH_USER@<target-node> 'sudo cat /etc/mongod.conf'
```

---

### Task 16: mongod Service Management

Ask: which action (`status` / `start` / `stop` / `restart`) and which node.
Default to all nodes for `status`. Stop/restart require explicit confirmation.

In a replica set, always act on secondaries before the primary — restarting/stopping the primary triggers an election.

```bash
# Status (all nodes)
for node in $MONGO_NODES; do
  echo "=== $node ==="
  ssh -i $SSH_KEY_PATH $MONGO_SSH_USER@$node 'sudo systemctl status mongod' 2>/dev/null
done

# Action on one node (confirm first for stop/restart)
ssh -i $SSH_KEY_PATH $MONGO_SSH_USER@<target-node> 'sudo systemctl <action> mongod'
```

After `start` or `restart`, verify the node rejoins the replica set:

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval "
  print(JSON.stringify(
    rs.status().members.find(m => m.name.startsWith('<target-node>')),
    null, 2
  ));
"
```

Expected: `stateStr: "SECONDARY"` (or `"PRIMARY"` if it won the election).

---

### Task 17: Change Admin Password

**High-risk — confirm with user first.**

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet \
  --eval "db.adminCommand({updateUser: 'admin', pwd: '<new-password>'})"
```

After changing: update Vault at `<platform-vault-path>` key `mongoDbAdmin`, then verify:

```bash
mongosh \
  "mongodb://admin:<new-password>@$MONGO_PRIMARY:$MONGO_PORT/?authSource=admin&replicaSet=$MONGO_RS" \
  $TLS_FLAGS --quiet --eval "db.adminCommand({ping: 1})"
```

---

### Task 18: Change IAP Service Account Password

**Confirm with user first.** IAP must be updated and restarted after this change.

The `itential` user authenticates against the `itential` database:

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet \
  --eval "db.getSiblingDB('itential').updateUser('itential', {pwd: '<new-password>'})"
```

After changing:
1. Update Vault at `<platform-vault-path>` key `mongoDb`.
2. Update the IAP MongoDB connection config.
3. Restart IAP to pick up the new credential.
4. Verify:

```bash
mongosh \
  "mongodb://itential:<new-password>@$MONGO_PRIMARY:$MONGO_PORT/itential?authSource=itential&replicaSet=$MONGO_RS" \
  $TLS_FLAGS --quiet --eval "db.adminCommand({ping: 1})"
```

---

### Task 19: Compact a Collection

**Confirm with user first.** `compact` blocks the collection for its duration. Run on a secondary, or during a maintenance window on the primary.

Ask the user for the database and collection name.

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet \
  --eval "db.getSiblingDB('<db>').runCommand({compact: '<collection>'})"
```

---

### Task 20: Cancel Running Jobs (Overload Recovery)

**Confirm with user first. Destructive — mutates job state directly.**

**When to use this vs. the API.** Normal job cancellation goes through the IAP API
(`POST /operations-manager/jobs/cancel`, see the `itential-platform` skill). Use this direct
MongoDB method only in the **overload / unresponsive** scenario:

- IAP is saturated and the API is not responding, so the API cancel path is unavailable.
- Restarting IAP does not help — on startup the workflow engine resumes the jobs that were
  `running`, saturates again, and gets stuck in the same state.

The fix is to mark the stuck jobs `canceled` in the database **while IAP is stopped**, so that on
the next start there is nothing left to resume.

**Order of operations matters — do not skip the stop:**

1. **Stop IAP** (all app nodes) via the `itential-platform` skill / service management.
   This must happen first. If IAP is still running, the engine holds these jobs in memory and can
   overwrite the status right back, and you risk mutating jobs mid-write.
2. Confirm IAP is fully stopped before touching the database.
3. **Preview** how many jobs will be affected (dry run — no writes):

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval "
  const itential = db.getSiblingDB('itential');
  print(JSON.stringify({
    running_jobs: itential.jobs.countDocuments({ status: 'running' })
  }, null, 2));
"
```

4. Confirm the count with the user, then **cancel**. This flips the job to `canceled` and flips any
   still-`running` task to `canceled` while leaving already-finished tasks untouched. The
   `$ifNull` guard keeps a job with no `tasks` field from erroring the pipeline:

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval '
  const itential = db.getSiblingDB("itential");
  const filter = { status: "running" };   // scope: all running jobs
  const res = itential.jobs.updateMany(filter, [
    { $set: {
        status: "canceled",
        tasks: { $arrayToObject: { $map: {
          input: { $objectToArray: { $ifNull: ["$tasks", {}] } },
          as: "task",
          in: {
            k: "$$task.k",
            v: { $mergeObjects: ["$$task.v", {
              status: { $cond: [
                { $eq: ["$$task.v.status", "running"] },
                "canceled",
                "$$task.v.status"
              ] }
            }] }
          }
        }}}
    }}
  ]);
  print(JSON.stringify({ matched: res.matchedCount, modified: res.modifiedCount }, null, 2));
'
```

To cancel only specific jobs instead of everything running, scope the filter by id:
`const filter = { _id: { $in: [ObjectId("..."), ObjectId("...")] } };`

5. **Verify** nothing is left running:

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval "
  print(db.getSiblingDB('itential').jobs.countDocuments({ status: 'running' }));
"
```

Expected: `0` (or the count of any jobs you intentionally left out of scope).

6. **Start IAP** again. It should come up clean with no jobs to resume.

Notes:
- This is idempotent — safe to re-run; already-canceled jobs won't match `status: "running"`.
- The update records status only, matching the validated recovery query. If dashboards need the
  jobs to show a completion time, `metrics.end_time: "$$NOW"` can be added to the `$set`, but
  confirm IAP's expected type for that field before relying on it.

---

### Task 21: Fast Resync a Member (Physical File-Copy Seed)

**Confirm with user first. Destructive to the target member (safe cluster-wide).**

**When to use.** A member is stale beyond the oplog window and needs a full resync, but a
**logical** initial sync is too slow or cannot complete:

- The dataset is large and logical sync spends most of its time rebuilding indexes (e.g. huge
  `jobs`/`tasks` collections), running many hours.
- The node's VM keeps resetting mid-clone (see the `proxmox_hypervisor` context — host memory
  pressure), so a logical sync **restarts from zero every reboot and never finishes**.
- This is Community edition, so Enterprise `initialSyncMethod: fileCopyBased` is unavailable.

**Why it works.** It copies a healthy secondary's WiredTiger files directly — no index-build
phase, so it runs near line-rate (hundreds of MB/s). It completes in one short window, and once
the target has a **complete dataset**, later reboots only need a quick **oplog catch-up (minutes),
not a resync**.

**Safety model.** The copy source is `fsyncLock`ed so its files are consistent. While the source
is frozen and the target is down, `w:majority` cannot be satisfied — so the default write concern
is temporarily set to `w:1` to avoid hanging application writes, and restored to `majority`
afterward. Always use a **secondary** as the source (never the primary), keep a
majority-serving member up, and guarantee unlock/restore even on failure. `fast-resync-seed.sh`
enforces these as **fail-closed pre-flight checks** — source must be a `SECONDARY`, the target must
not be the current primary, and majority must still hold with the target down — before any
`fsyncLock` or write-concern change.

Prerequisites: a healthy, caught-up SECONDARY source; node-to-node SSH + passwordless `sudo` +
`rsync` on both nodes; oplog window (from Section 8 / `rs.printReplicationInfo`) comfortably larger
than the copy time; enough disk on the target.

**1. Pre-flight** — confirm the source is healthy and current, and node-to-node copy works:

```bash
# source SECONDARY caught up + oplog window
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval "
  const s=rs.status(), pr=s.members.find(m=>m.stateStr==='PRIMARY');
  s.members.forEach(m=>print(m.name+' '+m.stateStr+' lag='+((pr.optimeDate-m.optimeDate)/1000)+'s'));
"
# node-to-node: from the SOURCE node, can it ssh to the TARGET and does rsync/sudo exist?
ssh -i $SSH_KEY $MONGO_SSH_USER@<source-node> \
  'command -v rsync; sudo -n true && echo sudo-ok; \
   ssh -i ~/.ssh/<node-key> -o StrictHostKeyChecking=accept-new <ssh-user>@<target-node> hostname'
```

**2. Lower the target member's priority to 0** (prevents an auto-failback onto a still-catching-up
node; keep votes so majority math is unchanged), on the primary:

```bash
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval "
  const c=rs.conf(); c.members.find(m=>m.host==='<target-node>:$MONGO_PORT').priority=0; rs.reconfig(c);
"
```

**3. Stop and wipe the target** (guarded by hostname):

```bash
ssh -i $SSH_KEY $MONGO_SSH_USER@<target-node> '
  [ "$(hostname)" = "<target-node>" ] || { echo ABORT; exit 1; }
  sudo systemctl stop mongod && sleep 3
  sudo rm -rf /var/lib/mongo/* /var/lib/mongo/.[!.]* '
```

**4. Run the seed** — `fast-resync-seed.sh` in this skill directory does the safety-critical block
(WC→`w:1` → `fsyncLock` source → verify frozen → `rsync` → trap-guaranteed unlock + restore
`majority`). Export its inputs and run it (long-running — background it):

```bash
export PRIMARY_URI="mongodb://admin:$MONGO_ADMIN_PASS@<primary>:$MONGO_PORT/?authSource=admin&directConnection=true"
export SOURCE_URI="mongodb://admin:$MONGO_ADMIN_PASS@<source-node>:$MONGO_PORT/?authSource=admin&directConnection=true"
export TLS="$TLS_FLAGS" SSH_KEY="$SSH_KEY" SSH_USER="$MONGO_SSH_USER"
export SOURCE_HOST="<source-node>" TARGET_HOST="<target-node>"
export TARGET_MEMBER="<target-node>:$MONGO_PORT"   # member id as in rs.conf (for guardrails)
export NODE_KEY="~/.ssh/<node-key>"        # key ON the source node used to reach the target
# export NODE_SSH_EXTRA="-F /dev/null"     # only if the source node's ~/.ssh/config is broken
./fast-resync-seed.sh    # progress in /tmp/mongo-fast-resync.log
```

**5. Bring the target up on the seeded data** — fix ownership, start, verify it joins and
catches up:

```bash
ssh -i $SSH_KEY $MONGO_SSH_USER@<target-node> '
  sudo rm -f /var/lib/mongo/mongod.lock
  sudo chown -R mongod:mongod /var/lib/mongo
  sudo systemctl start mongod'
# then watch it converge (should reach SECONDARY, lag -> ~0)
mongosh "$MONGODB_URI" $TLS_FLAGS --quiet --eval "
  const s=rs.status(), pr=s.members.find(m=>m.stateStr==='PRIMARY');
  const m=s.members.find(x=>x.name==='<target-node>:$MONGO_PORT');
  print(m.stateStr+' lag='+((pr.optimeDate-m.optimeDate)/1000)+'s');
"
```

**6. Deliberate failback (later).** Once the target is caught up and stable, restore its priority
as a controlled election — a separate, confirmed step, not automatic.

Notes:
- `fsyncLock` freezes oplog application on the source; it catches up from the oplog after unlock
  (must be within the oplog window). The `--info=stats2` rsync summary reports bytes/throughput.
- The seed preserves the source's `local` DB (oplog + shared RS config); the target identifies
  itself by hostname from the config and resumes as a SECONDARY at the lower priority.
- If the target VM resets mid-copy, just retry from step 3 — nothing cluster-side is harmed.

---

## Offline Collection (no Claude, air-gapped)

When there's no interactive path — air-gapped environments, unattended captures, or a
hand-off-a-bundle situation — use the two collector scripts in this skill directory instead of
running the sections live:

- `offline-collect-cluster.sh` — run **once** from any node with `mongosh` + credentials.
  Produces `/tmp/mongo-life-cluster.txt` (Sections 1–9).
- `offline-collect-node.sh` — run on **each** replica member. Produces
  `/tmp/mongo-life-<host>.txt` (Sections 3, 10, 11; guest-VM scope only).

The cluster script is configured via `MONGO_URI`, `MONGO_TLS`, and optional `MONGO_DB` env vars.

Two docs cover usage, by audience:

- [`OFFLINE-COLLECTION.md`](OFFLINE-COLLECTION.md) — **engineer-facing** quick reference
  (creating a least-privilege `monitor` user, credentials, TLS on/off, bundling, and how output
  maps to the sections below).
- [`CUSTOMER-DATA-COLLECTION.md`](CUSTOMER-DATA-COLLECTION.md) — **customer-shareable runbook**.
  Self-contained, no internal references; hand this (plus the two scripts) to a customer to run
  in an air-gapped/remote environment and return a `.tgz` bundle.

To analyze an offline capture, ingest the bundled `.txt` files and produce the scored summary
(Step 5) exactly as for a live run.

---

## Performance Decision Rules

| Situation | Action |
|-----------|--------|
| `COLLSCAN` on large collection | Fix query shape or add selective index |
| `IXSCAN` but `totalDocsExamined` ≈ collection size | Index not helping — fix query selectivity |
| Full-table status aggregation running frequently | Add `$match` filter or cache results; consider change stream approach |
| High `writeConflicts` | Application pattern problem — use append-only events or pre-aggregated counters |
| Exporter/dashboard scanning every scrape | Reduce frequency; cache snapshot results |
| Swap > 0 on MongoDB host | Investigate memory pressure; MongoDB should never swap |
| CPU saturated | Only address after query shape and contention are fixed |

---

## Notes for Agent Behavior

- Prefer evidence over assumptions.
- Quote exact stage names (`COLLSCAN`, `IXSCAN`, `FETCH`, `GROUP`) when explaining findings.
- A `COLLSCAN` in `currentOp` is not always a problem — distinguish change stream / oplog cursors (normal) from full-table aggregations (bad).
- Do not recommend WiredTiger cache tuning without evidence from Section 4 stats.
- Production changes (index creation, compaction, password rotation) require explicit user confirmation.
- If the user is mid-test or mid-incident, all diagnostic sections are safe to run non-destructively.
- Use `JSON.stringify()` (not `printjson()`) in all `mongosh --eval` blocks to produce valid JSON for `jq`.
