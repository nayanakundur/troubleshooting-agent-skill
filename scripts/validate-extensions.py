#!/usr/bin/env python3
"""
validate-extensions.py

Post-sync validator: compares LOCAL-EXTENSIONS.md override/insert labels
against the new vendor SKILL.md to detect:
  - CRITICAL: override or insert-anchor targets that changed or disappeared
  - NEW:      steps added to the vendor skill not covered by any extension
  - MODIFIED: steps whose content changed (override target may be stale)
  - STALE:    extension sections referencing labels that no longer exist

Usage:
  python3 scripts/validate-extensions.py [--skill <name>] [--json] [--save]

  --skill <name>   validate only this skill (default: all skills with LOCAL-EXTENSIONS.md)
  --json           emit machine-readable JSON (for downstream agentic use)
  --save           write the report to .claude/skills/<name>/sync-validation-latest.md

Exit codes:
  0 = clean (no issues)
  1 = warnings (NEW or MODIFIED steps — review recommended)
  2 = critical (CRITICAL or STALE — extension may break)
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── Paths ─────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"


# ── Parsing helpers ───────────────────────────────────────────────────────────

# Vendor SKILL.md step headings: "## Step 3 —", "### Step 3a —", "## Phase 1:", etc.
VENDOR_STEP_RE = re.compile(
    r"^(?:#{2,4})\s+"
    r"((?:Step\s+[\w.]+|Phase\s+[\w.]+|Pre-flight\s+\w.*?|Architecture\s+\w.*?|Extended\s+\w.*?|Cost\s+\&\s+Scope.*?))"
    r"(?:\s*[—–-]|\s*:|\s*$)",
    re.IGNORECASE,
)

# LOCAL-EXTENSIONS.md label headings:
# "## [OVERRIDE] Step 3 — ..."
# "## [INSERT BEFORE Step 1] Step 0 — ..."
# "## [INSERT AFTER Step 1a] Step 1b — ..."
EXT_LABEL_RE = re.compile(
    r"^#{1,3}\s+\[(?P<action>OVERRIDE|INSERT BEFORE|INSERT AFTER)\s*(?P<anchor>[^\]]*)\]",
    re.IGNORECASE,
)


def extract_vendor_steps(text: str) -> dict[str, dict]:
    """Return {normalized_label: {line, content_hash, raw_label}} from SKILL.md text."""
    steps = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = VENDOR_STEP_RE.match(lines[i])
        if m:
            raw_label = m.group(1).strip()
            norm = normalize_label(raw_label)
            # Collect content until next same-or-higher heading
            heading_level = len(lines[i]) - len(lines[i].lstrip("#"))
            body_lines = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if nxt.startswith("#"):
                    nxt_level = len(nxt) - len(nxt.lstrip("#"))
                    if nxt_level <= heading_level:
                        break
                body_lines.append(nxt)
                j += 1
            body = "\n".join(body_lines).strip()
            import hashlib
            content_hash = hashlib.sha1(body.encode()).hexdigest()[:12]
            steps[norm] = {
                "line": i + 1,
                "raw_label": raw_label,
                "content_hash": content_hash,
                "body_preview": body[:200],
            }
        i += 1
    return steps


def extract_extension_labels(text: str) -> list[dict]:
    """Return list of {action, anchor_norm, anchor_raw, line, section_title} from LOCAL-EXTENSIONS.md."""
    labels = []
    for i, line in enumerate(text.splitlines(), 1):
        m = EXT_LABEL_RE.match(line)
        if m:
            action = m.group("action").strip().upper()
            anchor_raw = m.group("anchor").strip()
            # For INSERT BEFORE/AFTER the anchor is something like "Step 1"
            # For OVERRIDE the anchor is the step label itself (rest of heading)
            # Normalize it for matching
            anchor_norm = normalize_label(anchor_raw)
            labels.append({
                "action": action,
                "anchor_raw": anchor_raw,
                "anchor_norm": anchor_norm,
                "line": i,
                "raw_heading": line.strip(),
            })
    return labels


def normalize_label(s: str) -> str:
    """Lowercase, strip punctuation variants, normalize whitespace for fuzzy matching."""
    s = s.lower().strip()
    # Remove trailing dash/em-dash/colon artifacts
    s = re.sub(r"[\s\-–—:]+$", "", s)
    # Collapse internal whitespace
    s = re.sub(r"\s+", " ", s)
    # Remove "section" word for prefix matching
    return s


def fuzzy_match(anchor: str, vendor_steps: dict[str, dict]) -> str | None:
    """Return the best matching vendor step key for anchor, or None."""
    # Exact match
    if anchor in vendor_steps:
        return anchor
    # Prefix: anchor is a prefix of a vendor step label
    for key in vendor_steps:
        if key.startswith(anchor) or anchor.startswith(key):
            return key
    # Substring
    for key in vendor_steps:
        if anchor in key or key in anchor:
            return key
    return None


def get_git_old_content(rel_path: str) -> str | None:
    """Get the git HEAD version of a file (before this sync's changes were staged)."""
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{rel_path}"],
            capture_output=True, text=True, cwd=REPO_ROOT
        )
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass
    return None


