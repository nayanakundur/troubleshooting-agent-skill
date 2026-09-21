---
name: contribute
description: Contribution sub-skill for the troubleshooting-agent repo. Reads closed investigation files, generates properly formatted resolution entries, version notes, and skill improvements, then creates a GitHub PR on behalf of the support engineer. Automates the full git branch → write → diff → approve → commit → push → PR flow.
argument-hint: "[ISD-XXXX | ENG-XXXX | version-note | skill-fix <skill-name> | known-bug ENG-XXXX | scan]"
---

# /contribute — PR-Based Knowledge Contribution

Automates the knowledge-loop contribution flow end-to-end. Takes content from closed investigation artifacts, formats it to the project standard, presents it for engineer approval, and opens a GitHub PR — without the engineer needing to touch git.

---

## Invocation Forms

| Command | What it does |
|---|---|
| `/contribute ISD-XXXX` | Contribute the resolution from a specific closed ISD ticket |
| `/contribute ENG-XXXX` | Contribute a known-bug entry from an ENG bug ticket |
| `/contribute version-note` | Contribute a product behavioral fact (§7 of product-capability-reference.md) |
| `/contribute skill-fix {skill-name} "description"` | Contribute an improvement to a specific sub-skill |
| `/contribute scan` | Scan all closed investigations and offer a batch contribution PR |
| `/contribute` (no args) | Interactive — presents a menu of available contribution types |

---

## CRITICAL SAFETY RULES

- **Never stage investigation data** — `data/{timestamp}/` folders contain customer PII and ticket content; they are gitignored. Stage only shared knowledge files.
- **Never stage `.env`, `.auth.json`, or any file with credentials**
- **Always show the full diff before committing** — engineer must explicitly approve
- **Always pull latest main before branching** — prevents merge conflicts
- **Branch from main only** — never branch from another contributor's branch
- **One PR per contribution session** — batch multiple ticket contributions into a single PR

---

## Phase 0: Preflight

Before touching any files, run the following checks:

```bash
# 1. Confirm git is clean (no unstaged changes that would be at risk)
git status --short

# 2. Get git user for branch naming
GIT_USER=$(git config user.name | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
echo "Contributing as: $GIT_USER"

# 3. Confirm gh CLI is available
gh auth status 2>/dev/null && echo "✅ gh CLI authenticated" || echo "⚠️ gh CLI not logged in — PR creation will need manual step"

# 4. Check current branch
git branch --show-current
```

If there are uncommitted changes to non-gitignored files (not investigation data), warn the engineer:
```
⚠️ You have uncommitted changes in the working tree.
   These will NOT be included in the contribution PR.
   Files detected: {list}
   Continue? [yes / no]
```

If the answer is no, stop here. If yes, stash the working-tree changes and proceed.

---

## Phase 1: Detect Contribution Type & Source

### If argument is `ISD-XXXX` or `ENG-XXXX`

Find the investigation folder for the ticket:

```bash
python3 -c "
import os, glob

ticket = '{TICKET_KEY}'
matches = sorted(
    glob.glob(f'data/*/{ticket}'),
    reverse=True   # newest first
)
if not matches:
    print(f'No investigation folder found for {ticket}')
    print('Available tickets:')
    for p in sorted(glob.glob('data/*/*')):
        print(f'  {p}')
else:
    for m in matches:
        files = os.listdir(m)
        print(f'Found: {m}')
        print(f'  Files: {files}')
"
```

Read the investigation artifacts in order of priority:
1. `diagnostic_report.md` — confirmed root cause + fix (if Phase 4 was completed)
2. `pre-investigation-summary.md` — initial hypothesis (if Phase 4 not yet done)
3. `ticket_context.md` — environment, component, symptom, versions

If `diagnostic_report.md` is absent or Phase 4 was not completed, warn:
```
⚠️ No diagnostic_report.md found for {TICKET_KEY}.
   Contributing from pre-investigation-summary.md instead.
   Root cause may be a hypothesis, not a confirmed fix.
   Mark contribution as "preliminary" until confirmed? [yes / no]
```

### If argument is `scan`

