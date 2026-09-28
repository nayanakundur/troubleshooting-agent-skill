---
name: redis
description: Administer the Redis sentinel cluster — health checks, replication status, key inspection, password rotation, cache flush, performance diagnostics, and full life report
allowed-tools: Bash, Read
---

# Redis Admin Skill

## When to Use

- Full health snapshot before/after a change or incident
- Performance investigation (memory pressure, eviction, hit rate, slow commands, client saturation)
- Sentinel cluster administration (quorum, failover, service management)
- Credential rotation or cache flush

---

## Step 1 — Identify Target Environment

If the user specified an environment (e.g. `/redis production`), use `$ARGUMENTS`.
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

- `REDIS_NODES` — all Redis node hostnames (for iterating across the cluster)
- `REDIS_PORT` — Redis port (default `6379`)
- `REDIS_MODE` — deployment mode: `standalone`, `sentinel`, or `cluster`
- `SENTINEL_PORT` — Sentinel port (default `26379`, sentinel mode only)
- `SENTINEL_MASTER` — Sentinel master name (sentinel mode only)
- `VAULT_ADDR` — HashiCorp Vault address
- `PLATFORM_VAULT_PATH` — Vault path for platform credentials
- `REDIS_SSH_USER` — SSH user for direct node access
- `REDIS_CONF` — Redis server config file: `/etc/redis/redis.conf`
- `SENTINEL_CONF` — Sentinel config file: `/etc/redis/sentinel.conf`

---

## Step 3 — Retrieve Credentials and Resolve Primary

```bash
source vault-env.sh
```

If `VAULT_TOKEN` is empty after sourcing, stop and ask the user to check `vault-env.sh`.

```bash
PLATFORM=$(curl -s -H "X-Vault-Token: $VAULT_TOKEN" \
  $VAULT_ADDR/<platform-vault-path> | jq -r '.data.data')

REDIS_ADMIN_PASS=$(echo "$PLATFORM" | jq -r '.redisAdmin')
REDIS_SENTINEL_ADMIN_PASS=$(echo "$PLATFORM" | jq -r '.redisSentinelAdmin')
REDIS_SENTINEL_PASS=$(echo "$PLATFORM" | jq -r '.redisSentinelPassword')
```

User model — Vault stores **passwords only**, usernames are fixed:

| Variable | Username | Vault Key | Purpose |
| --- | --- | --- | --- |
| `REDIS_ADMIN_PASS` | `admin` | `redisAdmin` | Redis server admin |
| `REDIS_ITENTIAL_PASS` | `itential` | `redisPassword` | Redis app user (reference only) |
| `REDIS_SENTINEL_ADMIN_PASS` | `admin` | `redisSentinelAdmin` | Sentinel admin |
| `REDIS_SENTINEL_PASS` | `sentineluser` | `redisSentinelPassword` | Sentinel discovery user |

Resolve current primary (always resolve at runtime, never assume):

```bash
REDIS_PRIMARY=$(redis-cli -h <any-sentinel-node> -p $SENTINEL_PORT \
  --user "admin" -a "$REDIS_SENTINEL_ADMIN_PASS" --no-auth-warning \
  SENTINEL get-master-addr-by-name $SENTINEL_MASTER | head -1)
```

> **TLS note:** TLS is not yet enabled in this deployment. When TLS is configured, add
> `--tls --cacert <ca-cert-path>` to all `redis-cli` commands.

---

## Step 4 — Execute Requested Task

If the user has not stated a task, present this menu:

```
Full Report
  1. Full life report (all sections below)

Sentinel & Replication
  2. Health check (PING all nodes)
  3. Replication status
  4. Sentinel quorum check
  5. Identify current primary
  6. List sentinel members

Data & Keys
  7. Key count and memory by database
  8. Inspect a key
  9. Scan keys matching a pattern

Diagnostics
 10. Slow log
 11. Connected clients
 12. List ACL users
 13. Search Redis server logs
 14. Search Sentinel logs

Service Management
 15. Redis service management          (status / start / stop / restart)
 16. Redis-Sentinel service management (status / start / stop / restart)

Destructive
 17. Restart Redis on a node           [requires confirmation]
 18. Change user password              [requires confirmation]
 19. Flush IAP session cache           [requires explicit approval]
 20. Delete keys with no TTL           [requires confirmation]
```

