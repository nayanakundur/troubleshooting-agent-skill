---
name: troubleshoot-infra
description: Troubleshoot infrastructure and OS-level issues for IAP, IAG, MongoDB, Redis, containers, EKS, ElastiCache, and load balancers. Covers CPU, memory, disk, file descriptors, network connectivity, and container crash detection.
argument-hint: "[component: iap|iag|mongodb|redis|kafka|eks|all]"
---

# Troubleshoot Infrastructure

**Owns:** Host-level and container-level resource diagnostics for all IAP platform components. Covers Docker, Kubernetes/EKS, VM/SSH, and AWS-managed services (ElastiCache, EKS node groups).
**Use when:** High CPU or memory on any platform component, disk full errors, container crash-looping, OOMKilled containers, inter-service connectivity failures, EKS node pressure, or ElastiCache memory saturation.

---

## Cloud Customer Topology (itential-saas) — Read First

If `cloud_customer_flag: true` is set in `ticket_context.md`, the customer is on Itential-managed cloud (`*.itential.io`). Adjust all SSH and container diagnostics accordingly:

| Target | On-prem | Cloud (itential-saas) |
|---|---|---|
| IAP application nodes | SSH available | **No SSH** — Itential-managed; request logs from cloud ops |
| IAP container logs | `docker logs {container}` via SSH | **Not available directly** — request from cloud ops |
| IAG nodes | SSH available | SSH available — customer on-prem; use `.env` SSH_HOST_N with `SSH_ROLE=gateway` |
| MongoDB / Redis | SSH + mongosh / redis-cli | Via URL in `.env` if cloud ops provided credentials |
| Adapter connectivity (OFFLINE) | Check host/port/creds | **Also check NAT IP whitelist** — Itential NATs adapter traffic; target must whitelist Itential's NAT IP |

For cloud customers, skip all Phase 7 (SSH) steps that target IAP nodes. Limit SSH phases to IAG on-prem hosts and any separately hosted on-prem DB nodes.

---

## CRITICAL SAFETY RULES

- **Read-only diagnostics** — no `docker rm`, `kubectl delete`, `systemctl restart`, `docker system prune` without explicit user consent
- **No DB writes** — read-only MongoDB and Redis queries only
- **Confirm before any destructive action** (container restart, service stop, log truncation)
- **Read `.env` for credentials** — never ask for credentials already in `.env`

---

## Auth Reuse

Check `{project_path}/.auth.json` — reuse if `platform_url` matches `.env` and `timestamp` < 50 minutes old. Otherwise re-authenticate and save.

```bash
# Password auth
curl -sk -X POST "{PLATFORM_URL}/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "{USERNAME}", "password": "{PASSWORD}"}'
```

---

## Phase 1: Container Overview (Docker)

### Step 1a — All Container Status

```bash
# All containers — running, stopped, restarting
docker ps -a --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.RunningFor}}\t{{.Ports}}" 2>/dev/null

# Focus on Itential stack
docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}" 2>/dev/null \
  | grep -E "platform|mongodb|redis|kafka|iag|itential|NAME"
```

### Step 1b — Resource Usage Snapshot

```bash
docker stats --no-stream --format \
  "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}" 2>/dev/null \
  | grep -E "platform|mongodb|redis|kafka|iag|NAME"
```

### Step 1c — Crash Loop Detection

```bash
docker inspect platform mongodb redis apache-kafka 2>/dev/null \
  | python3 -c "
import sys, json
for c in json.load(sys.stdin):
    name      = c['Name'].lstrip('/')
    state     = c['State']
    restarts  = state.get('RestartCount', 0)
    exit_code = state.get('ExitCode', 0)
    oom       = state.get('OOMKilled', False)
    status    = state.get('Status','?')
    flag = '🔴' if (restarts > 0 or exit_code != 0 or oom) else '✅'
    print(f'{flag} {name}: status={status}  restarts={restarts}  exit_code={exit_code}  OOMKilled={oom}')
"

# Last 100 log lines from any crash-looping container
# Replace {CONTAINER_NAME} with the crashing container
docker logs --tail 100 {CONTAINER_NAME} 2>&1 \
  | grep -i "error\|fatal\|oom\|killed\|failed\|panic\|exception" | head -50
```

---

## Phase 2: Disk Usage

Disk-full conditions cause MongoDB writes to fail, logs to stop rotating, and containers to crash.