# ── Core validation ───────────────────────────────────────────────────────────

def validate_skill(skill_name: str) -> dict:
    """Validate LOCAL-EXTENSIONS.md against the current (post-sync) vendor SKILL.md."""
    skill_dir = SKILLS_DIR / skill_name
    local_ext_path = skill_dir / "LOCAL-EXTENSIONS.md"
    skill_md_path = skill_dir / "SKILL.md"

    result = {
        "skill": skill_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "critical": [],
        "new_steps": [],
        "modified_steps": [],
        "stale_extensions": [],
        "clean": False,
    }

    if not local_ext_path.exists():
        result["error"] = "No LOCAL-EXTENSIONS.md — nothing to validate"
        result["clean"] = True
        return result

    if not skill_md_path.exists():
        result["error"] = f"No SKILL.md found at {skill_md_path}"
        return result

    new_skill_text = skill_md_path.read_text()
    new_steps = extract_vendor_steps(new_skill_text)

    # Try to get old SKILL.md from git HEAD
    rel_skill = str(skill_md_path.relative_to(REPO_ROOT))
    old_skill_text = get_git_old_content(rel_skill)
    old_steps = extract_vendor_steps(old_skill_text) if old_skill_text else {}

    ext_text = local_ext_path.read_text()
    ext_labels = extract_extension_labels(ext_text)

    # ── Check 1: Do extension anchor targets still exist in new SKILL.md? ────
    for label in ext_labels:
        anchor = label["anchor_norm"]
        if not anchor:
            continue
        matched = fuzzy_match(anchor, new_steps)
        if matched is None:
            result["critical"].append({
                "type": "ANCHOR_MISSING",
                "extension_line": label["line"],
                "extension_heading": label["raw_heading"],
                "anchor": label["anchor_raw"],
                "message": f"Extension label '{label['raw_heading']}' targets '{label['anchor_raw']}' which no longer exists in vendor SKILL.md.",
                "suggestion": f"Check if the step was renamed or removed. Update the [OVERRIDE]/[INSERT] label or remove the extension section.",
            })
        elif label["action"] == "OVERRIDE" and matched in old_steps:
            # Check if the overridden step's content changed between old and new
            old_hash = old_steps[matched]["content_hash"]
            new_hash = new_steps[matched]["content_hash"]
            if old_hash != new_hash:
                result["modified_steps"].append({
                    "type": "OVERRIDE_TARGET_CHANGED",
                    "extension_line": label["line"],
                    "extension_heading": label["raw_heading"],
                    "vendor_step": new_steps[matched]["raw_label"],
                    "vendor_line": new_steps[matched]["line"],
                    "old_hash": old_hash,
                    "new_hash": new_hash,
                    "new_content_preview": new_steps[matched]["body_preview"],
                    "message": f"Vendor step '{new_steps[matched]['raw_label']}' changed content (hash {old_hash} → {new_hash}). Your [OVERRIDE] may need updating.",
                    "suggestion": "Review the new vendor step content vs your override. Update the override if the vendor added useful changes.",
                })

    # ── Check 2: New steps in vendor SKILL.md not covered by any extension ───
    ext_anchors = {label["anchor_norm"] for label in ext_labels if label["anchor_norm"]}
    for norm, step in new_steps.items():
        if norm not in old_steps:  # genuinely new step
            covered = any(
                fuzzy_match(anc, {norm: step}) is not None
                for anc in ext_anchors
            )
            if not covered:
                result["new_steps"].append({
                    "type": "NEW_VENDOR_STEP",
                    "vendor_step": step["raw_label"],
                    "vendor_line": step["line"],
                    "content_preview": step["body_preview"],
                    "message": f"New vendor step '{step['raw_label']}' (line {step['line']}) has no LOCAL-EXTENSIONS.md coverage.",
                    "suggestion": "Review this step — does it conflict with or duplicate any existing extension? Add [INSERT AFTER] or [OVERRIDE] if needed.",
                })

    # ── Check 3: Steps whose content changed (not overridden) ────────────────
    if old_steps:
        for norm, step in new_steps.items():
            if norm in old_steps:
                old_hash = old_steps[norm]["content_hash"]
                new_hash = step["content_hash"]
                if old_hash == new_hash:
                    continue
                # Is it overridden? If yes, already caught above
                is_overridden = any(
                    label["action"] == "OVERRIDE" and fuzzy_match(label["anchor_norm"], {norm: step})
                    for label in ext_labels
                )
                if not is_overridden:
                    result["modified_steps"].append({
                        "type": "VENDOR_STEP_MODIFIED",
                        "vendor_step": step["raw_label"],
                        "vendor_line": step["line"],
                        "old_hash": old_hash,
                        "new_hash": new_hash,
                        "new_content_preview": step["body_preview"],
                        "message": f"Vendor step '{step['raw_label']}' changed content. No LOCAL-EXTENSIONS.md override targets it — new behavior will apply automatically.",
                        "suggestion": "Review the new content. If the change conflicts with an adjacent extension or insert-after step, add a new [OVERRIDE] section.",
                    })

    total_issues = len(result["critical"]) + len(result["new_steps"]) + len(result["modified_steps"]) + len(result["stale_extensions"])
    result["clean"] = total_issues == 0
    result["total_issues"] = total_issues
    return result