```bash
python3 -c "
import os, glob, json

tickets = []
for ctx_path in sorted(glob.glob('data/*/*/ticket_context.md'), reverse=True):
    folder = os.path.dirname(ctx_path)
    ticket = os.path.basename(folder)
    ts = os.path.basename(os.path.dirname(folder))
    has_report = os.path.exists(os.path.join(folder, 'diagnostic_report.md'))
    tickets.append({'ticket': ticket, 'ts': ts, 'has_report': has_report, 'folder': folder})

print(f'Found {len(tickets)} investigation folder(s):')
for t in tickets:
    flag = '✅ has report' if t['has_report'] else '⚠️  no report'
    print(f'  {t[\"ticket\"]}  ({t[\"ts\"]})  {flag}')
"
```

Present the list and offer:
```
Select tickets to contribute (space-separated ticket keys, or 'all' to include all with reports):
```

### If argument is `version-note`

Prompt the engineer to provide:
```
Version note details:
  IAP version range (e.g. "6.4.x" or "< 6.0"): 
  Component (e.g. Platform, IAG, Adapter, MongoDB):
  What APPLIES in this version:
  What does NOT APPLY / exist:
  Diagnostic impact (why this matters for troubleshooting):
  Source (ISD ticket or direct verification):
```

### If argument is `skill-fix {skill-name} "description"`

Identify the skill file:
```bash
ls .claude/skills/{SKILL_NAME}/SKILL.md
```

Prompt the engineer for the specific change needed. Present the current relevant section, then draft the fix for approval before writing.

### If argument is `known-bug ENG-XXXX`

Fetch the ENG ticket via Atlassian MCP:
```
mcp__claude_ai_Atlassian_MCP__getJiraIssue(issueKey: "{ENG_TICKET_KEY}")
```
Extract: summary, description, affectedVersions, fixVersions, components, status, priority.

---

## Phase 2: Generate Contribution Content

### 2a — Resolution Entry (for ISD tickets)

**Before generating the entry, resolve the ENG ticket key.** Do not leave this as a placeholder — run the three checks below in order and use the first key found:

**Check 1 — Jira issue links on the ISD ticket:**
```bash
curl -s "${JIRA_URL}/rest/api/3/issue/${ISD_TICKET_KEY}?fields=issuelinks" \
  -u "${JIRA_USER}:${JIRA_API_TOKEN}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for link in d.get('fields', {}).get('issuelinks', []):
    for side in ('inwardIssue', 'outwardIssue'):
        key = link.get(side, {}).get('key', '')
        if key.startswith('ENG-'):
            print(key)
"
```

**Check 2 — Remote links (external Jira links):**
```bash
curl -s "${JIRA_URL}/rest/api/3/issue/${ISD_TICKET_KEY}/remotelink" \
  -u "${JIRA_USER}:${JIRA_API_TOKEN}" | python3 -c "
import sys, json
for link in json.load(sys.stdin):
    title = link.get('object', {}).get('title', '')
    if 'ENG-' in title:
        import re; m = re.search(r'ENG-\d+', title)
        if m: print(m.group())
"
```

**Check 3 — Investigation artifacts (`diagnostic_report.md`, `eng_ticket_draft.md`):**
```bash
grep -oE 'ENG-[0-9]+' data/*/*/${ISD_TICKET_KEY}/diagnostic_report.md \
  data/*/*/${ISD_TICKET_KEY}/eng_ticket_draft.md 2>/dev/null | sort -u | head -1
```

Set `ENG_TICKET_KEY` to the first result found across all three checks, or `N/A` if none.

Read `diagnostic_report.md` and `ticket_context.md` and generate a resolution entry in the standard format:

