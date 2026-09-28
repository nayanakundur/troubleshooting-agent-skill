---
name: prometheus
description: Query and analyze Prometheus metrics — health checks, target status, PromQL instant and range queries, alert inspection, TSDB diagnostics, CPU/memory/task-rate summaries with full statistical profiles
allowed-tools: Bash, Read
---

You are the Prometheus admin skill. Follow these steps every time you are invoked.

## Step 1: Identify Target Environment

If the user specified an environment in their request (e.g. `/prometheus production`),
use `$ARGUMENTS` as the environment name. Otherwise ask: "Which environment? (production /
development / test)"

## Step 2: Load Environment Context

Read the environment skill file:

```
environments/<env-name>/CLAUDE.md
```

From it, extract and hold in memory:

- `PROMETHEUS_HOST` — Prometheus hostname
- `PROMETHEUS_PORT` — Prometheus port (default `9090`)
- `PROMETHEUS_AUTH` — auth method: `none`, `basic`, or `bearer`
- `VAULT_ADDR` — HashiCorp Vault address (only needed if auth is not `none`)
- `PLATFORM_VAULT_PATH` — Vault path for credentials (only needed if auth is not `none`)

Assemble the base URL and auth flags:

```bash
PROMETHEUS_BASE_URL="http://<host>:<port>"
PROM_AUTH=""   # empty string for auth=none
```

If `PROMETHEUS_AUTH` is `basic`:

```bash
source vault-env.sh

PROM_CREDS=$(curl -s -H "X-Vault-Token: $VAULT_TOKEN" \
  $VAULT_ADDR/<prometheus-vault-path> | jq -r '.data.data')

PROM_USER=$(echo "$PROM_CREDS" | jq -r '."<user-key>"')
PROM_PASS=$(echo "$PROM_CREDS" | jq -r '."<pass-key>"')
PROM_AUTH="-u $PROM_USER:$PROM_PASS"
```

If `PROMETHEUS_AUTH` is `bearer`:

```bash
source vault-env.sh

PROM_TOKEN=$(curl -s -H "X-Vault-Token: $VAULT_TOKEN" \
  $VAULT_ADDR/<prometheus-vault-path> | jq -r '.data.data."<token-key>"')
PROM_AUTH="-H \"Authorization: Bearer $PROM_TOKEN\""
```

Confirm Prometheus is reachable before proceeding:

```bash
curl -sf $PROM_AUTH "$PROMETHEUS_BASE_URL/-/healthy" && echo "healthy" || echo "unreachable"
```

If unreachable, remind the user that VPN must be active.

## Step 3: Execute Requested Task

If the user has not stated a task, present this menu and ask what they would like to do:

```
Health & Targets
  1.  Health check
  2.  List scrape targets
  3.  Target health (highlight down targets)
  4.  Scrape pool status (last scrape time, duration, errors)

Querying
  5.  Instant PromQL query
  6.  Range query (trend over a time window)
  7.  List all metric names
  8.  Search metrics by label matcher

Alerting
  9.  View firing alerts
  10. List alerting rules
  11. List recording rules

Diagnostics
  12. TSDB stats (series count, top metrics by cardinality)
  13. Runtime info
  14. CPU summary (min/max/mean/p50/p90/p95/p99 per process group)
  15. Memory summary (resident RSS min/max/mean/p50/p90/p95/p99 per process group)
  16. Task completion rate summary (min/max/mean/p50/p90/p95/p99 + estimated total)
```

---

### Task: Health Check

```bash
echo "=== Healthy ==="
curl -sf $PROM_AUTH "$PROMETHEUS_BASE_URL/-/healthy" && echo "OK" || echo "FAILED"

echo "=== Ready ==="
curl -sf $PROM_AUTH "$PROMETHEUS_BASE_URL/-/ready" && echo "OK" || echo "FAILED"
```

Report whether Prometheus is healthy and ready to serve traffic.

---

### Task: List Scrape Targets

```bash
curl -s $PROM_AUTH "$PROMETHEUS_BASE_URL/api/v1/targets" \
  | jq '[.data.activeTargets[] | {job: .labels.job, instance: .labels.instance, state: .health, lastError: .lastError}]'
```

Present results as a table. Highlight any target where `state` is not `up`.

---

### Task: Target Health

```bash
curl -s $PROM_AUTH "$PROMETHEUS_BASE_URL/api/v1/targets" \
  | jq '.data.activeTargets | group_by(.health) | map({state: .[0].health, count: length, targets: map(.labels.instance)})'
```

Summarize `up` vs `down` counts. List all `down` targets with their `lastError`.

---

### Task: Scrape Pool Status

```bash
curl -s $PROM_AUTH "$PROMETHEUS_BASE_URL/api/v1/targets" \
  | jq '[.data.activeTargets[] | {job: .labels.job, instance: .labels.instance, lastScrape: .lastScrape, lastScrapeDuration: .lastScrapeDuration, lastError: .lastError}]'
```