# ── Report formatting ─────────────────────────────────────────────────────────

def format_report(results: list[dict]) -> str:
    lines = [
        "# Vendor Skill Extension Validation Report",
        f"",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"Skills checked: {len(results)}",
        "",
    ]

    any_issues = False
    for r in results:
        skill = r["skill"]
        if r.get("error"):
            lines.append(f"## {skill}")
            lines.append(f"ℹ️  {r['error']}")
            lines.append("")
            continue

        total = r.get("total_issues", 0)
        status = "✅ CLEAN" if r["clean"] else f"⚠️  {total} ISSUE(S)"
        lines.append(f"## {skill} — {status}")
        lines.append("")

        if r["critical"]:
            any_issues = True
            lines.append("### 🔴 CRITICAL — Extension anchors missing from vendor SKILL.md")
            lines.append("These extension sections will silently have no effect (anchor not found).")
            lines.append("")
            for item in r["critical"]:
                lines.append(f"- **Extension line {item['extension_line']}:** `{item['extension_heading']}`")
                lines.append(f"  - Anchor `{item['anchor']}` not found in updated SKILL.md")
                lines.append(f"  - _{item['suggestion']}_")
                lines.append("")

        if r["modified_steps"]:
            any_issues = True
            lines.append("### 🟠 MODIFIED — Vendor steps with changed content")
            lines.append("")
            for item in r["modified_steps"]:
                tag = "OVERRIDE TARGET CHANGED" if item["type"] == "OVERRIDE_TARGET_CHANGED" else "VENDOR STEP MODIFIED"
                lines.append(f"- **[{tag}]** `{item['vendor_step']}` (vendor line {item['vendor_line']})")
                lines.append(f"  - Content hash: `{item['old_hash']}` → `{item['new_hash']}`")
                lines.append(f"  - _{item['suggestion']}_")
                if item.get("new_content_preview"):
                    preview = item["new_content_preview"][:150].replace("\n", " ")
                    lines.append(f"  - Preview: _{preview}…_")
                lines.append("")

        if r["new_steps"]:
            any_issues = True
            lines.append("### 🟡 NEW — Vendor steps added since last sync")
            lines.append("")
            for item in r["new_steps"]:
                lines.append(f"- **NEW STEP:** `{item['vendor_step']}` (vendor line {item['vendor_line']})")
                lines.append(f"  - _{item['suggestion']}_")
                if item.get("content_preview"):
                    preview = item["content_preview"][:150].replace("\n", " ")
                    lines.append(f"  - Preview: _{preview}…_")
                lines.append("")

        if r["clean"]:
            lines.append("No issues detected. LOCAL-EXTENSIONS.md is consistent with the updated vendor SKILL.md.")
            lines.append("")

    if not any_issues:
        lines.insert(4, "**All extensions are consistent with their vendor skills. ✅**")

    lines.append("---")
    lines.append("_Run `/sync-vendor-skills` in Claude Code to review and apply fixes interactively._")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Validate LOCAL-EXTENSIONS.md against synced vendor SKILL.md files")
    parser.add_argument("--skill", help="Validate only this skill name")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument("--save", action="store_true", help="Save report to .claude/skills/<name>/sync-validation-latest.md")
    args = parser.parse_args()

    # Collect skills to validate
    if args.skill:
        skills = [args.skill]
    else:
        skills = [
            d.name for d in SKILLS_DIR.iterdir()
            if d.is_dir() and (d / "LOCAL-EXTENSIONS.md").exists()
        ]

    if not skills:
        print("No skills with LOCAL-EXTENSIONS.md found.", file=sys.stderr)
        sys.exit(0)

    results = [validate_skill(s) for s in sorted(skills)]

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        report = format_report(results)
        print(report)

        if args.save:
            for r in results:
                skill_dir = SKILLS_DIR / r["skill"]
                out_path = skill_dir / "sync-validation-latest.md"
                report_single = format_report([r])
                out_path.write_text(report_single)
                print(f"\nReport saved: {out_path}", file=sys.stderr)

    # Determine exit code
    has_critical = any(r.get("critical") or r.get("stale_extensions") for r in results)
    has_warnings = any(r.get("new_steps") or r.get("modified_steps") for r in results)

    if has_critical:
        sys.exit(2)
    if has_warnings:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
