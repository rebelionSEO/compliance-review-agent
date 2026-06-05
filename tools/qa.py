#!/usr/bin/env python3
"""
QA Validation Tool — human sign-off on agent flags before fixes are applied.

Auto-approves HIGH confidence flags (confirmed 5+ times, 80%+ approval rate).
Surfaces LOW confidence and new flags for human review.
Logs every decision to knowledge/memory/confidence.json.
Saves confirmed fix pairs to knowledge/memory/fix_examples.json.

Usage:
  python3 tools/qa.py <path_to_draft.json>
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.context import auto_approve, save_domain_decision, save_fix_example, update_confidence
from agents.fixer import fix_and_verify

OUTPUT_DIR = Path(__file__).parent.parent / "output"
FALSE_POSITIVES = Path(__file__).parent.parent / "knowledge" / "memory" / "false_positives.md"
_W = 65


def _line(char="─"):
    return char * _W


def _risk_icon(risk):
    return {"LOW": "○", "MODERATE": "⚠", "HIGH": "⚠ ", "CRITICAL": "🚨"}.get(risk, "⚠")


def print_flag(flag):
    risk = flag.get("risk_level", "")
    auto = " [AUTO-APPROVABLE]" if flag.get("_auto_approve") else ""
    print(f"\n{_line()}")
    print(f"{flag['section']}  (¶{flag['paragraph']})")
    print(f"{_risk_icon(risk)} FLAG {flag['id']} — {flag['category']}  [{flag['rule_ref']}]  {risk}{auto}")
    print(f'\n  "{flag["quoted_text"]}"')
    print(f"\n  Why   : {flag['violation']}")
    print(f"  Action: {flag['action_required']}")
    print(f"  Fix   : {flag['suggested_fix']}")


def log_false_positive(flag, reason, article):
    FALSE_POSITIVES.parent.mkdir(parents=True, exist_ok=True)
    existing = FALSE_POSITIVES.read_text(encoding="utf-8") if FALSE_POSITIVES.exists() else "# False Positives Log\n\n"
    entry = (
        f"## {datetime.now().strftime('%Y-%m-%d')} — {article}\n"
        f"- **Category**: {flag['category']}\n"
        f"- **Quoted**: \"{flag['quoted_text']}\"\n"
        f"- **Agent said**: {flag['violation']}\n"
        f"- **Rejected because**: {reason}\n\n"
    )
    FALSE_POSITIVES.write_text(existing + entry, encoding="utf-8")


def save_approved_md(result, approved, rejected, reviewer, source_json):
    stem = source_json.stem.replace(" – Compliance Feedback", "").rsplit(" – ", 1)[0]
    date_str = datetime.now().strftime("%m.%d.%Y")
    out = OUTPUT_DIR / f"{stem} – Compliance Feedback – {date_str} – CCA – {reviewer}.md"
    OUTPUT_DIR.mkdir(exist_ok=True)

    status = result.get("status", "RETURNED FOR REVISION")

    # Compliance ready determination
    if not approved:
        compliance_ready = "✅ YES — All flags resolved or rejected. Ready for Ops submission."
    elif status == "APPROVED":
        compliance_ready = "✅ YES — No issues found. Ready for Ops submission."
    elif status == "ESCALATED FOR LEGAL REVIEW":
        compliance_ready = "🚨 NO — Requires attorney review before any further steps."
    elif status == "REJECTED":
        compliance_ready = "⛔ NO — Content is not permissible. Full rewrite required."
    else:
        compliance_ready = f"❌ NO — {len(approved)} confirmed flag(s) must be fixed before Ops submission."

    lines = [
        f"# Compliance Feedback — {stem}",
        "",
        f"## COMPLIANCE READY: {compliance_ready}",
        "",
        "---",
        "",
        f"**Material:** {stem}  ",
        f"**QA Reviewer:** {reviewer} · {datetime.now().strftime('%Y-%m-%d')}  ",
        f"**Status:** {status}  ",
        f"**Flags confirmed:** {len(approved)}  |  **Rejected:** {len(rejected)}",
        "",
        f"> {result.get('operations_summary', '')}",
        "",
        "---",
        "",
        f"## Confirmed Flags ({len(approved)})",
        "",
    ]
    for f in approved:
        auto_tag = " *(auto-approved)*" if f.get("_auto_approve") else ""
        lines += [
            f"### FLAG {f['id']} — {f['category']}  `{f.get('risk_level','')}`{auto_tag}",
            f"> \"{f['quoted_text']}\"",
            f"",
            f"**Issue:** {f['violation']}  ",
            f"**Action:** {f['action_required']}  ",
            f"**Fix:** {f['suggested_fix']}",
            "",
        ]
    if rejected:
        lines += ["---", "", f"## Rejected Flags ({len(rejected)})", ""]
        for r in rejected:
            lines += [
                f"- FLAG {r['id']} ({r['category']}): {r.get('_rejection_reason', 'Rejected')}",
            ]
    lines += [
        "",
        "---",
        f"*QA: {reviewer} · {datetime.now().strftime('%Y-%m-%d')}*",
        "*Requires Operations human validation before Ops submission.*",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def run_qa(json_path: Path):
    data = json.loads(json_path.read_text(encoding="utf-8"))
    article = json_path.stem.split(" – ")[0] if " – " in json_path.stem else json_path.stem
    flags = data.get("flags", [])

    print(f"\n{'═' * _W}")
    print("QA VALIDATION")
    print(f"Article : {article}")
    print(f"Status  : {data.get('status')}  |  Risk: {data.get('overall_risk')}  |  Flags: {len(flags)}")
    print(f"{'═' * _W}")

    reviewer = input("\nYour initials: ").strip().upper()
    if not reviewer:
        print("Initials required.")
        sys.exit(1)

    # Separate auto-approvable from needs-review
    auto_flags = [f for f in flags if f.get("_auto_approve")]
    manual_flags = [f for f in flags if not f.get("_auto_approve")]

    confirmed = []
    rejected = []

    # Auto-approve high-confidence flags
    if auto_flags:
        print(f"\n✓ Auto-approving {len(auto_flags)} HIGH confidence flag(s):")
        for f in auto_flags:
            print(f"  FLAG {f['id']} — {f['category']}")
            update_confidence(f["category"], f["quoted_text"], "confirmed")
            confirmed.append(f)

    # Manual review for the rest
    if manual_flags:
        print(f"\n{len(manual_flags)} flag(s) need your review.")
        print("[c] Confirm  [r] Reject  [e] Edit fix  [s] Skip remaining\n")

        for flag in manual_flags:
            print_flag(flag)
            while True:
                choice = input("\n  Decision [c/r/e/s]: ").strip().lower()
                if choice == "c":
                    confirmed.append(flag)
                    update_confidence(flag["category"], flag["quoted_text"], "confirmed")
                    if flag.get("suggested_fix"):
                        save_fix_example(flag["category"], flag["quoted_text"], flag["suggested_fix"], article)
                    print("  ✓ Confirmed")
                    break
                elif choice == "r":
                    reason = input("  Reason: ").strip() or "Rejected by QA reviewer"
                    flag["_rejection_reason"] = reason
                    rejected.append(flag)
                    update_confidence(flag["category"], flag["quoted_text"], "rejected")
                    log_false_positive(flag, reason, article)
                    print("  ✗ Rejected — logged")
                    break
                elif choice == "e":
                    new_fix = input(f"  Current: {flag['suggested_fix']}\n  New fix: ").strip()
                    if new_fix:
                        flag["suggested_fix"] = new_fix
                    confirmed.append(flag)
                    update_confidence(flag["category"], flag["quoted_text"], "edited")
                    save_fix_example(flag["category"], flag["quoted_text"], flag.get("suggested_fix", ""), article)
                    print("  ✓ Confirmed with edit")
                    break
                elif choice == "s":
                    # Auto-confirm all remaining
                    confirmed.extend(manual_flags[manual_flags.index(flag):])
                    for remaining in manual_flags[manual_flags.index(flag):]:
                        update_confidence(remaining["category"], remaining["quoted_text"], "confirmed")
                    print("  Remaining auto-confirmed.")
                    break
                else:
                    print("  Please enter c, r, e, or s.")
            if choice == "s":
                break

    # Summary
    print(f"\n{'═' * _W}")
    print(f"Confirmed: {len(confirmed)}  |  Rejected: {len(rejected)}")

    # ── Link review ───────────────────────────────────────────────────────────
    link_review = data.get("link_review", {})
    unknown_links = link_review.get("unknown", [])
    if unknown_links:
        print(f"\n{_line()}")
        print(f"LINK REVIEW — {len(unknown_links)} external non-gov link(s) need a decision")
        print("[s] Safe — add to approved domains  [r] Reject — flag in all future runs  [k] Keep unknown (skip)")
        for lnk in unknown_links:
            domain = lnk["url"].split("/")[2]
            print(f"\n  {lnk['url']}")
            print(f"  Domain: {domain}")
            while True:
                choice = input("  Decision [s/r/k]: ").strip().lower()
                if choice == "s":
                    save_domain_decision(domain, "safe")
                    print(f"  ✓ {domain} added to safe domains")
                    break
                elif choice == "r":
                    save_domain_decision(domain, "rejected")
                    print(f"  ✗ {domain} added to rejected domains — will flag in all future reviews")
                    break
                elif choice == "k":
                    break
                else:
                    print("  Please enter s, r, or k.")

    # ── Save approved result ───────────────────────────────────────────────────
    data["flags"] = confirmed
    data["flag_count"] = len(confirmed)
    data["_qa_reviewer"] = reviewer
    data["_qa_date"] = datetime.now().strftime("%Y-%m-%d")
    data["_qa_rejected"] = rejected

    out = save_approved_md(data, confirmed, rejected, reviewer, json_path)
    print(f"\n✓ QA-approved feedback saved to:\n  {out}")

    approved_json = json_path.parent / json_path.name.replace("– CCA.json", f"– CCA – {reviewer}.json")
    approved_json.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ── Auto-trigger Fix + Verify ─────────────────────────────────────────────
    if confirmed:
        print(f"\n{_line()}")
        print(f"Apply {len(confirmed)} approved fix(es) now?")
        choice = input("[y] Yes — fix + verify  [n] No — I'll apply manually: ").strip().lower()
        if choice == "y":
            # Find original article content
            article_stem = json_path.stem.split(" – ")[0] if " – " in json_path.stem else json_path.stem
            # Try to find the source article
            txt_candidates = list((json_path.parent.parent).glob(f"**/{article_stem}*.txt")) + \
                             list((json_path.parent.parent).glob(f"{article_stem}*.txt"))
            if not txt_candidates:
                print("  Source article .txt not found — run: python3 run.py <article.txt> --apply <approved.json>")
            else:
                article_path = txt_candidates[0]
                article_text = article_path.read_text(encoding="utf-8")
                print(f"  Applying fixes to {article_path.name}…")
                result = fix_and_verify(article_text, confirmed)
                verification = result["verification"]
                resolved = sum(1 for v in verification.get("verified", []) if v.get("resolved"))
                regressions = [v for v in verification.get("verified", []) if v.get("new_issue_introduced")]
                print(f"\n  ✓ Fixed: {resolved}  |  Regressions: {len(regressions)}")
                print(f"  {verification.get('summary', '')}")
                # Save final
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                final_path = json_path.parent / f"{article_stem}_compliant_{ts}.txt"
                final_path.write_text(result["fixed_text"], encoding="utf-8")
                print(f"\n✓ Final article saved: {final_path.name}")
                if not regressions:
                    print("  Clean — ready for Ops submission.")
                else:
                    print("  ⚠ Review regressions before submitting.")
    else:
        print("\nNo fixes to apply.")

    return data, confirmed


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    run_qa(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