```bash
# Host-level disk
df -h 2>/dev/null | grep -v tmpfs | grep -v udev

# Docker-specific disk usage
docker system df 2>/dev/null

# Largest directories in Itential volume mounts
du -sh /var/log/itential /opt/itential /var/lib/docker/volumes 2>/dev/null | sort -rh | head -20

# IAP platform container log sizes
docker exec platform du -sh /var/log/itential/*.log 2>/dev/null | sort -rh | head -20

# MongoDB data directory
docker exec mongodb du -sh /data/db 2>/dev/null
```

**SSH-based (role `iap` / `mongodb` targets from `.env`):**
```bash
ssh -i {SSH_KEY_PATH} {SSH_USER}@{SSH_HOST} \
  "df -h && du -sh /var/log/itential /var/lib/mongo /data/db 2>/dev/null | sort -rh | head -20"
```

**Disk thresholds:**
| Path | Concern | Action |
|------|---------|--------|
| Root filesystem > 85% | Critical | Find and remove large files |
| `/var/log/itential` > 1 GB | Log accumulation | Check log rotation; WFE `spam` level common cause |
| `/data/db` growing | MongoDB data | Check job/session collection sizes |
| Docker overlay > 80% disk | Image/layer bloat | `docker system prune` (with user consent) |

---

## Phase 3: CPU and Load Average

```bash
# Host CPU and load
uptime 2>/dev/null
cat /proc/loadavg 2>/dev/null   # 1min 5min 15min

# CPU count (normalize load against this)
nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null

# Top processes by CPU
ps aux --sort=-%cpu 2>/dev/null | head -20

# Container CPU (Docker)
docker stats --no-stream --format "{{.Name}}\t{{.CPUPerc}}" 2>/dev/null \
  | sort -t$'\t' -k2 -rn | head -10

# IAP platform Node.js processes
docker exec platform ps aux 2>/dev/null \
  | awk 'NR==1 || /node/' | sort -k3 -rn | head -10
```

**Threshold:** load > number of CPU cores = saturated; load > 2× cores = severely overloaded.

---

## Phase 3x: Prometheus Metrics (when `PROMETHEUS_URL` is available)

**When to invoke** — any one of these conditions is true:
- Phase 3 (CPU) shows sustained load > 2× CPU cores on any IAP node
- Phase 4 (Memory) will show or already shows a node approaching OOM
- Phase 1c (Crash Loop Detection) shows container restarts correlating with resource pressure
- Engineer wants time-series analysis of an incident window rather than a current-state snapshot
- `PROMETHEUS_URL` is set in `.env` and the engineer requests metrics-based analysis

**Prerequisites:** `PROMETHEUS_URL` in `.env`. For authenticated Prometheus:
- Basic auth → add `PROMETHEUS_BASIC_AUTH_USER` and `PROMETHEUS_BASIC_AUTH_PASS` to `.env`
- Bearer token → add `PROMETHEUS_BEARER_TOKEN` to `.env`
- Vault-managed credentials → source `vault-env.sh` first, then the skill picks up the env vars

**Invoke — and pass the incident time window:**
```
/prometheus
```

Tell the skill the incident start and end time from `ticket_context.md` (e.g.,
`"2026-09-10 14:30 UTC to 15:15 UTC"`) so it scopes PromQL range queries to the
relevant period instead of defaulting to the last hour.

The `/prometheus` skill covers:
- Health and readiness check
- Scrape target status (which targets are up/down)
- Firing alerts (current and resolved within window)
- Alerting and recording rule inventory
- Instant and range PromQL queries (pass raw PromQL for ad-hoc analysis)
- CPU utilization stat table per process group (using `quantile_over_time`)
- Resident RSS memory stat table
- IAP task completion rate (jobs/sec, estimated totals)
- TSDB head block cardinality and WAL size

**After Prometheus analysis:** cross-reference metric timestamps against log timestamps
from `/troubleshoot-logs` to confirm causality. Pass the PromQL findings to
`diagnostic_report.md` as evidence alongside the raw log excerpts.

---

## Phase 4: Memory (RAM)

