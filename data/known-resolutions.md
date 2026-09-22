# Known Resolutions Library

This file captures confirmed resolution patterns from ISD ticket investigations. Each entry includes the symptom, root cause, resolution steps, workaround, detection hints, and verification. Updated as part of Phase 7 — Resolution Learning.

---

## Resolution Entries

---

### [ISD-9288] itenProngAppDown / itenProngAppCrash SNMP traps never sent

| Field | Value |
|-------|-------|
| **Ticket** | ISD-9288 |
| **ENG Bug** | ENG-24868 |
| **Component** | Platform core — `Shutdown.js` / SNMP trap subsystem |
| **Platform Version** | 6.4.0 (confirmed via tcpdump on PE Labs) |
| **Severity** | S3 — Lab environment; SNMP monitoring non-functional for down/crash events |

**Symptom:**
SNMP trap manager receives `itenProngSystemRestart` (s=4) and `itenProngAppUp` (s=6) traps correctly on platform restart, but never receives `itenProngAppDown` (s=7) or `itenProngAppCrash` (s=8) traps when services are stopped or crash.

**Root Cause:**
The platform's internal shutdown handler (`/opt/itential/platform/server/core/startup/Shutdown.js`) has a 3-second timeout and exits after Redis cleanup — it does not emit AppDown traps for stopping services. There is no mirror logic to the AppUp emission on startup. For crashes, the systemd unit has `Restart=on-failure` but no `ExecStopPost` hook, and a dead process cannot self-report.

Confirmed by: tcpdump on UDP 162 across a full restart + stop cycle — 0 packets with s=7 or s=8.

**Detection Hints:**
- AppUp and SystemRestart traps work → SNMP is configured correctly; the deficiency is in the shutdown path
- Check `journalctl -u itential-platform` for `Shutdown.js: 'Shutdown timeout has been hit'`
- Check systemd unit for absence of `ExecStopPost`

**Workaround (immediate):**
Add `ExecStopPost` to the systemd unit to send a platform-level AppDown trap on any stop or crash:
```
sudo systemctl edit itential-platform
```
Add under `[Service]`:
```ini
ExecStopPost=/usr/bin/snmptrap -v 1 -c public <SNMP_MANAGER_IP> .1.3.6.1.4.1.47688.1.1.1.0 "" 6 7 "" .1.3.6.1.4.1.47688.1.1.1.1.1.0 s "itential-platform"
```
Prerequisite: `sudo dnf install net-snmp-utils -y`

**Limitation:** Workaround sends one platform-level AppDown trap, not per-microservice. Per-microservice granularity requires the platform fix in ENG-24868.

**Verification:**
1. Apply workaround + `systemctl daemon-reload`
2. Run `sudo tcpdump -i lo -nn udp port 162 -v &`
3. Run `systemctl stop itential-platform`
4. Confirm s=7 trap arrives at SNMP manager

---

### [ISD-9261] Enable query TypeError on looped childJob task

| Field | Value |
|-------|-------|
| **Ticket** | ISD-9261 |
| **Component** | Automation Studio UI — childJob task input panel / enable query handler |
| **Platform Version** | 6.4.0 (confirmed) |
| **Severity** | S3 — UI bug, workaround available |

**Symptom:**
User clicks the "enable query" toggle on a looped childJob task input field and receives:
```
TypeError: Cannot set properties of undefined (setting 'childJobLoopIndex')
```
The query editor does not open. The task details panel may go blank, freeze, or close. Error appears only in the browser developer console (F12 → Console) — **not** in `job.error[]`, not in platform logs.

**Pre-condition (mandatory for trigger):**
- childJob task must have `loopType` set (`parallel` or `sequential`)
- `data_array` must be configured
- The enable query toggle only appears when both conditions are met

**Root Cause:**
The Automation Studio enable query handler includes childJob-specific initialization code that sets `taskConfig.childJobLoopIndex` when the toggle is clicked. At that moment, `taskConfig` has not yet been instantiated — the assignment throws before the editor opens. This path is only reached when `loopType` is set.