---

### Task 1: Full Life Report

Run Sections 1–10 in sequence and present as a single structured report.
After all sections, produce the scored summary (Step 5) and offer follow-up options (Step 6).

---

### Section 1 — Sentinel Health

```bash
# Quorum check
for node in $REDIS_NODES; do
  echo "=== $node — sentinel quorum ==="
  redis-cli -h $node -p $SENTINEL_PORT \
    --user "admin" -a "$REDIS_SENTINEL_ADMIN_PASS" --no-auth-warning \
    SENTINEL ckquorum $SENTINEL_MASTER 2>/dev/null
done

# Current primary
echo "=== Primary ==="
redis-cli -h <any-sentinel-node> -p $SENTINEL_PORT \
  --user "admin" -a "$REDIS_SENTINEL_ADMIN_PASS" --no-auth-warning \
  SENTINEL get-master-addr-by-name $SENTINEL_MASTER

# Sentinel members
echo "=== Sentinel members ==="
redis-cli -h <any-sentinel-node> -p $SENTINEL_PORT \
  --user "admin" -a "$REDIS_SENTINEL_ADMIN_PASS" --no-auth-warning \
  SENTINEL sentinels $SENTINEL_MASTER
```

**Evaluate:**
- All sentinels should respond to quorum check with `OK N usable Sentinels`.
- A majority (≥2 of 3) must be reachable for failover to work.
- Flag any sentinel that is down or reports a different primary than the others.

---

### Section 2 — Replication Status

```bash
# PING all nodes
for node in $REDIS_NODES; do
  result=$(redis-cli -h $node -p $REDIS_PORT \
    --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
    PING 2>/dev/null)
  echo "$node -> $result"
done

# Replication info from primary
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  INFO replication
```

**Evaluate:**
- All nodes should respond `PONG`.
- Primary should show `role:master` and `connected_slaves` equal to expected replica count.
- `master_sync_in_progress:1` → a replica is syncing (normal after restart, concerning if prolonged).
- Check `slave_repl_offset` vs `master_repl_offset` for lag. Large delta → replica is behind.
- Flag any replica with `master_link_status:down`.

---

### Section 3 — System Resource Health (all nodes)

```bash
for node in $REDIS_NODES; do
  echo "=== $node — CPU / Memory ==="
  ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$node \
    'vmstat 1 5; echo "---"; free -h' 2>/dev/null

  echo "=== $node — Disk I/O ==="
  ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$node \
    'iostat -x 1 3 2>/dev/null || echo "iostat not available"'
done
```

**Evaluate:**
- High `wa` → disk wait during RDB save or AOF fsync — Redis persistence is I/O-bound.
- Swap > 0 → Redis is spilling to disk — severe performance impact. Investigate immediately.
- High `r` (run queue) → CPU pressure from too many clients or Lua scripts.
- Redis is single-threaded for commands — even moderate CPU saturation causes latency spikes.

---

### Section 4 — Redis Memory and Eviction

```bash
for node in $REDIS_NODES; do
  echo "=== $node ==="
  redis-cli -h $node -p $REDIS_PORT \
    --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
    INFO memory 2>/dev/null | grep -E \
    "used_memory_human|used_memory_peak_human|maxmemory_human|maxmemory_policy|\
mem_fragmentation_ratio|rss_overhead_ratio|used_memory_startup"
done

# Eviction and hit rate stats from primary
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  INFO stats | grep -E "evicted_keys|keyspace_hits|keyspace_misses|rejected_connections|total_commands_processed"
```

