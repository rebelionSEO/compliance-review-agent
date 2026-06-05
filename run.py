#!/usr/bin/env python3
"""
Century Legal Group — Compliance System
Single entry point. Reads file, detects type, calls orchestrator.

Usage:
  python3 run.py <file>                                    # full pipeline
  python3 run.py <file> --revision-of <v1.json>           # revision check
  python3 run.py --resume <session.json>                   # resume interrupted run
  python3 run.py --qa <draft.json>                         # QA only

Examples:
  python3 run.py article.txt
  python3 run.py ads.csv
  python3 run.py article_v2.txt --revision-of "output/article_v1 – CCA.json"
  python3 run.py --resume output/sessions/article_20260605_143200.json
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

OUTPUT_DIR = Path(__file__).parent / "output"


# ── Content type detection ────────────────────────────────────────────────────

def detect_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".csv", ".xlsx"):
        return "paid_media"
    return "blog"


# ── I/O ───────────────────────────────────────────────────────────────────────

def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── Output helpers (used by orchestrator + qa) ────────────────────────────────

def save_draft(result: dict, filename: str):
    OUTPUT_DIR.mkdir(exist_ok=True)
    stem = Path(filename).stem
    date_str = datetime.now().strftime("%m.%d.%Y")
    json_path = OUTPUT_DIR / f"{stem} – Compliance Feedback – {date_str} – CCA.json"
    md_path   = OUTPUT_DIR / f"{stem} – Compliance Feedback – {date_str} – CCA.md"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    _write_feedback_md(result, stem, md_path)
    return md_path, json_path


def save_final(fixed_text: str, filename: str) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    stem = Path(filename).stem
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUTPUT_DIR / f"{stem}_compliant_{ts}.txt"
    out.write_text(fixed_text, encoding="utf-8")
    return out


def _write_feedback_md(result: dict, stem: str, path: Path):
    flags = result.get("flags", [])
    auto_count = sum(1 for f in flags if f.get("_auto_approve"))
    status = result.get("status", "")
    overall_risk = result.get("overall_risk", "")

    # Compliance ready determination
    if status == "APPROVED":
        compliance_ready = "✅ YES — No issues found. Ready for Ops submission."
    elif status == "APPROVED WITH REQUIRED EDITS":
        compliance_ready = "⚠️ NOT YET — Minor edits required before Ops submission."
    elif status == "ESCALATED FOR LEGAL REVIEW":
        compliance_ready = "🚨 NO — Requires attorney review before any further steps."
    elif status == "REJECTED":
        compliance_ready = "⛔ NO — Content is not permissible. Full rewrite required."
    else:
        compliance_ready = f"❌ NO — {len(flags)} flag(s) must be resolved before Ops submission."

    lines = [
        f"# **Compliance Feedback — {stem}**",
        "",
        f"## **COMPLIANCE READY: {compliance_ready}**",
        "",
        "---",
        "",
        f"**Date:** {result.get('_submission_date', datetime.now().strftime('%Y-%m-%d'))}  ",
        f"**Reviewer:** Century Compliance Agent (CCA)  ",
        f"**Status:** {status}  ",
        f"**Overall Risk:** {overall_risk}  ",
        f"**Flags:** {len(flags)} total  ({auto_count} auto-approvable · {len(flags)-auto_count} need review)",
        "",
        f"> {result.get('operations_summary', '')}",
        "",
    ]
    if result.get("link_review_summary"):
        lines += [f"**Links:** {result['link_review_summary']}", ""]
    if result.get("disclosure_review"):
        lines += [f"**Disclosure Review:** {result['disclosure_review']}", ""]
    lines += ["---", ""]

    # Group flags by risk level for easier scanning
    high    = [f for f in flags if f.get("risk_level") in ("HIGH", "CRITICAL")]
    moderate = [f for f in flags if f.get("risk_level") == "MODERATE"]
    low     = [f for f in flags if f.get("risk_level") == "LOW"]

    for group_label, group_flags in [
        (f"## 🔴 HIGH RISK FLAGS ({len(high)})", high),
        (f"## 🟡 MODERATE FLAGS ({len(moderate)})", moderate),
        (f"## 🟢 LOW FLAGS ({len(low)})", low),
    ]:
        if not group_flags:
            continue
        lines += [group_label, ""]
        for flag in group_flags:
            auto_tag = " *(auto-approvable)*" if flag.get("_auto_approve") else ""
            lines += [
                f"### **FLAG {flag['id']} — {flag['category']}**  `{flag.get('risk_level','')}`{auto_tag}",
                f"**Section:** {flag['section']} · ¶{flag['paragraph']}  ",
                f"**Rule:** {flag['rule_ref']}  ",
                f"**Action:** `{flag['action_required']}`",
                "",
                f"> \"{flag['quoted_text']}\"",
                "",
                f"**Issue:** {flag['violation']}  ",
                f"**Fix:** {flag['suggested_fix']}",
                "",
                "---", "",
            ]
    lines.append(f"*Draft — run `python3 tools/qa.py \"{path.parent / (path.stem + '.json')}\"` to validate.*")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_delta_md(delta, stem, path, v1_name):
    resolved = delta.get("resolved", [])
    still    = delta.get("still_present", [])
    new      = delta.get("new_issues", [])
    lines = [
        f"# Revision Check — {stem}",
        f"",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d')}  ",
        f"**Previous review:** {v1_name}  ",
        f"**Status:** {delta.get('overall_status', '')}",
        "",
        f"> {delta.get('summary', '')}",
        "",
        "---",
        "",
        f"## ✓ Resolved ({len(resolved)})", "",
    ]
    for r in resolved:
        lines.append(f"- **Flag {r['id']}** — \"{r.get('quoted_text','')}\"  {r.get('note','')}")
    lines += ["", f"## ⚠ Still Present ({len(still)})", ""]
    for s in still:
        lines.append(f"- **Flag {s['id']}** — \"{s.get('quoted_text','')}\"  {s.get('note','')}")
    lines += ["", f"## 🆕 New Issues ({len(new)})", ""]
    for n in new:
        lines += [
            f"### {n.get('category','')}  `{n.get('risk_level','')}`",
            f"> \"{n.get('quoted_text','')}\"",
            f"**Issue:** {n.get('violation','')}  **Fix:** {n.get('suggested_fix','')}",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        sys.exit(1)

    # QA only
    if args[0] == "--qa":
        from tools.qa import run_qa
        if len(args) < 2:
            print("Usage: python3 run.py --qa <draft.json>")
            sys.exit(1)
        run_qa(Path(args[1]))
        return

    # Resume
    if args[0] == "--resume":
        if len(args) < 2:
            print("Usage: python3 run.py --resume <session.json>")
            sys.exit(1)
        session_path = Path(args[1])
        state = json.loads(session_path.read_text())
        filename = state["filename"]
        content_type = state.get("content_type", "blog")
        # Find original content
        src = Path(filename)
        if not src.exists():
            print(f"Original file not found: {filename}")
            sys.exit(1)
        content = read_file(src)
        from tools.orchestrator import run
        run(content, filename, content_type, resume_from=session_path)
        return

    # Parse flags
    revision_of = None
    file_arg = None
    i = 0
    while i < len(args):
        if args[i] == "--revision-of" and i + 1 < len(args):
            revision_of = Path(args[i + 1]); i += 2
        else:
            file_arg = args[i]; i += 1

    if not file_arg:
        print("Please provide a file path.")
        sys.exit(1)

    p = Path(file_arg)
    if not p.exists():
        print(f"File not found: {p}")
        sys.exit(1)

    content = read_file(p)
    filename = p.name
    content_type = detect_type(p)

    from tools.orchestrator import run, run_delta

    if revision_of:
        run_delta(content, filename, revision_of, content_type)
    else:
        run(content, filename, content_type)


if __name__ == "__main__":
    main()