```bash
# Host memory
free -h 2>/dev/null
# macOS:
vm_stat 2>/dev/null | head -10

# Top processes by memory
ps aux --sort=-%mem 2>/dev/null | head -20

# Container memory limits and usage
docker inspect platform mongodb redis apache-kafka 2>/dev/null \
  | python3 -c "
import sys, json
for c in json.load(sys.stdin):
    name      = c['Name'].lstrip('/')
    mem_limit = c.get('HostConfig',{}).get('Memory',0)
    limit_mb  = round(mem_limit/1024/1024) if mem_limit else 'unlimited'
    oom       = c['State'].get('OOMKilled', False)
    flag = '🔴 OOMKilled' if oom else ''
    print(f'{name}: mem_limit={limit_mb}MB  {flag}')
"
```

---

## Phase 5: Open File Descriptors

IAP and MongoDB exhaust FD limits under heavy load, causing connection failures (`EMFILE`).

```bash
# IAP Node.js process FDs
docker exec platform sh -c "
  pid=\$(pgrep -f 'node.*server' | head -1)
  if [ -n \"\$pid\" ]; then
    echo \"Node.js PID: \$pid\"
    cat /proc/\$pid/limits 2>/dev/null | grep -i 'open files'
    echo 'Current open FDs:' \$(ls /proc/\$pid/fd 2>/dev/null | wc -l)
  fi
" 2>/dev/null

# MongoDB process FDs
docker exec mongodb sh -c "
  pid=\$(pgrep mongod | head -1)
  if [ -n \"\$pid\" ]; then
    echo \"mongod PID: \$pid\"
    cat /proc/\$pid/limits 2>/dev/null | grep -i 'open files'
    echo 'Current open FDs:' \$(ls /proc/\$pid/fd 2>/dev/null | wc -l)
  fi
" 2>/dev/null

# System-wide FD usage
cat /proc/sys/fs/file-nr 2>/dev/null   # used / free / max
```

**Threshold:** current FDs > 80% of `Max open files` → process will start failing new connections.

---

## Phase 6: Network Connectivity Between Components

```bash
# Verify IAP can reach all its dependencies
docker exec platform sh -c "
  echo '=== IAP → MongoDB ==='; nc -zv mongodb 27017 2>&1 | head -2
  echo '=== IAP → Redis ===';   nc -zv redis 6379 2>&1 | head -2
  echo '=== IAP → Kafka ===';   nc -zv apache-kafka 9092 2>&1 | head -2
" 2>/dev/null

# Container network membership (all containers and their IPs)
docker inspect platform mongodb redis apache-kafka 2>/dev/null \
  | python3 -c "
import sys, json
for c in json.load(sys.stdin):
    name = c['Name'].lstrip('/')
    nets = c.get('NetworkSettings',{}).get('Networks',{})
    for net, cfg in nets.items():
        print(f'{name}: network={net}  ip={cfg.get(\"IPAddress\",\"?\")}')
"

# Who is on the itential-network?
docker network inspect itential-network 2>/dev/null \
  | python3 -c "
import sys, json
for n in json.load(sys.stdin):
    print('Network:', n['Name'])
    for cid, c in n.get('Containers',{}).items():
        print(f\"  {c['Name']}: {c['IPv4Address']}\")
" 2>/dev/null

# TLS cert validity on IAP endpoint
echo | openssl s_client -connect {HOST}:{PORT} 2>/dev/null \
  | openssl x509 -noout -dates -subject 2>/dev/null
```

---

## Phase 7: OS Diagnostics — Multi-Host SSH

Parse all SSH targets from `.env` and run diagnostics in parallel. Uses `SSH_HOST_N` blocks; falls back to legacy `SSH_HOST`.

```python
# Resolve SSH targets from .env
import os

def get_ssh_targets(env_path):
    env = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k,_,v = line.partition('=')
                env[k.strip()] = v.strip().strip('"').strip("'")
    targets = []
    i = 1
    while env.get(f'SSH_HOST_{i}'):
        targets.append({
            'host':  env[f'SSH_HOST_{i}'],
            'user':  env.get(f'SSH_USER_{i}', 'ec2-user'),
            'key':   env.get(f'SSH_KEY_PATH_{i}', '~/.ssh/id_rsa'),
            'port':  env.get(f'SSH_PORT_{i}', '22'),
            'role':  env.get(f'SSH_ROLE_{i}', 'generic'),
            'label': env.get(f'SSH_LABEL_{i}', f'host-{i}'),
        })
        i += 1
    if not targets and env.get('SSH_HOST'):
        targets.append({'host': env['SSH_HOST'], 'user': env.get('SSH_USER','ec2-user'),
                        'key': env.get('SSH_KEY_PATH','~/.ssh/id_rsa'), 'port': '22',
                        'role': 'generic', 'label': env['SSH_HOST']})
    return targets

targets = get_ssh_targets('{project_path}/.env')
for t in targets:
    print(f"[{t['role']}] {t['label']} → {t['user']}@{t['host']}:{t['port']}")
```