**Evaluate:**
- `used_memory` approaching or exceeding `maxmemory` → eviction pressure or OOM risk.
- `evicted_keys` > 0 → Redis is evicting data to stay under `maxmemory`. Check eviction policy.
- `keyspace_hits` / (`keyspace_hits` + `keyspace_misses`) → cache hit rate. Below 80% is a concern.
- `rejected_connections` > 0 → `maxclients` was hit. **Critical** — causes client errors and can trigger Sentinel failover if the primary can't serve health checks.
- `mem_fragmentation_ratio` > 1.5 → high fragmentation; consider `MEMORY PURGE` or planned restart.
- `mem_fragmentation_ratio` < 1.0 → Redis is using swap. Severe.

---

### Section 5 — Client Connections

```bash
for node in $REDIS_NODES; do
  echo "=== $node ==="
  redis-cli -h $node -p $REDIS_PORT \
    --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
    INFO clients 2>/dev/null | grep -E \
    "connected_clients|blocked_clients|tracking_clients|maxclients|cluster_connections"

  # Check maxclients config
  redis-cli -h $node -p $REDIS_PORT \
    --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
    CONFIG GET maxclients 2>/dev/null
done
```

**Evaluate:**
- `connected_clients` approaching `maxclients` → high risk of `rejected_connections`.
- `blocked_clients` > 0 → clients waiting on `BLPOP`/`BRPOP`/`WAIT` — normal for Bull queues, but a growing count indicates stalled consumers.
- Flag any node where `connected_clients` is within 20% of `maxclients`.

**Warning:** Never use a per-key shell loop (`xargs` + individual `redis-cli` calls) when `connected_clients` is high — each subprocess opens a new connection and can push over `maxclients`, causing a Sentinel failover.

---

### Section 6 — Persistence Health

```bash
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  INFO persistence | grep -E \
  "rdb_enabled|rdb_last_bgsave_status|rdb_last_bgsave_time_sec|rdb_last_save_time|\
aof_enabled|aof_last_write_status|aof_last_rewrite_time_sec|loading"
```

**Evaluate:**
- `rdb_last_bgsave_status:err` → last RDB snapshot failed. Check logs for cause.
- `aof_last_write_status:err` → AOF write failed. Data loss risk if Redis restarts.
- `loading:1` → Redis is loading data from disk — not yet serving requests.
- `rdb_last_bgsave_time_sec` very high → RDB save is taking too long; check disk I/O (Section 3).
- If both RDB and AOF are enabled and neither is working → `MISCONF` errors likely.

---

### Section 7 — Keyspace

```bash
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  INFO keyspace
```

**Evaluate:**
- Report key count and TTL stats per database.
- A database with many keys and `avg_ttl:0` → many no-TTL keys. If memory is high, these are the candidates for deletion (Task 20).
- Unexpectedly large key counts may indicate Bull queue buildup or stale session data.

---

### Section 8 — Slow Log

```bash
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  SLOWLOG GET 20

redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  SLOWLOG LEN
```

**Evaluate:**
- Flag commands with execution time > 10ms (default slow threshold is 10000 µs).
- Repeated slow `SCAN` or `KEYS` calls → full keyspace iteration; check if caller can be optimized.
- Slow `EVAL` (Lua) → Lua scripts blocking the main thread. Redis is single-threaded.
- `SLOWLOG LEN` growing → slowlog not being reset; check frequency, not just recent entries.

---

### Section 9 — ACL Users

```bash
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  ACL LIST
```

Expected users: `admin`, `itential`, `replication`, `prometheus`, `sentinel`.
Flag any user that is not `on` (enabled) or any expected user that is missing.

---

### Section 10 — Recent Log Anomalies (all nodes)

**Redis server logs:**

```bash
for node in $REDIS_NODES; do
  echo "=== $node — redis.log ==="
  ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$node \
    'sudo grep -hE "^\s*[0-9]+:[A-Z] [0-9]+ [A-Za-z]+ [0-9]+ [0-9:.]+ # |OOM|out of memory|MISCONF|LOADING|MASTERDOWN|CLUSTERDOWN|Connection refused|ERR max number" \
     /var/log/redis/redis.log | tail -30' 2>/dev/null
done
```

**Sentinel logs:**