Report last scrape time and duration per target. Flag any target with a non-empty `lastError`.

---

### Task: Instant PromQL Query

Ask the user for the PromQL expression if not provided. Optionally ask for a timestamp
(default: now).

```bash
curl -s $PROM_AUTH \
  --data-urlencode "query=<expr>" \
  "$PROMETHEUS_BASE_URL/api/v1/query" \
  | jq '.data.result'
```

Format results clearly. For vector results, show metric labels and value. For scalar
results, show the value directly.

---

### Task: Range Query (Trend Over Time)

Ask the user for:

- PromQL expression
- Start time (e.g., `1h ago`, RFC3339 timestamp, or Unix timestamp)
- End time (default: now)
- Step (e.g., `60s`, `5m`, `1h`)

Convert relative times to Unix timestamps before calling the API:

```bash
END=$(date +%s)
# macOS/Linux compatible relative time:
START=$(date -d "1 hour ago" +%s 2>/dev/null || date -v -1H +%s)
```

```bash
curl -s $PROM_AUTH \
  --data-urlencode "query=<expr>" \
  --data-urlencode "start=$START" \
  --data-urlencode "end=$END" \
  --data-urlencode "step=<step>" \
  "$PROMETHEUS_BASE_URL/api/v1/query_range" \
  | jq '.data.result'
```

Summarize the trend: for each series report the min, max, and latest value. Describe
whether values are increasing, decreasing, or stable over the requested window.

---

### Task: List All Metric Names

Ask if the user wants to filter by prefix or substring (optional).

```bash
curl -s $PROM_AUTH \
  "$PROMETHEUS_BASE_URL/api/v1/label/__name__/values" \
  | jq '.data | sort[]'
```

If a filter was provided, append `| grep -i "<filter>"`. Report the total count.

---

### Task: Search Metrics by Label Matcher

Ask the user for a label matcher (e.g., `job="iap"`, `instance=~".*mongo.*"`).

```bash
curl -s $PROM_AUTH \
  --data-urlencode 'match[]={<label-matcher>}' \
  "$PROMETHEUS_BASE_URL/api/v1/series" \
  | jq '[.data[] | .__name__] | unique | sort[]'
```

Report all metric names that match. Include the total count.

---

### Task: View Firing Alerts

```bash
curl -s $PROM_AUTH "$PROMETHEUS_BASE_URL/api/v1/alerts" \
  | jq '[.data.alerts[] | select(.state == "firing") | {alertname: .labels.alertname, severity: .labels.severity, instance: .labels.instance, summary: .annotations.summary, activeAt: .activeAt}]'
```

If no alerts are firing, report that explicitly. For each firing alert, show name,
severity, affected instance, summary annotation, and how long it has been active.

---

### Task: List Alerting Rules

```bash
curl -s $PROM_AUTH "$PROMETHEUS_BASE_URL/api/v1/rules" \
  | jq '[.data.groups[].rules[] | select(.type == "alerting") | {name: .name, state: .state, health: .health, lastEvaluation: .lastEvaluation}]'
```

Highlight any rule where `state` is `firing` or `health` is not `ok`.

---

### Task: List Recording Rules

```bash
curl -s $PROM_AUTH "$PROMETHEUS_BASE_URL/api/v1/rules" \
  | jq '[.data.groups[].rules[] | select(.type == "recording") | {name: .name, health: .health, lastEvaluation: .lastEvaluation}]'
```

---

### Task: TSDB Stats

```bash
curl -s $PROM_AUTH "$PROMETHEUS_BASE_URL/api/v1/status/tsdb" \
  | jq '{
      numSeries: .data.numSeries,
      numLabelPairs: .data.numLabelPairs,
      topMetricsBySeriesCount: .data.seriesCountByMetricName[:10],
      topLabelsByValueCount: .data.labelValueCountByLabelName[:10]
    }'
```

Report total series count, top 10 metrics by series count, and top 10 labels by value
count. Flag any single metric contributing more than 5% of total series as a cardinality
concern.

---

### Task: CPU Summary

Ask the user for:

- PromQL metric name (default: `namedprocess_namegroup_cpu_seconds_total`)
- Instance label matcher (e.g., `bt-iap01:9256|bt-iap02:9256`)
- Time window (default: `24h`)
- Rate interval (default: `5m`)

Compute min, max, mean, median (p50), p90, p95, and p99 of the per-second CPU rate for
each `(instance, groupname)` pair. Aggregate `user` and `system` modes by summing before
computing statistics.