**Resolution Steps:**
1. Check browser console (F12) for `TypeError: Cannot set properties of undefined (setting 'childJobLoopIndex')` — confirms this is ISD-9261
2. Confirm no platform-side issue: check `job.error[]` — will be empty; check platform logs — no errors
3. Apply workaround (see below)
4. File or reference ENG bug ticket for permanent fix

**Workaround:**
Do not use enable query on childJob task inputs. Pre-extract the array before the childJob so `data_array` references it directly.

**Option A — Pass the array as a separate flat job input:**
```json
{
  "workflow": "your-workflow",
  "options": {
    "variables": {
      "devices": [{"deviceName": "router-01", ...}]
    }
  }
}
```
Wire `data_array` to `$var.job.devices` — no enable query needed.

**Option B — Pre-process with a newVariable or merge task:**
Add a task before the childJob that builds the array from the input object:
- `newVariable` task: set `name=inventory`, `value={"devices":[...]}` (object with nested array)
- Wire `data_array` to `$var.job.devices` (the flat workaround input)

**Detection Hints:**
| Signal | Meaning |
|--------|---------|
| Browser console has `childJobLoopIndex` error | Confirmed ISD-9261 — UI bug |
| No error in `job.error[]` | Platform is unaffected — UI only |
| Adapter task panel works normally | Bug is childJob-specific |
| Error only fires when loop is enabled | Pre-condition check |
| IAP 6.4.0 | Affected version — check fix version when ENG ticket is filed |

**Verification:**
1. Apply Option A or B workaround
2. Start job with flat array variable
3. Confirm loop executes: all child jobs run in parallel, `parentStatus=success`
4. Confirm browser console has no `childJobLoopIndex` error during the run

**Demo Environment:**
- PE Labs: `https://p6.pe.itential.io:3443`
- Parent workflow ID: `cf4d44a1-2429-4a91-81a3-c633d825c8e9` ("ISD-9261 - Parent Workaround Demo")
- Child workflow ID: `a827f11d-8549-45a7-bb8a-e88ee5733c0c` ("ISD-9261 - Child Demo")
- STR: `data/2026-06-09T00-00-00/ISD-9261/steps-to-reproduce.md`

**Sample payload (workaround demo):**
```json
{
  "workflow": "ISD-9261 - Parent Workaround Demo",
  "options": {
    "variables": {
      "devices": [
        {"deviceName": "router-core-01", "configKey": "ntp-server",  "configValue": "192.168.1.10"},
        {"deviceName": "router-core-02", "configKey": "ntp-server",  "configValue": "192.168.1.10"},
        {"deviceName": "switch-dist-01", "configKey": "syslog-host", "configValue": "10.0.0.5"}
      ]
    }
  }
}
```

**Intended query value** (what user would type if enable query worked):
```
[devices]
```

**Replicated by:** Ahmed Al-Zubidy (Product support cloud, 2026-06-09), Builder agent (PE Labs, 2026-06-09)

---

### [ISD-9500] Workflow 30x slower after upgrade — MongoDB majority write concern on distributed cluster

| Field | Value |
|-------|-------|
| **Ticket** | ISD-9500 |
| **Component** | MongoDB (replica set write concern) + Redis (contributing factor) |
| **Platform Version** | 6.4 (new build), regression vs. 22.1 |
| **Severity** | Major — production workflow degraded from 3 seconds to 1.5 minutes |

**Symptom:**
Same workflow that completed in ~3 seconds on IAP 22.1 took ~1.5 minutes on a new Platform 6.4 build. The workflow logic was unchanged — only the platform version/environment differed.

**Root Cause:**
Two contributing issues, found in order:
1. One Redis node in the cluster was down. Fixing this alone did not resolve the slowness.
2. The real cause: MongoDB replica set was configured with a **`majority` write concern**. Because this is a **distributed, multi-site Mongo cluster**, every write had to wait for acknowledgment from secondaries across sites before returning — that cross-site round-trip was the actual source of the delay. This is not visible from the workflow or job data itself; it only shows up as generalized slowness across every task that writes job state.