```bash
for node in $REDIS_NODES; do
  echo "=== $node — sentinel.log ==="
  ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$node \
    'sudo grep -hE "# |\+failover|\+odown|\+sdown|-odown|-sdown|\+elected|\+promoted|NOAUTH|WRONGPASS|WARNING|error|failed|Disconnecting" \
     /var/log/redis/sentinel.log | tail -30' 2>/dev/null
done
```

**Flag as significant:**
- `OOM` / `out of memory` — Redis hit `maxmemory` and cannot evict (eviction policy may be `noeviction`)
- `ERR max number of clients reached` — `maxclients` exhausted; clients were rejected
- `MISCONF` — RDB/AOF configuration conflict
- `LOADING` — Redis loading from disk, not yet serving
- `MASTERDOWN` — replica lost connection to primary
- `+odown` / `+failover-triggered` — Sentinel initiated failover
- `NOAUTH` / `WRONGPASS` — credential mismatch between Sentinel and Redis
- Any `#` line near a restart → crash or unclean shutdown

**Sentinel failover timeline:** `+sdown` → `+odown` → `+failover-triggered` → `+elected-leader` → `+promoted-slave` → `-odown`. Reconstruct this sequence if failover events are found.

---

## Step 5 — Scored Summary

After all sections, produce this summary:

```
## Redis Life Report — <environment> — <timestamp>

### Overall Status: [HEALTHY / DEGRADED / CRITICAL]

| Area                    | Status | Finding |
|-------------------------|--------|---------|
| Sentinel Health         | ✓/✗   | <summary> |
| Replication Status      | ✓/✗   | <summary> |
| System Resources        | ✓/✗   | <summary> |
| Memory & Eviction       | ✓/✗   | <summary> |
| Client Connections      | ✓/✗   | <summary> |
| Persistence Health      | ✓/✗   | <summary> |
| Keyspace                | ✓/✗   | <summary> |
| Slow Log                | ✓/✗   | <summary> |
| ACL Users               | ✓/✗   | <summary> |
| Log Anomalies           | ✓/✗   | <summary> |

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
  A. Inspect a specific key or scan a pattern
  B. View full slow log
  C. Search Redis or Sentinel logs with a custom pattern
  D. Redis service management (status / start / stop / restart)
  E. Redis-Sentinel service management
  F. Change user password              [destructive]
  G. Flush IAP session cache           [destructive — explicit approval]
  H. Delete keys with no TTL           [destructive — confirm]
```

---

### Task 2: Health Check

```bash
for node in $REDIS_NODES; do
  result=$(redis-cli -h $node -p $REDIS_PORT \
    --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
    PING 2>/dev/null)
  echo "$node -> $result"
done
```

All nodes should respond `PONG`.

---

### Task 3: Replication Status

```bash
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  INFO replication
```

Report `role`, `connected_slaves`, `master_sync_in_progress`, and per-replica offset lag.

---

### Task 4: Sentinel Quorum Check

```bash
redis-cli -h <any-sentinel-node> -p $SENTINEL_PORT \
  --user "admin" -a "$REDIS_SENTINEL_ADMIN_PASS" --no-auth-warning \
  SENTINEL ckquorum $SENTINEL_MASTER
```

Expected: `OK N usable Sentinels. Quorum and failover authorization can be reached`.

---

### Task 5: Identify Current Primary

```bash
redis-cli -h <any-sentinel-node> -p $SENTINEL_PORT \
  --user "admin" -a "$REDIS_SENTINEL_ADMIN_PASS" --no-auth-warning \
  SENTINEL get-master-addr-by-name $SENTINEL_MASTER
```

---

### Task 6: List Sentinel Members

```bash
redis-cli -h <any-sentinel-node> -p $SENTINEL_PORT \
  --user "admin" -a "$REDIS_SENTINEL_ADMIN_PASS" --no-auth-warning \
  SENTINEL sentinels $SENTINEL_MASTER
```

---

### Task 7: Key Count and Memory by Database

```bash
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  INFO keyspace
```

---

### Task 8: Inspect a Key

Ask the user for the key name if not provided.