```bash
# OS diagnostics command for each SSH host
OS_CMD='
  echo "=== Uptime / Load ===" && uptime
  echo "=== CPU Count ===" && nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo
  echo "=== Memory ===" && free -h 2>/dev/null
  echo "=== Disk ===" && df -h | grep -v tmpfs
  echo "=== Top Processes (CPU) ===" && ps aux --sort=-%cpu 2>/dev/null | head -15
  echo "=== Open FDs (top pids) ===" && find /proc -maxdepth 3 -name fd 2>/dev/null | while read p; do echo "$(ls $p 2>/dev/null | wc -l) $p"; done | sort -rn | head -10
  echo "=== Listening Ports ===" && ss -tlnp 2>/dev/null | head -25
  echo "=== OOM events ===" && dmesg 2>/dev/null | grep -i "oom\|killed process" | tail -20
  echo "=== Systemd failed units ===" && systemctl --failed 2>/dev/null | head -20
'

# Run all hosts in parallel
declare -A PIDS
for i in $(seq 1 20); do
  HOST=$(grep "^SSH_HOST_${i}=" {project_path}/.env 2>/dev/null | cut -d= -f2-)
  [ -z "$HOST" ] && break
  USER=$(grep "^SSH_USER_${i}=" {project_path}/.env | cut -d= -f2- || echo ec2-user)
  KEY=$(grep "^SSH_KEY_PATH_${i}=" {project_path}/.env | cut -d= -f2- || echo ~/.ssh/id_rsa)
  PORT=$(grep "^SSH_PORT_${i}=" {project_path}/.env | cut -d= -f2- || echo 22)
  LABEL=$(grep "^SSH_LABEL_${i}=" {project_path}/.env | cut -d= -f2- || echo "host-${i}")
  ROLE=$(grep "^SSH_ROLE_${i}=" {project_path}/.env | cut -d= -f2- || echo generic)
  OUTFILE="{project_path}/data/{TIMESTAMP}/os_${ROLE}_${LABEL}.txt"
  echo "[${ROLE}] ${LABEL} — collecting..."
  ssh -i "$KEY" -p "$PORT" -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
    "${USER}@${HOST}" "$OS_CMD" > "$OUTFILE" 2>&1 &
  PIDS[$i]=$!
done
for pid in "${PIDS[@]}"; do wait "$pid"; done
echo "OS diagnostics complete."
```

**Role-specific checks after the general sweep:**
```bash
# IAP nodes — Node.js process, log dir size, service status
# MongoDB nodes — mongod process, data dir size, replica status
# Redis nodes — redis-server process, memory
# (Run SSH commands filtered by SSH_ROLE_N = iap|mongodb|redis|iag|kafka)
```

---

## Phase 8: EKS / Kubernetes

Run if `KUBE_NAMESPACE` is in `.env`:

```bash
# Verify kubectl context
kubectl cluster-info 2>&1 | head -3

# Pod status in Itential namespace
kubectl get pods -n {KUBE_NAMESPACE} -o wide 2>/dev/null

# Pods in non-Running state
kubectl get pods -n {KUBE_NAMESPACE} 2>/dev/null \
  | grep -v "Running\|Completed" | head -20

# Node resource usage
kubectl top nodes 2>/dev/null

# Pod resource usage
kubectl top pods -n {KUBE_NAMESPACE} 2>/dev/null

# OOMKilled / CrashLoopBackOff details
kubectl describe pods -n {KUBE_NAMESPACE} 2>/dev/null \
  | grep -A5 "OOMKilled\|CrashLoopBackOff\|Error\|Reason" | head -100

# Recent events in namespace
kubectl get events -n {KUBE_NAMESPACE} --sort-by='.lastTimestamp' 2>/dev/null | tail -30

# EKS node conditions
kubectl describe nodes 2>/dev/null \
  | grep -A5 "Conditions:\|pressure\|DiskPressure\|MemoryPressure\|PIDPressure" | head -50
```

**EKS-specific thresholds:**
| Condition | Meaning |
|-----------|---------|
| `MemoryPressure=True` on node | Node evicting pods due to memory |
| `DiskPressure=True` on node | Node near disk limit; pod eviction imminent |
| `CrashLoopBackOff` | Pod exiting repeatedly — check logs |
| `OOMKilled` in pod describe | Container exceeded memory limit |