**Detection Hints:**
- Symptom is "workflow got slower after a platform upgrade/rebuild" with **no logic change** — rules out the workflow itself
- Reproduce the same workflow in a known-healthy environment (e.g., another Platform instance) — if it's fast there, the problem is environment/infra, not the workflow or the platform version's code
- Check Redis cluster node health first (quick to rule in/out) — but don't stop there if slowness persists after fixing it
- Check MongoDB replica set write concern configuration, especially on **multi-site/geo-distributed** clusters — `majority` forces cross-site secondary acknowledgment on every write

**Resolution:**
Change the MongoDB write concern from `majority` to `w: 2` (acknowledgment from 2 nodes, satisfiable locally without waiting on cross-site secondaries):
```javascript
// example — set at the connection/driver or replica set default level per your MongoDB deployment
{ writeConcern: { w: 2 } }
```

**Verification:**
1. Confirm Redis cluster shows all nodes healthy
2. Confirm MongoDB write concern updated to `w: 2`
3. Re-run the same workflow and confirm duration returns to baseline (~seconds, not minutes)

**Resolved by:** David Haywood (2026-08-17), reproduced customer's exact workflow in his own P6.5 environment (2 seconds) to confirm the workflow itself was not the cause before pivoting to infra.

---

### [ISD-9506] New SSO accounts not created/authorized — group mapping mismatch vs. MongoDB

| Field | Value |
|-------|-------|
| **Ticket** | ISD-9506 |
| **Component** | SSO / IdP (Azure AD) — group-to-role provisioning |
| **Platform Version** | Labs environment |
| **Severity** | Blocking (Outage) — new users entirely unable to log in |

**Symptom:**
New SSO users could not log into the Itential environment. Accounts were not being authorized and not being created at all. Customer observed that SSO group mapping values did not match what existed in MongoDB.

**Root Cause:**
Customer-side misconfiguration, not a Platform bug — the affected users were missing the required group assignments in their **Azure AD** (IdP) tenant. Because the users weren't in the groups Itential's SSO integration expects, no group claim was asserted at login, so the platform had no group to map to a role and never provisioned/authorized the account. The "values that don't match Mongo" symptom is the visible effect of a claim that was never sent, not a Platform-side mapping bug.

**Detection Hints:**
- Symptom pattern: new SSO users fail to log in / accounts never get created, while existing users are unaffected — points at provisioning/group-claim issues rather than general SSO/auth breakage
- Before assuming a Platform mapping bug, verify the affected users actually have the expected group assignments on the **IdP side** (Azure AD / Okta / etc.) — this is the first thing to check, ahead of comparing Platform `groups`/`roles` collections in MongoDB
- Related: ENG-25981 (feature request for automatic SSO/IdP group-to-role mapping) confirms group→role mapping is a limited/manually-configured capability today — reinforces that config/assignment issues on the IdP side are the more likely cause vs. a platform defect

**Resolution:**
Customer added the missing group assignments for the affected users in Azure AD. No Platform-side change required.

**Verification:**
New SSO users with correct Azure AD group assignment could log in and were authorized/created successfully.

---

### [Pattern] Adapter OFFLINE — token_timeout: -1

| Field | Value |
|-------|-------|
| **Component** | IAG adapter (GatewayManager) |
| **Symptom** | Adapter authenticates on startup, goes ONLINE briefly, then drops OFFLINE after first token expires |

**Root Cause:** `token_timeout: -1` — adapter never refreshes the token after initial auth.

**Resolution:** Set `token_timeout: 3600000` (1 hour in ms) in adapter settings.

**Verification:** Adapter stays ONLINE through a full token refresh cycle.

---

### [Pattern] No config found for Adapter: X

| Field | Value |
|-------|-------|
| **Component** | Workflow Engine — adapter task routing |
| **Symptom** | `"No config found for Adapter: {name}"` at runtime on adapter tasks |

**Root Cause:** `app` field on the workflow task uses the adapter **instance name** instead of the adapter **type name**. `app` and `locationType` must be the type name from `apps.json` (e.g., `EmailOpensource`, `Servicenow`). The adapter instance name (e.g., `email`, `servicenow-prod`) belongs in `adapter_id` only.

