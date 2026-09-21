---
name: sync-vendor-skills
description: Post-sync review and repair of LOCAL-EXTENSIONS.md files after a vendor skill sync. Reads the validation report produced by validate-extensions.py, diffs old vs new vendor SKILL.md content, drafts specific LOCAL-EXTENSIONS.md additions or corrections, and applies them with engineer approval.
argument-hint: "[<skill-name> | --all | --report-only]"
---

# /sync-vendor-skills — Vendor Skill Extension Review & Repair

Invoked after `scripts/sync-platform-skills.sh` when the validator detects issues.
Reads the structured validation report, presents each change to the engineer, drafts
LOCAL-EXTENSIONS.md fixes, and applies them on approval — so extensions stay aligned
with the updated vendor skill without rewriting them from scratch.

---

## CRITICAL SAFETY RULES

- **Never modify files listed in `vendor/platform-skills/SYNC_MANIFEST.json`** — those are vendor-owned
- **Only modify `LOCAL-EXTENSIONS.md` files** — never edit the vendor `SKILL.md` directly
- **Show the full proposed diff before writing** — engineer must approve each change
- **Do not remove existing extension sections** without explicit engineer confirmation — they may contain site-specific overrides that are still correct
- **Preserve all `[OVERRIDE]` / `[INSERT AFTER]` / `[INSERT BEFORE]` label syntax exactly** — Claude reads these labels to merge skills at runtime

---

## Phase 0 — Load Validation Report

### Step 0a — Determine which skill(s) to review

If invoked with a skill name (e.g. `/sync-vendor-skills themis-aws-deploy`):
- Focus on that skill only

If invoked with `--all` or no argument:
- Find all `sync-validation-latest.md` files under `.claude/skills/*/`
- List skills with issues; ask engineer which to handle first

If invoked with `--report-only`:
- Run the validator and print the report; stop here (no fixes applied)

```bash
# Regenerate the report fresh (picks up git state from the sync)
python3 scripts/validate-extensions.py --save 2>/dev/null
echo "---"
cat .claude/skills/${SKILL_NAME}/sync-validation-latest.md 2>/dev/null \
  || echo "No validation report found — run scripts/sync-platform-skills.sh first"
```

### Step 0b — Load the two versions of vendor SKILL.md

```bash
SKILL_NAME="${SKILL_NAME}"  # set from argument or selection

# Current (post-sync) version
NEW_SKILL=".claude/skills/${SKILL_NAME}/SKILL.md"

# Previous (pre-sync) version from git
OLD_SKILL_CONTENT=$(git show HEAD:.claude/skills/${SKILL_NAME}/SKILL.md 2>/dev/null || echo "")

# LOCAL-EXTENSIONS.md
EXT_FILE=".claude/skills/${SKILL_NAME}/LOCAL-EXTENSIONS.md"
```

Read all three files into context. They are the working data for this session.

Print a summary:
```
╔══════════════════════════════════════════════════════════════╗
║  Vendor Skill Sync Review: {SKILL_NAME}                      ║
╠══════════════════════════════════════════════════════════════╣
║  Old vendor SHA:  {git show HEAD:.../SKILL.md | head digest} ║
║  New vendor file: {SKILL_NAME}/SKILL.md (post-sync)          ║
║  Extensions file: LOCAL-EXTENSIONS.md                        ║
╠══════════════════════════════════════════════════════════════╣
║  Issues detected:                                            ║
║    🔴 CRITICAL (broken anchors):  {N}                        ║
║    🟠 MODIFIED (overrides stale): {N}                        ║
║    🟡 NEW steps (uncovered):       {N}                       ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Phase 1 — Handle Each Issue Type

Work through issues in severity order: CRITICAL → MODIFIED → NEW.
For each issue, present it, draft a fix, show the diff, and await approval.

### 1A — CRITICAL: Broken Anchors

**What happened:** the extension references a step (`[OVERRIDE] Step X` or `[INSERT AFTER Step X]`)
that no longer exists in the updated vendor SKILL.md. This extension section is now inert —
Claude cannot find the anchor to apply it.

**For each broken anchor:**

1. Show the extension section heading and its current content.
2. Scan the new vendor SKILL.md for the closest renamed/restructured step:
   - Look for similar wording
   - Look for a step at the same relative position in the skill flow
   - Look for a step covering the same domain (pre-flight, tofu commands, certify, etc.)
3. If a likely match is found, propose updating the extension label to use the new step name:

```
══════════════════════════════════════════════════════════════
  CRITICAL FIX — Broken Anchor
