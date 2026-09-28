---
name: troubleshoot-workflows
description: Troubleshoot workflow failures, job errors, JST errors, import failures, and validation errors in Itential Platform. Analyzes job error arrays, task outputs, and transition gaps.
argument-hint: "[workflow name or job ID]"
---

# Troubleshoot Workflows

**Owns:** Workflow and job failure diagnosis — job errors, task-level failures, JST (JavaScript Transformation) errors, workflow import failures, validation errors, and childJob wiring issues.
**Use when:** A workflow job errored or is stuck, a workflow won't import, a JST script is failing, or a workflow is producing wrong output.

---

## CRITICAL SAFETY RULES

- **GET requests only** — no PUT, PATCH, POST, DELETE without explicit engineer approval
- **Read `.env` for credentials** — never ask the user for credentials already in `.env`
- **Never modify workflow definitions** without explicit user consent
- **builder-skill invocations also use `.env`** — when invoking `/builder-agent` or other builder-skills for fixes or workarounds, the same `.env` is the credential source. Source it before invoking so the skill knows which platform to target.

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

## Phase 1: Triage

Ask the user (or infer from context):
- **Workflow/job name** — which workflow is failing?
- **Job ID** — if the user has a specific job ID
- **Error message** — copy/paste from UI or logs
- **When did it start failing** — was it working before?
- **Type of failure:**
  - Job errored during execution
  - Workflow won't import (validation error)
  - JST script error
  - ChildJob not completing
  - Specific task failing

---

## Phase 2: Job Error Analysis

### Step 2a — Fetch the Failing Job

If the user has a job ID:
```bash
curl -sk "{PLATFORM_URL}/operations-manager/jobs/{JOB_ID}?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
j = d if d.get('_id') else d.get('job', d)
print('Job ID:   ', j.get('_id'))
print('Workflow: ', j.get('name'))
print('Status:   ', j.get('status'))
print('Start:    ', j.get('start_time') or j.get('startTime'))
print('End:      ', j.get('end_time') or j.get('endTime') or '(still running)')
print()

# Print job.error array
errors = j.get('error', [])
if isinstance(errors, list):
    print(f'Errors ({len(errors)}):')
    for e in errors:
        if isinstance(e, dict):
            iap = e.get('IAPerror', e)
            print('  source:      ', iap.get('source','?'))
            print('  displayString:', iap.get('displayString','?'))
            print('  raw:         ', str(e)[:200])
            print()
elif errors:
    print('Error:', errors)
else:
    print('No errors in job.error array')
"
```

If no job ID, search recent jobs:
```bash
curl -sk "{PLATFORM_URL}/operations-manager/jobs?name={WORKFLOW_NAME}&status=error&limit=5&token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
jobs = d.get('jobs', d.get('results', []))
print(f'Found {len(jobs)} recent error jobs:')
for j in jobs:
    print(f'  {j.get(\"_id\",\"?\")}  {j.get(\"name\",\"?\")}  {j.get(\"start_time\",\"?\")[:19]}')
"
```

### Step 2b — Analyze Failing Task

```bash
curl -sk "{PLATFORM_URL}/operations-manager/jobs/{JOB_ID}?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
j = d if d.get('_id') else d.get('job', d)
tasks = j.get('tasks', {})
print(f'Tasks ({len(tasks)}):')
for tid, t in tasks.items():
    if not isinstance(t, dict): continue
    status = t.get('status','?')
    name   = t.get('name', tid)
    flag   = '🔴' if status == 'error' else ('⚠️' if status == 'incomplete' else '')
    print(f'  {flag} [{status}] {name}  (id: {tid})')
    if status == 'error':
        output = t.get('output', {})
        print(f'    output: {str(output)[:300]}')
        err = t.get('error')
        if err:
            print(f'    error: {str(err)[:300]}')
"
```

### Step 2c — Error Source Routing

| `IAPerror.source` | Likely Cause | Next Step |
|-------------------|-------------|-----------|
| `adapter` | Adapter misconfiguration or connectivity | Run `/troubleshoot-adapters {adapter_name}` |
| `automation-studio` | Workflow validation error or missing task | Check workflow definition (Step 3) |
| `operations-manager` | Worker crash or job timeout | Run `/troubleshoot-jobs` |
| `WorkFlowEngine` | WFE execution error | Run `/troubleshoot-jobs` |
| `[task name]` | Task-specific error | Check task output (Step 2b) |
| `jst` | JavaScript Transformation error | See Phase 4 (JST errors) |