```bash
python3 - <<'PYEOF'
import subprocess, json

PROM = "<prometheus-base-url>"
METRIC = '<metric>{instance=~"<instance-matcher>"}'
WINDOW = "<time-window>"   # e.g. 24h
RATE   = "<rate-interval>" # e.g. 5m
BASE   = f"sum by (instance, groupname) (rate({METRIC}[{RATE}]))[{WINDOW}:{RATE}]"

stats = [
    ("min",  f"min_over_time({BASE})"),
    ("max",  f"max_over_time({BASE})"),
    ("mean", f"avg_over_time({BASE})"),
    ("p50",  f"quantile_over_time(0.5,  {BASE})"),
    ("p90",  f"quantile_over_time(0.9,  {BASE})"),
    ("p95",  f"quantile_over_time(0.95, {BASE})"),
    ("p99",  f"quantile_over_time(0.99, {BASE})"),
]

data = {}
for stat_name, query in stats:
    r = subprocess.run(
        ["curl", "-s", "--data-urlencode", f"query={query}",
         f"{PROM}/api/v1/query"],
        capture_output=True, text=True
    )
    for item in json.loads(r.stdout).get("data", {}).get("result", []):
        key = (item["metric"]["instance"], item["metric"]["groupname"])
        data.setdefault(key, {})[stat_name] = float(item["value"][1])

rows = sorted(data.items(), key=lambda x: x[1].get("mean", 0), reverse=True)
cols  = ["min", "max", "mean", "p50", "p90", "p95", "p99"]
hdr   = f"{'instance':<20} {'groupname':<30} " + " ".join(f"{c:>8}" for c in cols)
print(hdr)
print("-" * len(hdr))
for (instance, groupname), vals in rows:
    def fmt(v): return f"{v*1000:.3f}" if v is not None else "n/a"
    print(f"{instance:<20} {groupname:<30} " + " ".join(f"{fmt(vals.get(c)):>8}" for c in cols))

print()
print("Values are CPU rate in milliseconds/second (ms/s). Sorted by mean descending.")
PYEOF
```

After printing the table, summarize:

- Which instance is carrying active load (non-zero Pronghorn/itential processes) vs standby (all zeros).
- Any process where max >> p99, indicating rare but extreme CPU spikes.
- Any process where p50 ≈ p90 ≈ p95, indicating steady predictable consumption.

---

### Task: Runtime Info

```bash
curl -s $PROM_AUTH "$PROMETHEUS_BASE_URL/api/v1/status/runtimeinfo" | jq .
```

Report storage path, retention duration, and whether TSDB is operating normally.

---

### Task: Memory Summary

Ask the user for:

- Instance label matcher (e.g., `bt-iap01:9256|bt-iap02:9256`)
- Group name filter (e.g., `Pronghorn core|Pronghorn WorkF`) — optional, omit to include all
- Start time (RFC3339 UTC or Unix timestamp)
- End time (RFC3339 UTC, Unix timestamp, or duration offset from start e.g. `+6855s`)
- Step (default: `60s`)

`namedprocess_namegroup_memory_bytes` is a **gauge** — do not wrap in `rate()`. Filter
to `memtype="resident"` for RSS. Compute statistics from the raw range query values.

```bash
python3 - <<'PYEOF'
import subprocess, json
from datetime import datetime, timezone

PROM  = "<prometheus-base-url>"
START = <start-unix-timestamp>
END   = <end-unix-timestamp>
STEP  = "60s"

query = (
    'namedprocess_namegroup_memory_bytes{'
    '  instance=~"<instance-matcher>",'
    '  groupname=~"<groupname-matcher>",'   # omit this line if no group filter
    '  memtype="resident"'
    '}'
)

def percentile(sv, p):
    n = len(sv)
    if n == 0: return float("nan")
    idx = (p / 100.0) * (n - 1)
    lo  = int(idx); hi = min(lo + 1, n - 1)
    return sv[lo] + (idx - lo) * (sv[hi] - sv[lo])

r = subprocess.run(
    ["curl", "-s", "-G",
     "--data-urlencode", f"query={query}",
     "--data-urlencode", f"start={START}",
     "--data-urlencode", f"end={END}",
     "--data-urlencode", f"step={STEP}",
     f"{PROM}/api/v1/query_range"],
    capture_output=True, text=True
)
resp = json.loads(r.stdout)
if resp.get("status") != "success":
    print("ERROR:", resp); raise SystemExit(1)

data = {}
for item in resp["data"]["result"]:
    inst  = item["metric"].get("instance", "?")
    group = item["metric"].get("groupname", "?")
    vals  = [float(v[1]) for v in item["values"]
             if v[1] not in ("NaN", "+Inf", "-Inf")]
    data[(inst, group)] = vals

cols  = ["min", "max", "mean", "median", "p90", "p95", "p99"]
col_w = 12
hdr   = f"{'instance':<18}{'groupname':<22}" + "".join(f"{c:>{col_w}}" for c in cols) + f"  {'n':>5}"
print(hdr); print("-" * len(hdr))

node_totals = {}
for (inst, group), values in sorted(data.items()):
    sv    = sorted(values); n = len(sv)
    mean  = sum(sv) / n if n else 0
    node_totals[inst] = node_totals.get(inst, 0) + mean
    stats = {"min": sv[0], "max": sv[-1], "mean": mean,
             "median": percentile(sv, 50), "p90": percentile(sv, 90),
             "p95": percentile(sv, 95), "p99": percentile(sv, 99)} if n else {}
    def fmt(v): return f"{v/1_048_576:.1f}"
    row = f"{inst:<18}{group:<22}" + "".join(f"{fmt(stats.get(c,0)):>{col_w}}" for c in cols) + f"  {n:>5}"
    print(row)

print()
print(f"  Cluster mean total: {sum(node_totals.values())/1_048_576:.1f} MiB")
print(f"  Values in MiB (mebibytes). Step: {STEP}")
PYEOF
```