**Resolution:** Fix `app` and `locationType` to the type name from `apps.json`. The `adapter_id` field holds the instance name.

---

### [Pattern] Job has no available transitions

| Field | Value |
|-------|-------|
| **Component** | Workflow Engine — task error handling |
| **Symptom** | Job gets stuck in `running` state after adapter or external task fails |

**Root Cause:** No `"state": "error"` transition on the failing adapter/external task. When the task errors, WFE finds no valid transition and the job stalls.

**Resolution:** Add error transition on every adapter and external task:
```json
"transitions": {
  "{taskId}": {
    "{errorHandlerTaskId}": {"type": "standard", "state": "error"}
  }
}
```

---

### [Pattern] Adapter in stub mode

| Field | Value |
|-------|-------|
| **Component** | Adapter configuration |
| **Symptom** | Adapter calls return empty/stub data; no real API calls are made |

**Root Cause:** `stub: true` in adapter settings — adapter is in stub mode and does not call the target system.

**Resolution:** Set `stub: false` in adapter settings.

---

### [Pattern] WFE log > 500MB + slow jobs

| Field | Value |
|-------|-------|
| **Component** | WorkflowEngine — logging |
| **Symptom** | Jobs noticeably slower than expected; WFE log file is very large |

**Root Cause:** `console_level: spam` in WFE settings — produces extreme I/O volume that degrades job throughput.

**Resolution:** Set `console_level: error` in WFE settings.

---

### [Pattern] Jobs COLLSCAN + slow at scale

| Field | Value |
|-------|-------|
| **Component** | MongoDB — jobs collection |
| **Symptom** | Slow job queries at high job volume; MongoDB slow query log shows COLLSCAN on jobs collection |

**Root Cause:** Missing `{status: 1}` index on the jobs collection.

**Resolution:** Add index with DBA consent:
```javascript
db.jobs.createIndex({status: 1})
```

---

### [Pattern] OOMKilled container

| Field | Value |
|-------|-------|
| **Component** | Container runtime (Docker / Kubernetes) |
| **Symptom** | Container exits unexpectedly; `docker inspect` or pod events show `OOMKilled` |

**Root Cause:** Container memory limit set too low for the workload.

**Resolution:** Increase Docker memory limit or Kubernetes resource limit for the affected container.

---

### [Pattern] ASIA* AWS key prefix + adapter OFFLINE

| Field | Value |
|-------|-------|
| **Component** | AWS adapter / IAM credentials |
| **Symptom** | Adapter using AWS credentials goes OFFLINE; key ID starts with `ASIA` |

**Root Cause:** `ASIA*` prefix indicates STS temporary credentials — they have a short TTL and expire. Long-lived IAM access keys use `AKIA` prefix.

**Resolution:** Replace temporary STS credentials with long-lived IAM access key (`AKIA` prefix).

---

### [Pattern] Workflow errors[] not empty after import

| Field | Value |
|-------|-------|
| **Component** | Automation Studio — workflow import/validation |
| **Symptom** | Workflow is in draft state after import; `errors[]` array is not empty |

**Root Cause:** Validation errors during import leave the workflow in draft state. Cannot be started until all validation errors are resolved.

**Resolution:** Check each entry in `errors[]`, fix the workflow, re-PUT. Common causes: non-hex task IDs, missing transitions, invalid `app`/`locationType` values.

---

### [Pattern] $var reference resolves to undefined

| Field | Value |
|-------|-------|
| **Component** | Workflow Engine — variable resolution |
| **Symptom** | `$var.<taskId>.<variable>` resolves to `undefined` at runtime |

**Root Cause:** Non-hex task ID on the referenced task. WFE classifies non-hex task IDs as static values and never resolves them. Task IDs must match `[0-9a-f]{1,4}`.

**Resolution:** Rename the task ID to a valid hex value (e.g., `a1b2`, `ef01`).

---

### [Pattern] childJob Cannot find workflow: X at runtime

| Field | Value |
|-------|-------|
| **Component** | Workflow Engine — childJob task |
| **Symptom** | childJob task fails with `Cannot find workflow: {name}` at runtime |