---

## Phase 3: Workflow Definition Analysis

### Step 3a — Get Workflow

```bash
# By name
curl -sk "{PLATFORM_URL}/automation-studio/workflows?name={WORKFLOW_NAME}&token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
items = d.get('items', d.get('results', []))
print(f'Found {len(items)} workflow(s):')
for w in items:
    print(f'  {w.get(\"_id\",\"?\")}  {w.get(\"name\",\"?\")}  v{w.get(\"version\",\"?\")}  {\"VALID\" if not w.get(\"errors\") else \"HAS ERRORS\"} ')
    errs = w.get('errors', [])
    if errs:
        print('  Validation errors:')
        for e in errs[:5]:
            print(f'    - {e}')
"
```

### Step 3b — Check Workflow Validation Errors

A workflow with validation errors is in **draft** state — it cannot be started.

```bash
curl -sk "{PLATFORM_URL}/automation-studio/workflows/{WORKFLOW_ID}?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
w = d.get('data', d)
errors = w.get('errors', [])
if errors:
    print(f'🔴 Workflow has {len(errors)} validation error(s):')
    for e in errors:
        print(f'  - {e}')
    print()
    print('This workflow is in DRAFT state and cannot be started until errors are resolved.')
else:
    print('✅ No validation errors — workflow is valid')

# Check for missing task transitions
tasks = w.get('tasks', {})
print(f'Tasks: {len(tasks)}')
for tid, t in tasks.items():
    if not isinstance(t, dict): continue
    trans = t.get('transitions', {})
    if not trans and tid not in ('workflow_start', 'workflow_end'):
        print(f'  ⚠️ Task {t.get(\"name\",tid)}: no outgoing transitions')
    # Check for error transition on adapter/external tasks
    actor = t.get('actor', {})
    if actor.get('type') in ('adapter', 'application') and 'error' not in trans:
        print(f'  ⚠️ Task {t.get(\"name\",tid)}: no error transition (job will get stuck on task failure)')
"
```

**Fix path — missing error transition (with engineer approval):**

When `⚠️ Task {name}: no error transition` is detected, offer to apply the fix via `/builder-agent`:

```bash
# GET current workflow definition
curl -sk "{PLATFORM_URL}/workflow_builder/workflows/{WORKFLOW_ID}?token={TOKEN}" \
  | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))" \
  > /tmp/workflow_fix.json

# Apply fix: add {"state": "error", "to": "workflow_end"} to identified task's transitions
# (or route to a named error-handler task if one exists in the workflow)
python3 << 'EOF'
import json
with open('/tmp/workflow_fix.json') as f:
    wf = json.load(f)
tasks = wf.get('tasks', {})
TASK_ID = "{TASK_ID}"  # from detection above
if TASK_ID in tasks:
    tasks[TASK_ID].setdefault('transitions', {})['error'] = [{'to': 'workflow_end', 'condition': ''}]
    print(f"Added error transition to task {TASK_ID}")
with open('/tmp/workflow_fixed.json', 'w') as f:
    json.dump(wf, f)
EOF

# PUT the corrected workflow (full replacement — confirm with engineer first)
curl -sk -X PUT "{PLATFORM_URL}/workflow_builder/workflows/{WORKFLOW_ID}?token={TOKEN}" \
  -H "Content-Type: application/json" \
  -d @/tmp/workflow_fixed.json | python3 -c "import sys,json; d=json.load(sys.stdin); print('Updated:', d.get('name','?'))"
```

> **Invoke `/builder-agent`** for complex workflows where multiple tasks need error transitions or where an explicit error-handler path (not just `workflow_end`) should be wired.

### Step 3c — Common Validation Error Patterns

| Error | Root Cause | Fix |
|-------|-----------|-----|
| `Task {name} not found in catalog` | Task name typo or wrong app | Verify task name via `/workflow_builder/tasks/list` |
| `Missing required field: {field}` | Task input not wired | Check task schema, wire the field |
| `Circular reference detected` | childJob referencing itself | Trace childJob chain |
| `No config found for Adapter: {name}` | `app` field set to instance name (not type name) | Set `app` = adapter type from `apps.json`, not instance name |
| `$var reference unresolvable` | Variable name not defined in upstream task | Check source task output schema and variable name |
| `Task ID contains non-hex characters` | Task ID like `apush` instead of hex | Regenerate task with valid hex ID |