══════════════════════════════════════════════════════════════

  Extension (LOCAL-EXTENSIONS.md line {N}):
    ## [OVERRIDE] {old anchor}

  Old vendor step:   "{old label}" (no longer exists)
  Suggested new anchor: "{new label}" (line {N} in new SKILL.md)

  Proposed change to LOCAL-EXTENSIONS.md:
  - ## [OVERRIDE] {old anchor}
  + ## [OVERRIDE] {new anchor}

  Preview of new vendor step content:
  {first 10 lines of new step}

  ⚠️  Check that your override content still makes sense against the
      new vendor step before approving.

  Apply? [yes / rename-only / remove / skip]
══════════════════════════════════════════════════════════════
```

- **yes** — update the label and keep extension content as-is
- **rename-only** — just update the label (same as yes for label-only issues)
- **remove** — delete the entire extension section (the override is no longer needed)
- **skip** — leave as-is (engineer will fix manually later)

4. If no match is found, flag for manual review:
```
  ❌ Cannot auto-match '{old anchor}' to any current vendor step.
     Show me the full LOCAL-EXTENSIONS.md section so we can decide together:
     [view section / remove section / skip]
```

### 1B — MODIFIED: Override Target Changed

**What happened:** the engineer has a `[OVERRIDE] Step X` section in LOCAL-EXTENSIONS.md,
but the vendor's Step X content changed between the old and new sync. The override still
*applies* (the anchor exists) but may now be stale — the engineer's override could be
missing new vendor improvements or may conflict with vendor changes.

**For each modified override:**

1. Show the diff between old and new vendor step content:

```bash
# In-memory diff: old vs new vendor step body
diff <(echo "${OLD_STEP_CONTENT}") <(echo "${NEW_STEP_CONTENT}") | head -60
```

2. Show the current LOCAL-EXTENSIONS.md override content.
3. Analyze the diff:
   - **Additive change** (vendor added a sub-step, flag, or check): consider whether to incorporate it into the override
   - **Rewrite** (vendor restructured the step): override may need a full update
   - **Minor tweak** (vendor fixed a typo or a URL): override likely fine as-is

4. Present a structured decision:

```
══════════════════════════════════════════════════════════════
  MODIFIED — Override Target Changed