After printing the table, summarize:

- Which node holds more total resident memory, and by how much.
- Any process with a wide min→max spread, indicating a restart or heap growth during the window.
- Any process with a zero min (process was absent or restarted during the window).
- Steady processes where p50 ≈ p99 (bounded, predictable footprint).

---

### Task: Task Completion Rate Summary

Ask the user for:

- Job label matcher (e.g., `wfe-metrics-bt`)
- Start time (RFC3339 UTC or Unix timestamp)
- End time (RFC3339 UTC, Unix timestamp, or duration offset from start e.g. `+6765s`)
- Rate interval (default: `5m`)
- Step (default: `60s`)

`itential_task_complete` is a **counter** — wrap in `rate()`. The `sum()` aggregates
across all instances for a single cluster-wide series. Compute statistics from the range
query values and estimate total tasks completed as `mean_rate × window_seconds`.

Always use `-G` with curl for range queries to avoid empty responses.

```bash
python3 - <<'PYEOF'
import subprocess, json

PROM  = "<prometheus-base-url>"
START = <start-unix-timestamp>
END   = <end-unix-timestamp>
RATE  = "5m"
STEP  = "60s"

query = 'sum(rate(itential_task_complete{job=~"<job-matcher>"}[' + RATE + ']))'

def percentile(sv, p):
    n = len(sv)
    if n == 0: return float("nan")
    idx = (p / 100.0) * (n - 1)
    lo  = int(idx); hi = min(lo + 1, n - 1)
    return sv[lo] + (idx - lo) * (sv[hi] - sv[lo])

r = subprocess.run(
    ["curl", "-s", "-G",
     "--data-urlencode", f"query={query}",
     "--data-urlencode", f"start={START}",
     "--data-urlencode", f"end={END}",
     "--data-urlencode", f"step={STEP}",
     f"{PROM}/api/v1/query_range"],
    capture_output=True, text=True
)
raw = r.stdout.strip()
if not raw:
    print("Empty response — check VPN and metric name"); raise SystemExit(1)

resp = json.loads(raw)
if resp.get("status") != "success":
    print("ERROR:", resp); raise SystemExit(1)

results = resp["data"]["result"]
if not results:
    print("No series returned — verify job label matcher"); raise SystemExit(0)

values = [float(v[1]) for v in results[0]["values"]
          if v[1] not in ("NaN", "+Inf", "-Inf")]
sv     = sorted(values); n = len(sv)
mean   = sum(sv) / n
duration   = END - START
total_est  = mean * duration

stats = {"min": sv[0], "max": sv[-1], "mean": mean,
         "median": percentile(sv, 50), "p90": percentile(sv, 90),
         "p95": percentile(sv, 95), "p99": percentile(sv, 99)}

cols  = ["min", "max", "mean", "median", "p90", "p95", "p99"]
col_w = 12
hdr   = "".join(f"{c:>{col_w}}" for c in cols) + f"  {'n':>5}  {'est_total':>12}"
print(hdr); print("-" * len(hdr))
row   = "".join(f"{stats[c]:>{col_w}.6f}" for c in cols) + f"  {n:>5}  {total_est:>12,.1f}"
print(row)
print(f"\n  Values in tasks/second.")
print(f"  Estimated total tasks completed: {total_est:,.1f}  (mean rate × {duration}s window)")
print(f"  Step: {STEP}  |  Rate window: {RATE}")
PYEOF
```

After printing the table, summarize:

- The sustained throughput band (min → median) vs burst ceiling (p95/p99).
- Whether a low min relative to the median suggests a restart or stall mid-window.
- Estimated total tasks as a concrete workload volume figure.
- When comparing two windows: note rate delta, total delta, and whether the upper tail
  (p90–p99) converges even when means differ — this indicates the same peak capacity
  with different average utilization.