**Fix path — wrong `app` field (with engineer approval):**

When `No config found for Adapter: {name}` is detected and the root cause is an instance name in the `app` field:

```bash
# Look up the correct type name from the platform
curl -sk "{PLATFORM_URL}/adapter-manager/adapters?token={TOKEN}" \
  | python3 -c "
import sys, json
adapters = json.load(sys.stdin)
items = adapters.get('items', adapters) if isinstance(adapters, dict) else adapters
for a in items:
    print(a.get('id','?'), '→ type:', a.get('type','?'), '  instance:', a.get('name','?'))
" 2>/dev/null | grep -i '{ADAPTER_NAME}'

# GET workflow, fix app field in all affected tasks, PUT back
curl -sk "{PLATFORM_URL}/workflow_builder/workflows/{WORKFLOW_ID}?token={TOKEN}" \
  | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))" \
  > /tmp/workflow_appfix.json

python3 << 'EOF'
import json
with open('/tmp/workflow_appfix.json') as f:
    wf = json.load(f)
WRONG_APP = "{INSTANCE_NAME}"   # what's currently in the workflow
CORRECT_APP = "{TYPE_NAME}"     # what it should be (from adapter type above)
fixed = 0
for tid, t in wf.get('tasks', {}).items():
    actor = t.get('actor', {})
    if actor.get('app') == WRONG_APP:
        actor['app'] = CORRECT_APP
        fixed += 1
        print(f"  Fixed task {tid}: app {WRONG_APP!r} → {CORRECT_APP!r}")
with open('/tmp/workflow_appfixed.json', 'w') as f:
    json.dump(wf, f)
print(f"Fixed {fixed} task(s). Wrote /tmp/workflow_appfixed.json")
EOF

# PUT corrected workflow (confirm with engineer first)
curl -sk -X PUT "{PLATFORM_URL}/workflow_builder/workflows/{WORKFLOW_ID}?token={TOKEN}" \
  -H "Content-Type: application/json" \
  -d @/tmp/workflow_appfixed.json \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Updated:', d.get('name','?'))"
```

> **Invoke `/builder-agent`** when multiple adapter types need fixing in the same workflow, or when the correct type name isn't obvious from the adapter list (Rule 3/23: `app` = type name, `adapter_id` = instance name).

---

## Phase 4: JST (JavaScript Transformation) Errors

### Step 4a — Get the JST

```bash
# List JSTs
curl -sk "{PLATFORM_URL}/transformations?name={JST_NAME}&token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
items = d.get('items', d.get('results', []))
for t in items:
    print(t.get('_id','?'), t.get('name','?'))
"

# Get specific JST by ID
curl -sk "{PLATFORM_URL}/transformations/{JST_ID}?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
t = d.get('data', d)
print('Name:', t.get('name'))
print()
print('=== incoming_script ===')
print(t.get('incoming_script','(none)'))
print()
print('=== outgoing_script ===')
print(t.get('outgoing_script','(none)'))
"
```

### Step 4b — Common JST Failure Patterns

JST scripts run in a sandboxed Node.js context. Common issues:

| Issue | Symptom | Fix |
|-------|---------|-----|
| No `return` statement | Script runs but output is `null` | Add `return result;` at end |
| Async code | `Promise {}` in output | JST context is synchronous — remove async/await |
| `undefined` variable | `TypeError: Cannot read property of undefined` | Check that incoming data has the expected shape |
| JSON parse of string | `SyntaxError: Unexpected token` | Use `JSON.parse()` on string fields from task output |
| Access to `require` | `require is not defined` | JST sandbox has no `require` — use built-in JS only |

### Step 4c — Test the JST Script Locally

If the task's input data is available from the job output, test the script manually:

```bash
node -e "
// Paste the incoming_script here and wrap in a function
// Provide a sample 'incoming' object matching the task's input
const incoming = {/* paste from job task output */};
// Paste incoming_script body here:
{INCOMING_SCRIPT}
"
```