══════════════════════════════════════════════════════════════

  Extension: ## [OVERRIDE] {step label}
  Vendor diff (old → new):
  {unified diff, truncated at 50 lines}

  Your override content:
  {first 20 lines of extension section}

  Analysis:
  {Claude's assessment: additive / rewrite / minor — and why}

  Options:
    [keep]        — override is still correct; vendor diff doesn't affect it
    [merge]       — incorporate useful vendor additions into your override
    [view-full]   — show complete diff before deciding
    [skip]        — defer to manual review

══════════════════════════════════════════════════════════════
```

If engineer chooses **merge**, draft the merged override content:
- Start from the engineer's current override (the authoritative base)
- Add vendor-only additions that don't conflict with local customizations
- Highlight any conflicts (e.g. vendor added a step that the local extension also handles)
- Show the full merged content for approval before writing

### 1C — NEW: Uncovered Vendor Steps

**What happened:** the vendor added a new step that has no LOCAL-EXTENSIONS.md coverage.
This is not necessarily a problem — new vendor steps run as-is unless the engineer wants
to override or augment them.

**For each new vendor step:**

1. Show the full new step content.
2. Ask:

```
══════════════════════════════════════════════════════════════
  NEW VENDOR STEP: {step label} (line {N})
══════════════════════════════════════════════════════════════

  {full step content, or first 30 lines if long}

  Does this new step need local customization?

    [skip]      — run vendor step as-is (no extension needed)
    [override]  — replace this step entirely; I'll draft the section
    [insert-before] — add a step before this one
    [insert-after]  — add a step after this one

══════════════════════════════════════════════════════════════
```

If engineer selects **override**, **insert-before**, or **insert-after**:

Ask what the new section should do. Draft the LOCAL-EXTENSIONS.md section:

```markdown
## [OVERRIDE] {step label}

{engineer-described behavior, drafted by Claude based on context from run-vars.yml,
.env patterns, and the existing LOCAL-EXTENSIONS.md style}
```

Show it for approval, then append to LOCAL-EXTENSIONS.md in the correct position
(overrides grouped by section, inserts in chronological step order).

---

## Phase 2 — Apply Approved Changes

After all issues have been reviewed and decisions recorded:

### Step 2a — Batch all approved edits

Build a single in-memory patch for LOCAL-EXTENSIONS.md:
- Label renames (CRITICAL fixes): find-and-replace the heading line only
- Content merges (MODIFIED fixes): replace the section body
- New sections (NEW step coverage): insert at the correct position in the file

Show the complete proposed diff for LOCAL-EXTENSIONS.md before writing:

```bash
diff <(cat "${EXT_FILE}") <(echo "${PROPOSED_CONTENT}") | head -120
```

```
══════════════════════════════════════════════════════════════
  PROPOSED LOCAL-EXTENSIONS.md CHANGES
  {N} changes: {N_critical} label fixes, {N_merged} merges, {N_new} new sections
══════════════════════════════════════════════════════════════
  {full unified diff}
══════════════════════════════════════════════════════════════
  Write these changes? [yes / review-each / abort]
```

### Step 2b — Write and verify

On approval:

```python
with open(EXT_FILE, 'w') as f:
    f.write(proposed_content)
print(f"✅ LOCAL-EXTENSIONS.md updated")
```

Re-run the validator to confirm no residual issues:

```bash
python3 scripts/validate-extensions.py --skill "${SKILL_NAME}"
```

If clean: `✅ Validation passed — LOCAL-EXTENSIONS.md is consistent with the updated vendor SKILL.md.`

If still issues: report the remaining items and offer to continue.

### Step 2c — Stage and summarize

```bash
git add ".claude/skills/${SKILL_NAME}/LOCAL-EXTENSIONS.md"
git status --short
git diff --staged
```

Present the diff. Do NOT commit — leave that to the engineer along with the vendor SKILL.md changes:

```
══════════════════════════════════════════════════════════════
  Review complete for: {SKILL_NAME}

  Changes staged (not yet committed):
    M  .claude/skills/{SKILL_NAME}/LOCAL-EXTENSIONS.md

  Recommended commit message:
    fix: update LOCAL-EXTENSIONS.md for {SKILL_NAME} post-sync vendor changes

    - {summary of what changed: N label fixes, N merges, N new sections}
    - Vendor SHA: {UPSTREAM_SHA[:12]} ({BRANCH})

  Run 'git commit' when ready, together with the vendor SKILL.md changes.
══════════════════════════════════════════════════════════════
```

---

## Quick Reference

| Issue type | Risk | Default action |
|---|---|---|
| CRITICAL — anchor missing | Extension has no effect | Find renamed step, update label |
| MODIFIED — override stale | Override may miss vendor improvements | Diff and merge if additive |
| NEW — uncovered step | None (vendor step runs as-is) | Review; override only if needed |

| Command | Effect |
|---|---|
| `/sync-vendor-skills` | Review all skills with validation reports |
| `/sync-vendor-skills themis-aws-deploy` | Review only this skill |
| `/sync-vendor-skills --report-only` | Print report, no edits |
| `python3 scripts/validate-extensions.py` | Regenerate report for all skills |
| `python3 scripts/validate-extensions.py --skill themis-aws-deploy --save` | Report for one skill, save to file |