```bash
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  TYPE <key-name>

redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  TTL <key-name>
```

---

### Task 9: Scan Keys Matching a Pattern

Ask the user for the pattern. Never use `KEYS` on a live system — use `SCAN` only.

```bash
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  --scan --pattern '<pattern>' | head -20
```

---

### Task 10: Slow Log

```bash
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  SLOWLOG GET 20

redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  SLOWLOG LEN
```

---

### Task 11: Connected Clients

```bash
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  CLIENT LIST
```

---

### Task 12: List ACL Users

```bash
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  ACL LIST
```

Expected users: `admin`, `itential`, `replication`, `prometheus`, `sentinel`.
Report any user that is not `on` or missing.

---

### Task 13: Search Redis Server Logs

If no pattern specified, run the default scan. Otherwise use the user's pattern.

**Default:**

```bash
for node in $REDIS_NODES; do
  echo "=== $node ==="
  ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$node \
    'sudo grep -hE "^\s*[0-9]+:[A-Z] [0-9]+ [A-Za-z]+ [0-9]+ [0-9:.]+ # |WARN|ERR|error|failed|OOM|out of memory|Connection refused|MISCONF|LOADING|MASTERDOWN|CLUSTERDOWN" /var/log/redis/redis.log | tail -50' 2>/dev/null
done
```

**Custom:**

```bash
for node in $REDIS_NODES; do
  echo "=== $node ==="
  ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$node \
    "sudo grep -iE '<pattern>' /var/log/redis/redis.log | tail -50" 2>/dev/null
done
```

---

### Task 14: Search Sentinel Logs

**Default:**

```bash
for node in $REDIS_NODES; do
  echo "=== $node ==="
  ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$node \
    'sudo grep -hE "# |\+failover|\+odown|\+sdown|-odown|-sdown|\+elected|\+promoted|NOAUTH|WRONGPASS|WARNING|error|failed|Disconnecting" /var/log/redis/sentinel.log | tail -50' 2>/dev/null
done
```

**Custom:**

```bash
for node in $REDIS_NODES; do
  echo "=== $node ==="
  ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$node \
    "sudo grep -iE '<pattern>' /var/log/redis/sentinel.log | tail -50" 2>/dev/null
done
```

Reconstruct failover timeline if events found: `+sdown` → `+odown` → `+failover-triggered` → `+elected-leader` → `+promoted-slave` → `-odown`.

---

### Task 15: Redis Service Management

Ask: which action (`status` / `start` / `stop` / `restart`) and which node.
Default to all nodes for `status`. Stop/restart require explicit confirmation.
In sentinel mode, always act on replicas before the primary.

```bash
# Status (all nodes)
for node in $REDIS_NODES; do
  echo "=== $node ==="
  ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$node 'sudo systemctl status redis' 2>/dev/null
done

# Action on one node
ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@<target-node> 'sudo systemctl <action> redis'
```

After `start` or `restart`, verify:

```bash
redis-cli -h <target-node> -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning PING
```

---

### Task 16: Redis-Sentinel Service Management

Ask: which action and which node. Stopping sentinel on a majority of nodes prevents failover.

```bash
# Status (all nodes)
for node in $REDIS_NODES; do
  echo "=== $node ==="
  ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$node 'sudo systemctl status redis-sentinel' 2>/dev/null
done

# Action on one node
ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@<target-node> 'sudo systemctl <action> redis-sentinel'
```

After `start` or `restart`, verify quorum:

```bash
redis-cli -h <target-node> -p $SENTINEL_PORT \
  --user "admin" -a "$REDIS_SENTINEL_ADMIN_PASS" --no-auth-warning \
  SENTINEL ckquorum $SENTINEL_MASTER
```

---

### Task 17: Restart Redis on a Node

**Confirm with user before executing.** In sentinel mode, restarting the primary triggers a failover. Always restart replicas before the primary.

```bash
ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@<target-node> 'sudo systemctl restart redis'

redis-cli -h <target-node> -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning PING
```

---

### Task 18: Change User Password