**Fix path — JST script correction (with engineer approval):**

When the local `node -e` test confirms the corrected script works:

```bash
# GET current JST to confirm latest version
curl -sk "{PLATFORM_URL}/transformations/{JST_ID}?token={TOKEN}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d.get('data',d), indent=2))" \
  > /tmp/jst_current.json

# Apply the corrected script (modify incoming_script or outgoing_script in place)
python3 << 'EOF'
import json
with open('/tmp/jst_current.json') as f:
    jst = json.load(f)
# Replace with corrected script body:
jst['incoming_script'] = """{CORRECTED_INCOMING_SCRIPT}"""
with open('/tmp/jst_fixed.json', 'w') as f:
    json.dump(jst, f)
print("Script updated in /tmp/jst_fixed.json")
EOF

# PUT the corrected JST (confirm with engineer before executing)
curl -sk -X PUT "{PLATFORM_URL}/transformations/{JST_ID}?token={TOKEN}" \
  -H "Content-Type: application/json" \
  -d @/tmp/jst_fixed.json \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Updated JST:', d.get('name', d.get('data',{}).get('name','?')))"
```

> **Invoke `/builder-agent`** when the JST fix is more than a single-line correction — e.g., restructuring the transformation logic, handling multiple script sections, or when the fix needs to be applied alongside a workflow change.

---

## Phase 5: Workflow Import Failures

### Step 5a — Validate Before Import

```bash
# Check if workflow JSON is valid before attempting import
python3 -c "
import json, sys
try:
    with open('{WORKFLOW_FILE}') as f:
        d = json.load(f)
    print('JSON valid ✅')
    print('name:', d.get('name','?'))
    print('tasks:', len(d.get('tasks',{})))
    print('version:', d.get('version','?'))
except json.JSONDecodeError as e:
    print(f'🔴 JSON parse error: {e}')
"
```

### Step 5b — Common Import Failure Causes

| Cause | Symptom | Check |
|-------|---------|-------|
| Invalid JSON | `SyntaxError` on import | Validate JSON syntax |
| Missing `name` field | Import rejected | Ensure top-level `name` key exists |
| Task references non-existent app | `No config found` at runtime | Verify `app` field matches `apps.json` type names |
| Circular childJob references | Import succeeds but job hangs | Trace all childJob task `workflow` references |
| Version conflict | `Workflow already exists` | Bump `version` field or delete existing |
| Task IDs with non-hex characters | Workflow validates but `$var` refs silently fail | Ensure all task IDs are `[0-9a-f]{1,4}` |

**Fix path — non-hex task ID repair (with engineer approval):**

```bash
# Detect and fix non-hex task IDs in a workflow file
python3 << 'EOF'
import json, re, random

with open('{WORKFLOW_FILE}') as f:
    wf = json.load(f)

tasks = wf.get('tasks', {})
non_hex = {tid: t for tid, t in tasks.items() if not re.fullmatch(r'[0-9a-f]{1,4}', tid)}
if not non_hex:
    print("All task IDs are valid hex — no fix needed")
    exit(0)

print(f"Found {len(non_hex)} non-hex task ID(s): {list(non_hex.keys())}")

# Generate new hex IDs (4-char, unique within the workflow)
existing_ids = set(tasks.keys())
id_map = {}
for old_id in non_hex:
    new_id = format(random.randint(0, 0xFFFF), '04x')
    while new_id in existing_ids:
        new_id = format(random.randint(0, 0xFFFF), '04x')
    id_map[old_id] = new_id
    existing_ids.add(new_id)

# Rebuild tasks dict with new IDs
new_tasks = {}
for tid, t in tasks.items():
    new_tid = id_map.get(tid, tid)
    new_tasks[new_tid] = t

# Rewrite all $var.tasks.{old_id} references in transitions and variable_maps
wf_str = json.dumps(wf)
for old_id, new_id in id_map.items():
    wf_str = wf_str.replace(f'"tasks.{old_id}"', f'"tasks.{new_id}"')
    wf_str = wf_str.replace(f'tasks.{old_id}.', f'tasks.{new_id}.')
wf = json.loads(wf_str)
wf['tasks'] = new_tasks

with open('/tmp/workflow_hex_fixed.json', 'w') as f:
    json.dump(wf, f, indent=2)

print(f"Renamed {len(id_map)} task ID(s): {id_map}")
print("Fixed workflow written to /tmp/workflow_hex_fixed.json — retry import")
EOF
```

