---
name: perf-test-analysis
description: Analyze a completed Platform Server Spec Validation performance test run (loadgen JSON, statscollector CSV, Prometheus infra metrics) and produce a Markdown report with pass/fail evaluation and comparison to the previous run of the same Test ID.
---

# Perf Test Analysis

## Purpose

After a Platform Server Spec Validation test run finishes on the perf lab, analyze the collected
results and produce a single Markdown report: pass/fail against the test plan's criteria, the
measured numbers, and a comparison against the previous run of the same Test ID so trends over
time are visible. This is a one-time test campaign, not a recurring regression suite — capture
data thoroughly since these runs will not be repeated.

## Invocation

Two ways this skill gets invoked:

1. **Automated** — a bash script that runs loadgen and statscollector invokes this skill
   immediately after a test finishes, passing three explicit arguments: `<TestID> <Environment>
   <Date>`. `Date` is the date string (`YYYY-MM-DD`) the script captured before it started the
   run, matching the filename date suffix loadgen/statscollector use.
   Example: `PLAT-RAM-01 vm 2026-08-24`
[`run-perf-analysis.sh`](run-perf-analysis.sh) in this skill directory implements the automated
path: it fetches `ANTHROPIC_API_KEY`/`ANTHROPIC_WORKSPACE_ID` from Vault via a dedicated AppRole
(role_id/secret_id read from `/home/pet-user/.vault-approle/` on pe-ansible — no `vault` CLI is
installed there, so it speaks Vault's HTTP API directly with `curl`+`jq`), then runs
`claude -p "/perf-test-analysis <TestID> <Environment> <Date>" --allowedTools "Read,Glob,Bash,Write"`
from the reports directory. Deliberately does not use `--dangerously-skip-permissions` — the
pre-approved tool list is exactly what the skill needs, without blanket-bypassing everything else
on a shared lab host. Run it as `pet-user` on pe-ansible: `~/scripts/run-perf-analysis.sh
PLAT-CPU-01 vm 2026-08-18`.

2. **Manual** — invoked directly with either the same explicit `<TestID> <Environment> <Date>`
   form, or a plain-language description of which run to analyze (e.g. "analyze the PLAT-RAM-01
   VM baseline run from last week"). When given plain language instead of an explicit date,
   resolve it to a specific date first — use `ls -lt` / directory listings under
   `loadgen/perf-minimal/` (or whichever subtest is easiest to check) to figure out which dated
   file the description refers to, and state the resolved date in the report so it's clear which
   run was actually analyzed. If the description is ambiguous (e.g. more than one plausible
   match), ask for clarification rather than guessing.

- `TestID` — e.g. `PLAT-RAM-01`, `MONGO-CPU-01`, `GW-COMBO-01`. Identifies which component and
  variable this run tested, and which published spec (the ceiling) applies.
- `Environment` — `vm` or `k8s`. The two passes are never compared to each other automatically;
  each gets its own report. Only compare VM-to-K8s numbers if explicitly asked to do so afterward.
- `Date` — the filename date suffix shared by this run's files, e.g. `2026-08-24`.

## Data sources

All paths are relative to `/home/pet-user/Performance-Tests/loadgen/reports/` on pe-ansible.
This structure is expected to change in a future update to this skill — do not generalize beyond
what is listed here.

### 1. Loadgen JSON (one file per subtest, required)

A single Test ID run consists of these 12 subtests, each with its own directory:

```
loadgen/perf-minimal/perf-minimal-<date>.json
loadgen/perf-cpu/perf-cpu-<date>.json
loadgen/perf-mem/perf-mem-<date>.json
loadgen/perf-sequence/perf-sequence-<date>.json
loadgen/perf-sequence-large-steady/perf-sequence-large-steady-<date>.json
loadgen/perf-parallel-loop/perf-parallel-loop-<date>.json
loadgen/perf-sequential-loop/perf-sequential-loop-<date>.json
loadgen/perf-hybrid-steady/perf-hybrid-steady-<date>.json
loadgen/perf-jst-steady/perf-jst-steady-<date>.json
loadgen/perf-advanced-mem-compact-steady/perf-advanced-mem-compact-steady-<date>.json
loadgen/perf-advanced-mem-compact-gridfsmax-10m/perf-advanced-mem-compact-gridfsmax-10m-<date>.json
loadgen/perf-complicated-steady/perf-complicated-steady-<date>.json
```

For each of the 12 directories: the file whose filename date suffix matches the `Date` argument
is this run's result — i.e. `loadgen/perf-minimal/perf-minimal-<Date>.json`. Any other dated
files in the same directory are history from previous runs of that subtest, used for the
time-comparison section of the report. If a subtest's file for `<Date>` doesn't exist, note it
in the report (that subtest may not have run, or ran on a different date than expected) rather
than silently skipping it or substituting a different date.

JSON fields: `shape`/`automation` (subtest name), `start_time`/`end_time` (ISO timestamps —
needed to scope the Prometheus queries below), `sent`/`failed` (totals), and `phases[]`
(`ramp-up`/`steady-state`/`spike`/`recovery`), each with `sent`/`failed`/`duration` and
`response_data` (`p95`/`p90`/`median`/`min`/`max`/`mean` response time in ms). `median` is this
run's p50.

### 2. Statscollector CSV (one file per subtest, optional — may not exist yet)

```
statscollector/<subtest>/<subtest>-<date>.csv
```

Same `<Date>`-matching rule as the loadgen JSON. If missing for a given subtest, note it in the
report under Known Limitations rather than failing — as of 2026-08-24 this is only populated for
trial runs, not yet for real numbered Test ID runs.

Columns of interest: `iap_score`, `job_total`/`job_stuck`/`job_succeeded_during`/`job_errored_during`/
`job_succeeded_full`/`job_errored_full`, `job_during_p50_ms`/`job_during_p99_ms`/`job_full_p50_ms`/
`job_full_p99_ms` (and the `task_*` equivalents). This is the source for the test plan's
"Job/task latency (p50/p99)" metric.

### 3. Prometheus infra metrics (query directly, always capture the full set)

Prometheus API: `http://172.85.0.29:9090/api/v1/query_range` (host: pe-mon01). Every test run
exercises all four components (Platform, Mongo, Redis, Gateway) regardless of which one is under
test, so always query all of the below, scoped to `start`/`end` = each subtest's `start_time`/
`end_time` from its loadgen JSON, with `step=15s`.

```promql
# CPU busy % (per node)
100 - (avg by (instance)(rate(node_cpu_seconds_total{job="node_exporter",mode="idle"}[5m]))*100)

# RAM used % (per node)
100 * (1 - node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes)

# Swap in use, bytes (per node) — any value > 0 sustained is an automatic fail
node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes

# Mongo ops/sec by type
sum by (legacy_op_type)(rate(mongodb_ss_opcounters[5m]))

# Mongo connections (current vs available)
mongodb_ss_connections{conn_type="current"}
mongodb_ss_connections{conn_type="available"}

# Mongo query/op latency, avg usec per op, by op_type (reads|writes|commands|transactions)
rate(mongodb_ss_opLatencies_latency{op_type="reads"}[5m]) / clamp_min(rate(mongodb_ss_opLatencies_ops{op_type="reads"}[5m]), 1)

# Mongo cache hit ratio (derived)
1 - (rate(mongodb_ss_wt_cache_pages_read_into_cache[5m]) / clamp_min(rate(mongodb_ss_wt_cache_pages_requested_from_the_cache[5m]), 1))

# Redis ops/sec
rate(redis_commands_processed_total[5m])

# Redis evictions/sec
rate(redis_evicted_keys_total[5m])

# Redis clients
redis_connected_clients
redis_blocked_clients

# Platform job/task throughput + errors
rate(itential_job_start[5m])
rate(itential_job_complete[5m])
rate(itential_job_error[5m])
rate(itential_task_start[5m])
rate(itential_task_complete[5m])
rate(itential_task_error[5m])
# workflow engine queue depth proxy: gap between itential_task_start and itential_task_complete rates

# Gateway execution latency (p95)
histogram_quantile(0.95, rate(service_completion_time_seconds_bucket[5m]))

# Gateway concurrent execution count
service_run_count
```

**Not available — do not attempt to query, report as Known Limitations instead:** Platform
Node.js event loop lag, Platform GC pause time, Gateway queue depth. These are real
instrumentation gaps (confirmed 2026-08-24 by listing every metric name on the relevant
exporters), not missing queries.

### 4. Previous run comparison

For the time-comparison section, prefer reading **previous analysis reports** (see Output below)
over re-querying Prometheus for old time windows — Prometheus retention is 30 days and a
multi-week test campaign may outlive that for early runs. The report itself is the durable record.
If no previous report exists yet for this Test ID + Environment, note that this is the first run
and skip the comparison section.

## Pass/Fail criteria (defaults — override per test if the test plan specifies otherwise)

- No sustained CPU > 90% for more than 5 minutes under the reference workload
- No swap usage at all
- p99 job latency does not exceed 1.5x the baseline-spec p95 latency
- Error rate stays at 0% (or matches baseline error rate if baseline is non-zero)

## Report structure

Write one Markdown file per invocation. For each of the 12 subtests, report: value(s) tested (CPU
cores / RAM GB, from the Test ID's own record — confirm with the user if not already known),
CPU util (avg/peak), RAM util (avg/peak), swap (pass/fail), throughput, job/task latency
(p50/p99), API latency (p50/p90/p95), error rate, and the component-specific metrics above.
Follow this structure:

```markdown
# <TestID> — <Environment> — <run date>

## Summary
<Pass/fail at a glance, one paragraph>

## Results by Subtest
<One table per subtest or one combined table — CPU/RAM/swap/throughput/latency/error rate/pass-fail/notes>

## Component-Specific Metrics
<Mongo ops/latency/cache hit ratio, Redis ops/evictions/clients, Gateway concurrency/latency, Platform job/task rates>

## Comparison to Previous Run
<Only if a prior report exists for this Test ID + Environment — call out deltas, not just raw numbers>

## Known Limitations
- Platform Node.js event loop lag: not measured (no Prometheus instrumentation available)
- Platform GC pause time: not measured (no Prometheus instrumentation available)
- Gateway queue depth: not measured (no Prometheus instrumentation available)
- <add statscollector-missing note here if applicable to this run>

## Log Analysis
_Not yet available — log collection is planned as a future addition to the data gathered for
these tests. This section will be completed once log data is included._
```

## Output

Save the report to:
```
/home/pet-user/Performance-Tests/loadgen/reports/analysis/<TestID>-<Environment>-<date>.md
```