**High-risk — confirm with user first.** All clients using the affected user must be reconfigured and restarted.

```bash
# Apply new password
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  ACL SETUSER <username> >"<new-password>"

# Persist ACL so it survives restart
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  ACL SAVE
```

After changing: update Vault at `<platform-vault-path>`. If the changed user is `itential` or `sentinel`, update IAP configuration and restart IAP.

Verify:

```bash
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "<username>" -a "<new-password>" --no-auth-warning PING
```

---

### Task 19: Flush IAP Session Cache

> **WARNING:** This immediately logs out all active IAP users. Never run without explicit user approval. In production, confirm with the team first.

Ask the user to confirm the IAP session database number (default: `0`).

```bash
redis-cli -h $REDIS_PRIMARY -p $REDIS_PORT \
  --user "admin" -a "$REDIS_ADMIN_PASS" --no-auth-warning \
  -n <db-number> FLUSHDB
```

---

### Task 20: Delete Keys with No TTL

> **WARNING:** Deletes all keys in the target database with no expiry (TTL = -1).
> Sample first so the user understands what will be lost. Confirm before executing.

**Critical:** Do NOT use a per-key shell loop (`xargs` + individual `redis-cli` calls) — each subprocess opens a new connection and will exhaust `maxclients`, causing a Sentinel failover. Use a Lua script — all SCAN + TTL checks + DEL run server-side in a single connection.

**Step 1 — Resolve the current primary:**

```bash
REDIS_PRIMARY=$(redis-cli -h <any-sentinel-node> -p $SENTINEL_PORT \
  --user "admin" -a "$REDIS_SENTINEL_ADMIN_PASS" --no-auth-warning \
  SENTINEL get-master-addr-by-name $SENTINEL_MASTER | head -1)
```

**Step 2 — Sample no-TTL keys:**

```bash
ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$REDIS_PRIMARY \
  "redis-cli --user admin -a '$REDIS_ADMIN_PASS' --no-auth-warning -n <db> \
   EVAL \"
    local cursor = '0'
    local samples = {}
    local result = redis.call('SCAN', cursor, 'COUNT', 500)
    for _, key in ipairs(result[2]) do
      local ttl = redis.call('TTL', key)
      if ttl == -1 then
        table.insert(samples, key)
        if #samples >= 30 then break end
      end
    end
    return samples
   \" 0" 2>/dev/null
```

Show the sample to the user and confirm before proceeding.

**Step 3 — Write the Lua deletion script to the primary node:**

```bash
ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$REDIS_PRIMARY "cat > /tmp/del_no_ttl.lua << 'LUAEOF'
local cursor = ARGV[1]
local result = redis.call('SCAN', cursor, 'COUNT', 2000)
local next_cursor = result[1]
local keys = result[2]
local deleted = 0
local to_del = {}
for _, key in ipairs(keys) do
  local ttl = redis.call('TTL', key)
  if ttl == -1 then
    table.insert(to_del, key)
  end
end
if #to_del > 0 then
  deleted = redis.call('DEL', unpack(to_del))
end
return {next_cursor, tostring(deleted)}
LUAEOF
echo 'Lua script written'"
```

**Step 4 — Test one iteration:**

```bash
ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$REDIS_PRIMARY \
  "redis-cli --user admin -a '$REDIS_ADMIN_PASS' --no-auth-warning -n <db> \
   --eval /tmp/del_no_ttl.lua , 0"
```

Expected: two lines — `<next_cursor>` and `<deleted_count>`. Fix any errors before proceeding.

**Step 5 — Write the runner script (use Python to avoid heredoc expansion):**