> **Invoke `/builder-agent`** when the workflow has many tasks or complex variable wiring — it validates all `$var.tasks.{id}` references across transition conditions, variable_map entries, and childJob configs in a single pass.

### Step 5c — Check if Workflow Already Exists

```bash
curl -sk "{PLATFORM_URL}/automation-studio/workflows?name={WORKFLOW_NAME}&token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
items = d.get('items', [])
if items:
    print(f'Workflow already exists ({len(items)} version(s)):')
    for w in items:
        print(f'  v{w.get(\"version\",\"?\")}  id={w.get(\"_id\",\"?\")}')
else:
    print('Workflow does not exist — safe to import fresh')
"
```

---

## Phase 6: ChildJob Analysis

### Step 6a — Find All ChildJobs

```bash
# Get parent job and extract childJob task references
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
    print('ChildJob IDs:')
    for name, cid in child_ids:
        print(f'  {name}: {cid}')
else:
    print('No childJob outputs found in task outputs')
"
```

### Step 6b — Fetch ChildJob Status

```bash
curl -sk "{PLATFORM_URL}/operations-manager/jobs/{CHILD_JOB_ID}?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
j = d if d.get('_id') else d.get('job', d)
print('ChildJob:', j.get('_id'))
print('Workflow:', j.get('name'))
print('Status:  ', j.get('status'))
errors = j.get('error', [])
if errors:
    print('Errors:')
    for e in (errors if isinstance(errors, list) else [errors]):
        if isinstance(e, dict):
            iap = e.get('IAPerror', e)
            print('  ', iap.get('displayString','?'))
"
```

---

## Phase 7: Workflow Report

Save to `{project_path}/data/{TIMESTAMP}/workflow_report.md`:

```markdown
# Workflow Troubleshoot Report
**Generated:** {YYYY-MM-DD HH:MM:SS UTC}
**Workflow:** {WORKFLOW_NAME}
**Job ID:** {JOB_ID} | **Status:** {status}

## Error Summary
| Field | Value |
|-------|-------|
| IAPerror.source | {source} |
| IAPerror.displayString | {message} |
| Failing Task | {task name} |
| Task Status | {status} |

## Root Cause
{One-sentence diagnosis}

## Failing Task Output
```json
{task output snippet}
```

## Validation Errors
{List or "None"}

## Recommended Actions
1. {Highest-impact fix}
2. {Secondary action}
```

---

## Common Failure Patterns — Quick Reference

| Symptom | Root Cause | Action |
|---------|-----------|--------|
| Job status `error`, `IAPerror.source: adapter` | Adapter not reaching target system | Run `/troubleshoot-adapters {name}` |
| Job stuck in `running` with no task progress | WFE worker saturated or dead | Run `/troubleshoot-jobs` |
| Workflow won't start — `"validation errors"` | Draft workflow | Fix validation errors in Step 3b |
| `No config found for Adapter: {name}` | `app` field = instance name, not type name | Fix `app` field to match type from `apps.json` |
| `$var.job.x` not resolving | Upstream task didn't output `x`, or task ID is non-hex | Check task output schema and task IDs |
| JST output is `null` | Missing `return` statement | Add `return result` to outgoing/incoming script |
| ChildJob never completes | Parent job variable wiring issue | Check childJob task `variables` array format |
| `Job has no available transitions` | No error transition on failed adapter task | Add `"state": "error"` transition to the task |
| JSON Form dropdown not populating | REST binding endpoint or result path wrong | Run Phase 8 → invoke `/itential-json-forms` |
| MOP `<!var!>` not rendering in output | Variable name mismatch or wrong template type | Run Phase 9 → invoke `/itential-mop` |
| LCM action not updating instance state | Missing `instance` variable output or wrong model | Run Phase 10 → invoke `/itential-lcm` |

---

## Phase 8: JSON Form Issues

Route here when the ticket involves a JSON Form asset — schema structure errors, REST-bound dropdowns not populating, trigger variable mismatches, or import failures on `jsonForm`-type components.

### Step 8a — Identify the JSON Form