---

## Phase 9: AWS ElastiCache (Redis)

Run if `REDIS_HOST` points to an ElastiCache endpoint and `AWS_ACCESS_KEY_ID` is in `.env`:

```bash
# Check ElastiCache cluster status via AWS CLI
aws elasticache describe-cache-clusters \
  --region {AWS_REGION} \
  --output json 2>/dev/null \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for c in d.get('CacheClusters', []):
    cid     = c.get('CacheClusterId')
    status  = c.get('CacheClusterStatus')
    engine  = c.get('Engine')
    version = c.get('EngineVersion')
    flag = '✅' if status == 'available' else '🔴'
    print(f'{flag} {cid}: {status}  {engine} {version}')
"

# CloudWatch metrics for ElastiCache (memory, connections, evictions)
aws cloudwatch get-metric-statistics \
  --namespace AWS/ElastiCache \
  --metric-name FreeableMemory \
  --dimensions Name=CacheClusterId,Value={CLUSTER_ID} \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v -1H +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 --statistics Average \
  --region {AWS_REGION} \
  --output json 2>/dev/null \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
points = sorted(d.get('Datapoints',[]), key=lambda x: x['Timestamp'])
for p in points[-5:]:
    mb = round(p['Average']/1024/1024, 1)
    print(f\"{p['Timestamp'][:19]}: FreeableMemory={mb}MB\")
"

# Evictions from ElastiCache
aws cloudwatch get-metric-statistics \
  --namespace AWS/ElastiCache \
  --metric-name Evictions \
  --dimensions Name=CacheClusterId,Value={CLUSTER_ID} \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v -1H +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 --statistics Sum \
  --region {AWS_REGION} --output json 2>/dev/null \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
total = sum(p.get('Sum',0) for p in d.get('Datapoints',[]))
flag = '⚠️' if total > 0 else '✅'
print(f'Total evictions in last hour: {total}  {flag}')
"
```

---

## Phase 10: UI & API Performance Baseline

```bash
# Time key IAP API endpoints (3 samples to detect variance)
time_request() {
  local url="$1" label="$2"
  result=$(curl -sk -o /dev/null -w "%{http_code} %{time_total}s" "${url}" 2>/dev/null)
  echo "${label}: ${result}"
}

BASE="{PLATFORM_URL}"
TOKEN="{TOKEN}"

for run in 1 2 3; do
  echo "=== Run ${run} ==="
  time_request "${BASE}/health?token=${TOKEN}" "health"
  time_request "${BASE}/health/applications?token=${TOKEN}" "health/applications"
  time_request "${BASE}/health/adapters?token=${TOKEN}" "health/adapters"
  time_request "${BASE}/operations-manager/jobs?status=running&limit=10&token=${TOKEN}" "jobs/running"
  time_request "${BASE}/automation-studio/workflows?limit=10&token=${TOKEN}" "workflows"
done

# Detailed timing breakdown for any endpoint > 2s
curl -sk -o /dev/null \
  -w "dns:%{time_namelookup}s connect:%{time_connect}s ssl:%{time_appconnect}s ttfb:%{time_starttransfer}s total:%{time_total}s http:%{http_code}\n" \
  "{SLOW_ENDPOINT}?token={TOKEN}" 2>/dev/null
```

**API latency thresholds:** < 500ms ✅ | 500ms–2s acceptable | > 2s slow ⚠️ | > 5s critical 🔴

---

## Common Infra Failure Patterns — Quick Reference

| Symptom | Likely Cause | Steps |
|---------|-------------|-------|
| Container restarting repeatedly | OOMKilled or startup error | Phase 1c, Phase 4 |
| `ENOSPC` / write failures | Disk full | Phase 2 |
| Platform app timeout on startup | MongoDB/Redis not reachable | Phase 6 |
| IAP slow / jobs stuck | CPU saturated on host | Phase 3 |
| `EMFILE` / too many open files | FD limit exhausted | Phase 5 |
| Random connection failures | Network partition or wrong network | Phase 6 |
| EKS pod evicted | Node memory or disk pressure | Phase 8 |
| ElastiCache evictions | Redis at memory limit | Phase 9 |
| API latency spikes | Server slow processing or large response | Phase 10 |
| Zombie processes accumulating | Parent not reaping children | Phase 1 (ps defunct) |