```markdown
---

### [{TICKET_KEY}] {one-line summary from ticket}

| Field | Value |
|-------|-------|
| **Ticket** | {TICKET_KEY} |
| **ENG Bug** | {ENG_TICKET_KEY — resolved above, never left as placeholder} |
| **Component** | {component — e.g. "Platform core — Shutdown.js"} |
| **Platform Version** | {IAP version} (confirmed) |
| **Severity** | {S1-S4} — {brief impact description} |

**Symptom:**
{Exact error string or observable behavior. What the customer saw — their words where possible.
Enough detail for pattern-matching against future tickets.}

**Root Cause:**
{Precise technical explanation. What failed, why it failed, which file/service/config caused it.
Confirmed by: {how it was confirmed — log line, API response, comparison, tcpdump, etc.}}

**Detection Hints:**
- {Specific log message, metric, or API response that points to this root cause}
- {Second detection hint}
- {Any "negative" hint — what you'd expect to see if this weren't the cause}

**Workaround (immediate):**
{Actionable fix the customer can apply now. Include exact commands if applicable.
If no workaround, state: "None — wait for ENG-XXXX fix in {fix version}."}

**Verification:**
1. {Step to confirm the workaround was applied}
2. {Step to confirm the symptom is resolved}
```

**Privacy rules for resolution entries:**
- No customer hostnames, IP addresses, or environment-specific URLs
- No credentials, tokens, or internal-only identifiers
- Symptom description uses generic terms, not customer-specific configuration values
- If in doubt about a detail being customer-specific, omit it and use a placeholder

### 2b — Duplicate check before appending

```bash
python3 -c "
with open('data/known-resolutions.md') as f:
    content = f.read()
ticket = '{TICKET_KEY}'
if f'### [{ticket}]' in content or f'**Ticket** | {ticket}' in content:
    print(f'⚠️  {ticket} already has an entry in known-resolutions.md')
    print('Skipping to avoid duplicate. Update the existing entry instead.')
else:
    print(f'✅ {ticket} not yet in known-resolutions.md — safe to append')
"
```

### 2c — Version Note (§7 row)

Format as a markdown table row for `product-capability-reference.md §7`:
```
| {version range} | {component} | {what applies} | {what does NOT apply / exist} | {diagnostic impact — cite {TICKET_KEY} or "direct verification"} |
```

### 2d — Known Bug Entry (§5)

Format for `product-capability-reference.md §5`:
```
| {ENG-XXXX} | {summary, one line} | {affected versions} | {fix version or "Open"} | {workaround, one line, or "None"} |
```

Check §5 first to avoid duplicates:
```bash
python3 -c "
with open('data/product-capability-reference.md') as f:
    content = f.read()
eng = '{ENG_TICKET_KEY}'
if eng in content:
    print(f'⚠️  {eng} already listed in §5 — update existing row instead')
else:
    print(f'✅ {eng} not yet in §5 — safe to append')
"
```

---

## Phase 3: Present for Engineer Approval

Before writing anything to disk, show the complete proposed content to the engineer:

```
══════════════════════════════════════════════════════════════
  PROPOSED CONTRIBUTION — {TICKET_KEY}
══════════════════════════════════════════════════════════════

  Target file: data/known-resolutions.md
  Operation:   Append new resolution entry

  ─── PREVIEW ──────────────────────────────────────────────
  {full formatted content from Phase 2}
  ──────────────────────────────────────────────────────────

  ⚠️  Review for: customer-specific details, credentials,
      hostnames, or unconfirmed root causes before approving.

  Approve and write? [yes / edit / skip / abort]
══════════════════════════════════════════════════════════════
```

- **yes** — write to file and proceed
- **edit** — engineer edits the content inline, then re-presents for final approval
- **skip** — skip this ticket, move to next in batch
- **abort** — stop the entire contribution session; clean up any staged changes

---

## Phase 4: Branch & Write

```bash
# 4a — Pull latest main
git checkout main
git pull origin main

# 4b — Create contribution branch
BRANCH="contrib/${GIT_USER}-$(date +%Y-%m-%d)"
# If branch already exists (same-day second run), append a counter
git checkout -b "${BRANCH}" 2>/dev/null \
  || git checkout -b "${BRANCH}-2" 2>/dev/null \
  || git checkout "${BRANCH}"

echo "Working on branch: $(git branch --show-current)"
```

Write approved content to the target file(s). For `known-resolutions.md`, append after the last `---` separator. For `product-capability-reference.md`, insert into the correct section (§5 or §7 table).

```python
# Append to known-resolutions.md
with open('data/known-resolutions.md', 'a') as f:
    f.write('\n' + approved_content + '\n')
print('✅ Appended to data/known-resolutions.md')
```