```bash
# List JSON forms by name
curl -sk "{PLATFORM_URL}/form-builder/forms?name={FORM_NAME}&token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for f in d.get('items', []):
    print(f.get('_id','?'), f.get('name','?'), '— schema keys:', list(f.get('schema',{}).get('properties',{}).keys())[:5])
"
```

### Step 8b — Common JSON Form Failure Patterns

| Symptom | Root Cause | Action |
|---------|-----------|--------|
| Form renders blank / no fields | `form[]` array out of sync with `schema.properties` | Reconcile form array vs schema |
| Dropdown not loading options | REST binding `endpoint` wrong or `resultPath` doesn't match response | Verify endpoint with a direct curl |
| Trigger variable not passed to workflow | `triggerVariable` name mismatch with workflow input | Match `triggerVariable` to workflow's first task `incoming` field |
| Import rejected | `jsonForm` component missing required metadata fields | Check `_id`, `name`, `schema`, `form` fields present |

### Step 8c — Invoke `/itential-json-forms`

```
/itential-json-forms
```

Provide: the exported form JSON, the specific error or symptom, and the target workflow's input schema. The skill will diagnose schema/binding issues and generate a corrected form definition.

---

## Phase 9: MOP Command Template Issues

Route here when the ticket involves MOP (Method of Procedure) command templates — `<!var!>` variables not rendering, analytic template type mismatch, validation rule regex errors, or import failures on `mopCommandTemplate`/`mopAnalyticTemplate` components.

### Step 9a — Identify the Template

```bash
# List MOP command templates
curl -sk "{PLATFORM_URL}/mop-manager/command-templates?name={TEMPLATE_NAME}&token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for t in d.get('items', []):
    print(t.get('_id','?'), t.get('name','?'), '— type:', t.get('type','?'))
"
```

### Step 9b — Common MOP Template Failure Patterns

| Symptom | Root Cause | Action |
|---------|-----------|--------|
| `<!var!>` appears literally in rendered output | Variable name mismatch or wrong delimiter | Verify `<!varName!>` exactly matches workflow variable name |
| Analytic template not matching device output | `type` field wrong (`textfsm` vs `regex`) or pattern mismatch | Verify template type and pattern against real device output |
| Validation rule rejects valid input | Regex error in `validationRule` field | Test regex independently with `node -e` |
| Import rejected | Missing `type`, `content`, or `name` fields | Check all required MOP fields present |

### Step 9c — Invoke `/itential-mop`

```
/itential-mop
```

Provide: the exported command template JSON, a sample of the device output (for analytic templates), and the specific rendering or validation error. The skill will diagnose and generate a corrected template.

---

## Phase 10: LCM Action Workflow Issues

Route here when the ticket involves LCM (Lifecycle Management) — action workflows not updating instance state, resource model import failures, action workflows not correctly wired to the resource model, or missing `instance` variable pattern.

### Step 10a — Identify the Resource Model

```bash
# List LCM resource models
curl -sk "{PLATFORM_URL}/lifecycle-manager/resource-models?token={TOKEN}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for m in d.get('items', []):
    print(m.get('_id','?'), m.get('name','?'), '— actions:', [a.get('name') for a in m.get('actions',[])])
"
```

### Step 10b — Common LCM Failure Patterns

| Symptom | Root Cause | Action |
|---------|-----------|--------|
| Instance state not updating after action | Action workflow missing `instance` variable output | Add `instance` to workflow output, wired from input |
| Action workflow not appearing in LCM UI | Workflow not wired to resource model action | Link workflow to action in resource model definition |
| Resource model import rejected | Missing required `states`, `actions`, or `instanceSchema` fields | Verify against the canonical LCM project in `vendor/builder-skills/helpers/assets/lcm/` |
| LCM action stuck in `running` | Action workflow has no error transition on external task | Apply Gap E fix path or invoke `/itential-lcm` |

Reference: `vendor/builder-skills/helpers/assets/lcm/lcm-vxlan-fabric-services-project.json` — canonical example of correct `instance` variable pattern across 6 action workflows.

### Step 10c — Invoke `/itential-lcm`

```
/itential-lcm
```

Provide: the resource model export, the action workflow export, the specific error or symptom, and the instance schema. The skill will diagnose wiring issues and generate corrected definitions.