**Root Cause:** childJob references the plain workflow name (`workflow: "MyWorkflow"`) but the asset is project-scoped after being added to a project. Project-scoped workflow names are prefixed with `@{projectId}: `.

**Resolution:** Update `workflow` field to `@{projectId}: {name}` format to reference the project-scoped asset.

---

### [ISD-9502] Operations Manager jobs API — child job filtering and variable dereferencing limits

| Field | Value |
|-------|-------|
| **Ticket** | ISD-9502 |
| **Type** | Service Request (Labs) — API behavioral investigation |
| **Component** | `GET /operations-manager/jobs` — query DSL and `dereference` parameter |
| **Platform** | `p6.pe.itential.io` (tested empirically; OpenAPI spec does not document query params for this endpoint) |
| **Severity** | S4 — No customer impact; Lab / API exploration |

**Symptom (Question 1):**
Customer expected `equals[ancestors]=<parentJobId>` to return only the immediate children of a job. It instead returns the full descendant subtree at any depth.

**Root Cause (Q1 — `ancestors` semantics):**
`ancestors` on every job document is an inclusive lineage array `[root, ..., self]`. Filtering `equals[ancestors]=<id>` matches every job whose lineage contains `<id>` — meaning every descendant at every depth, not just immediate children. `parent.job` (the actual immediate-parent pointer) is **not** in the `equals`-operator allowlist for this resource; all attempts to filter on `parent.job` via `equals[parent.job]`, `equals[parent][job]`, `in[parent.job]`, or `contains[parent.job]` are rejected with `"Parameter 'equals' received invalid property paths"`. Other operators (`regex`, `elemMatch`, `match`, `like`, `startsWith`) are silently no-ops on unknown fields.

**Workaround (Q1):**
Use `GET /operations-manager/jobs?equals[ancestors]=<parentJobId>` to retrieve the full subtree, then filter client-side on `parent.job === parentJobId` in the returned documents. The `parent` field is present in list-endpoint responses even though it cannot be used as a server-side filter.

**Symptom (Question 2):**
Variables returned by the list endpoint (`GET /operations-manager/jobs`) appear as `{"location": "job_data", "_id": "..."}` reference objects, not resolved values. Customer wanted a `dereference` parameter to inline them.

**Root Cause (Q2 — `dereference` behavior):**
The list endpoint's `dereference` parameter accepts only `tasks` as a valid target. Every other value (`true`, `variables`, `all`, `job_data`, `job-data`, `jobData`, `*`, `variables.*`, per-field paths like `variables.vip_ip`) is rejected with `"Unsupported dereference target(s)"`. `dereference=tasks` adds a `tasks` key to each result document but leaves `variables` as reference objects. The reference-object behavior is by design on the bulk list endpoint to avoid full-document expansion cost across potentially millions of rows.

**Workaround (Q2):**
Use `GET /operations-manager/jobs/{id}` (single-job-by-ID) for each job of interest. This endpoint always returns fully-resolved `variables` inline — no `dereference` parameter needed. It is the extra per-job API call the customer was trying to avoid, but it is the only confirmed path to inline variable values today.

**Detection Hints (for future similar tickets):**
- If a customer reports "job variables show as `{location: job_data, _id: ...}` objects" → they are hitting the list endpoint. Redirect to the single-job endpoint.
- If a customer reports "filter only returns distant descendants, not just children" → they are relying on `ancestors` semantics. Redirect to client-side `parent.job` filtering.
- These are API design limitations, not bugs. Enhancement requests for both are candidates for ENG backlog if customer need is strong.

**Verification:**
No platform-side fix. Confirm workarounds work for the customer's use case by testing `GET /operations-manager/jobs/{id}` and client-side `parent.job` filtering against their job set.


---

### [ISD-9593] Aruba AirWave adapter HTTP 403 on token request — AirWave role, empty request body, and field/redirect mismatches