```bash
ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$REDIS_PRIMARY \
  "python3 -c \"
script = '''#!/bin/bash
PASS=\\\"$REDIS_ADMIN_PASS\\\"
DB=<db>
TOTAL=0
CURSOR=0
ITERATION=0
while true; do
  RESULT=\\\$(redis-cli --user admin -a \\\"\\\$PASS\\\" --no-auth-warning -n \\\$DB --eval /tmp/del_no_ttl.lua , \\\"\\\$CURSOR\\\")
  NEXT=\\\$(echo \\\"\\\$RESULT\\\" | head -1)
  DELETED=\\\$(echo \\\"\\\$RESULT\\\" | tail -1 | grep -oE \\\"[0-9]+\\\" || echo 0)
  CURSOR=\\\$NEXT
  TOTAL=\\\$((TOTAL + DELETED))
  ITERATION=\\\$((ITERATION + 1))
  if (( ITERATION % 500 == 0 )); then
    REMAINING=\\\$(redis-cli --user admin -a \\\"\\\$PASS\\\" --no-auth-warning -n \\\$DB DBSIZE)
    echo \\\"\\\$(date): iter=\\\$ITERATION cursor=\\\$CURSOR deleted=\\\$TOTAL remaining=\\\$REMAINING\\\"
  fi
  [ \\\"\\\$CURSOR\\\" = \\\"0\\\" ] && break
done
echo \\\"\\\$(date): COMPLETE deleted=\\\$TOTAL\\\"
redis-cli --user admin -a \\\"\\\$PASS\\\" --no-auth-warning -n \\\$DB DBSIZE
'''
with open('/tmp/run_delete.sh','w') as f: f.write(script)
import os; os.chmod('/tmp/run_delete.sh',0o755)
print('ok')
\""
```

**Step 6 — Run in background and monitor:**

```bash
ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$REDIS_PRIMARY \
  'nohup /tmp/run_delete.sh > /tmp/redis_delete.log 2>&1 & echo "PID:$!"'

# Monitor progress
ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$REDIS_PRIMARY 'tail -f /tmp/redis_delete.log'
```

**Step 7 — Verify:**

```bash
ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$REDIS_PRIMARY \
  "redis-cli --user admin -a '$REDIS_ADMIN_PASS' --no-auth-warning INFO memory | \
   grep -E 'used_memory_human|maxmemory_human'"

ssh -i $SSH_KEY_PATH $REDIS_SSH_USER@$REDIS_PRIMARY \
  "redis-cli --user admin -a '$REDIS_ADMIN_PASS' --no-auth-warning INFO keyspace"
```

---

## Performance Decision Rules

| Situation | Action |
|-----------|--------|
| `used_memory` ≥ `maxmemory` | Check eviction policy; clear stale data or increase `maxmemory` |
| `evicted_keys` growing | Data is being evicted under memory pressure — review key TTLs and `maxmemory-policy` |
| `keyspace_hits` / total < 80% | Poor cache hit rate — check if expected keys are being set correctly |
| `rejected_connections` > 0 | `maxclients` was hit — increase limit or reduce connection churn |
| `mem_fragmentation_ratio` > 1.5 | High fragmentation — consider `MEMORY PURGE` or rolling restart |
| `mem_fragmentation_ratio` < 1.0 | Redis using swap — critical; investigate memory immediately |
| `blocked_clients` growing | Consumer stall — check Bull queue workers and IAP health |
| Slow log full of `SCAN`/`KEYS` | Replace with cursor-based SCAN with COUNT limit |
| Slow `EVAL` (Lua) | Lua blocks the main thread — optimize or split the script |
| `wa` high in vmstat | Persistence I/O pressure — check RDB save interval or AOF fsync mode |
| Swap > 0 on Redis host | Severe — Redis must never swap; investigate memory and restart if needed |
| Sentinel quorum lost | At least 2 sentinels must be reachable — check service status on all nodes |

---

## Notes for Agent Behavior

- Always resolve the primary via Sentinel before write operations — never hardcode a hostname.
- Never use per-key shell loops or `xargs` pipelines against a live Redis instance with many keys — exhausts `maxclients` and triggers Sentinel failover.
- `KEYS` is forbidden on live systems — use `SCAN` with a COUNT hint.
- Restarting the primary in sentinel mode triggers a failover — always restart replicas first.
- Stopping sentinel on a majority of nodes (≥2 of 3) disables automatic failover.
- Production changes (password rotation, flush, key deletion, service restarts) require explicit user confirmation.