---

## Phase 5: Stage, Diff & Final Approval

```bash
# Stage ONLY the shared knowledge files
git add data/known-resolutions.md
git add data/product-capability-reference.md
# For skill-fix contributions:
# git add .claude/skills/{SKILL_NAME}/SKILL.md

# Verify nothing else was staged
git status --short

# Show the exact diff that will be committed
git diff --staged
```

Present the diff to the engineer:
```
══════════════════════════════════════════════════════════════
  GIT DIFF — what will be committed
══════════════════════════════════════════════════════════════
  {git diff --staged output}
══════════════════════════════════════════════════════════════
  Confirm commit and push? [yes / abort]
```

If the diff shows anything other than `data/known-resolutions.md`, `data/product-capability-reference.md`, or an approved `SKILL.md` file → **abort immediately** and warn the engineer.

---

## Phase 6: Commit, Push & Create PR

### 6a — Commit

Build a structured commit message listing every ticket contributed:

```bash
# Build message
TICKETS="{comma-separated ticket keys}"
RESOLUTION_COUNT={N}
VERSION_NOTE_COUNT={M}

MSG="contrib: ${TICKETS}"
[ ${RESOLUTION_COUNT} -gt 0 ] && MSG="${MSG} — ${RESOLUTION_COUNT} resolution(s)"
[ ${VERSION_NOTE_COUNT} -gt 0 ] && MSG="${MSG}, ${VERSION_NOTE_COUNT} product fact(s)"

git commit -m "$(cat <<EOF
${MSG}

$(git diff HEAD~1 --stat 2>/dev/null || echo '')

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

### 6b — Push

```bash
git push -u origin "$(git branch --show-current)"
```

### 6c — Create PR via gh CLI

```bash
TODAY=$(date +%Y-%m-%d)
TITLE="[Contrib] ${TODAY} — {summary of what's in the PR}"

gh pr create \
  --title "${TITLE}" \
  --body "$(cat <<'PREOF'
## What's in this PR

{For each ticket contributed:}
- **{TICKET_KEY}** — {one-line symptom}: {one-line fix}

{For each version note:}
- §7 note: [{IAP version}] {component}: {behavioral fact}

{For each known bug:}
- §5: {ENG-XXXX} — {one-line summary}

## Source

Contributed via `/contribute` skill on {TODAY}.
Investigations closed: {list of ticket keys}

## Review checklist

- [ ] Resolution rows have confirmed root causes (not hypotheses)
- [ ] No customer-specific hostnames, credentials, or environment details
- [ ] §7 facts cite a ticket number or direct verification
- [ ] Symptom keywords are generic enough to match future tickets
PREOF
  )" \
  --base main \
  --head "$(git branch --show-current)"
```

Print the PR URL when complete:
```
✅ PR created: {PR_URL}
   Branch: {branch-name}
   Files changed: {list}
   
   Next steps:
   1. Share the PR URL with a peer reviewer
   2. After merge, everyone pulls main before next investigation
   3. Locally: git checkout main && git pull origin main
```

---

## Phase 7: Cleanup

```bash
# Switch back to main (leave the contrib branch in place until PR is merged)
git checkout main
echo "✅ Back on main. Contribution branch '${BRANCH}' is open for review."
```

If changes were stashed in Phase 0:
```bash
git stash pop
echo "✅ Restored your prior working-tree changes."
```

---

## Error Handling

| Scenario | Action |
|---|---|
| Investigation folder not found for ticket | List available tickets; prompt to pick one or skip |
| `diagnostic_report.md` missing | Fall back to `pre-investigation-summary.md`; mark as preliminary |
| Ticket already in `known-resolutions.md` | Skip silently with a note; offer to update the existing entry instead |
| `gh` not authenticated | Commit and push proceed; print manual `gh pr create` command for engineer to run |
| Push rejected (branch already exists remotely) | Switch to the existing branch and continue appending |
| Merge conflict on pull | Stop and instruct engineer to resolve manually: `git merge main` → resolve → re-run `/contribute` |
| Staged files include unexpected paths | Abort, `git reset HEAD`, warn engineer, do not commit |