| Field | Value |
|-------|-------|
| **Ticket** | ISD-9593 |
| **ENG Bug** | ENG-27979 (logging defect only — NOT the root cause of the 403; see Root Cause) |
| **Component** | Adapter (OSS) — `@itentialopensource/adapter-aruba_airwave`, auth settings + AirWave-side role config |
| **Platform Version** | 6.5.1 (confirmed) |
| **Severity** | S3 — new adapter integration blocked, single customer, workaround available |

**Symptom:**
New AirWave adapter instance (`auth_method: request_token`) fails all token/login requests to AirWave's `/LOGIN` endpoint with HTTP 403, returning AirWave's HTML login page instead of a token. Identical credentials succeed when tested manually via Postman/curl. Adapter debug log (`auth_logging: true`) shows the `FULL BODY` line missing `&` separators between form fields (e.g., `credential_0=...credential_1=...destination=...`) — this specific symptom is a known **logging-only artifact** (see ENG-27979) and should not be treated as proof the actual request was malformed.

**Root Cause:**
Not isolated to a single change — customer applied several corrections together and confirmed the fix without isolating which one resolved it. Contributing factors identified:
1. The AirWave-side account used by the adapter lacked a role with API access enabled (AirWave's role types are AMP Administrator, Device Manager, AirWave Management Client, Guest Access Sponsor — API access must be explicitly granted via role; a successful AirWave **UI** login does not imply API access).
2. Adapter's `auth_request_datatype` was blank. Contrary to the initial investigation hypothesis, this did **not** reliably fall back to the adapter's `action.json`-level `requestDatatype: URLENCODE` in practice — the original failing request showed `Content-Length: 0`, i.e., no login body was sent to AirWave at all. Setting `auth_request_datatype` explicitly to `URLENCODE` was one of the changes applied to resolve this.
3. Adapter's `token_user_field`/`token_password_field` did not match AirWave's expected `/LOGIN` form field names (`credential_0`/`credential_1`) at the adapter-settings level.
4. `request.number_redirects` was set to `1`; AirWave returns the session cookie and `X-BISCOTTI` header on the initial 302 response itself, so following a redirect was unnecessary and was set to `0`.

Confirmed by: customer's independent curl test from a workstation (POST `/LOGIN` → 302 + session cookie + `X-BISCOTTI` → GET → 200 with data) and from the IAP host via a REST Call task, both outside the adapter, both succeeding after the AirWave-side role/account change combined with the adapter property changes above.

**Detection Hints:**
- `FULL BODY` debug log line missing `&` separators between token-request fields whose names don't match adapter-utils's hardcoded sensitive-word list — **do not** treat this alone as proof of a malformed wire request (logging artifact, ENG-27979); verify with a packet capture or the target system's own access logs instead.
- `Content-Length: 0` on the token request (visible via packet capture or target-side access log) — a genuine indicator of an empty request body, distinct from the logging artifact above.
- Target system returning a full HTML login page (not a JSON error) with HTTP 403 is consistent with a request that never reached credential parsing — check account role/API-access before assuming a request-construction defect.
- Negative hint: if the same credentials succeed via Postman/curl outside the adapter, this rules out network/credential issues broadly, but does NOT rule out an account role lacking API access specifically for programmatic (vs. UI) access — test both separately.

**Workaround (immediate):**
1. In AirWave, create/assign a role with explicit API access (e.g., an AMP Administrator role) to the account used by the adapter — do not assume a UI-login-capable account has API access.
2. Set the adapter's `auth_request_datatype` property explicitly to `URLENCODE` rather than leaving it blank.
3. Confirm `token_user_field`/`token_password_field` adapter settings match the target system's expected form field names.
4. Set `request.number_redirects` to `0` for this adapter, since AirWave returns the session cookie on its initial 302 response.
5. Ignore the `FULL BODY` debug log line's missing `&` separators — known logging artifact (ENG-27979), not indicative of the real request.

**Verification:**
1. Independently test the account (curl/Postman, and/or a REST Call task from the IAP host) against the login endpoint with the adapter's configured field names — confirm a 302 + session cookie, not a 403/login-page response.
2. Trigger the adapter's token request and confirm it transitions to Online / a subsequent data call returns valid data rather than erroring.
