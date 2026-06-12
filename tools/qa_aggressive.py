#!/usr/bin/env python3
"""
Aggressive QA Tool — challenges the agent on inconsistencies, edge cases, and nuance.

This is NOT a rubber stamp. It:
1. Compares flagged phrases across sections and challenges different risk levels
2. Checks if the agent missed similar violations elsewhere
3. Tests edge cases where the agent might have been too lenient
4. Requires human justification for auto-approved flags
5. Escalates patterns of missed violations

Usage:
  python3 tools/qa_aggressive.py <path_to_draft.json>
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.context import auto_approve, save_domain_decision, save_fix_example, update_confidence
from agents.fixer import fix_and_verify

OUTPUT_DIR = Path(__file__).parent.parent / "output"
FALSE_POSITIVES = Path(__file__).parent.parent / "knowledge" / "memory" / "false_positives.md"
_W = 75


def _line(char="─"):
    return char * _W


def _risk_icon(risk):
    return {"LOW": "🟢", "MODERATE": "🟡", "HIGH": "🔴", "CRITICAL": "⛔"}.get(risk, "⚠")


class AggregateAnalyzer:
    """Detects inconsistencies and challenges agent decisions"""
    
    def __init__(self, flags: list):
        self.flags = flags
    
    def get_phrase_analysis(self):
        """Find same phrases in different sections — challenge if risk levels don't make sense"""
        phrase_map = defaultdict(list)
        for flag in self.flags:
            phrase = flag.get("quoted_text", "").lower()[:80]
            phrase_map[phrase].append(flag)
        
        inconsistencies = []
        for phrase, flags_with_phrase in phrase_map.items():
            if len(flags_with_phrase) > 1:
                risk_levels = [f.get("risk_level") for f in flags_with_phrase]
                sections = [f.get("_section_type") for f in flags_with_phrase]
                
                if len(set(risk_levels)) > 1:
                    inconsistencies.append({
                        "phrase": phrase,
                        "flags": [f["id"] for f in flags_with_phrase],
                        "sections": sections,
                        "risk_levels": risk_levels,
                        "issue": "Same phrase with different risk levels. Is this justified by section context?"
                    })
        
        return inconsistencies
    
    def get_missing_escalations(self):
        """Find LOW/MODERATE flags that might actually be HIGH based on section type"""
        issues = []
        
        for flag in self.flags:
            section = flag.get("_section_type", "unknown")
            risk = flag.get("risk_level", "")
            category = flag.get("category", "")
            
            if section in ("headline", "hero", "bullet_list", "cta"):
                if category in ("Outcome Claim", "Speed Claim", "Benefit Stacking") and risk in ("LOW", "MODERATE"):
                    issues.append({
                        "flag_id": flag["id"],
                        "section": section,
                        "category": category,
                        "current_risk": risk,
                        "issue": f"'{category}' in high-risk section '{section}' is usually HIGH or CRITICAL. Why {risk}?"
                    })
            
            if category == "Disclosure Placement" and risk != "CRITICAL":
                issues.append({
                    "flag_id": flag["id"],
                    "category": category,
                    "current_risk": risk,
                    "issue": f"Missing/misplaced disclosure in '{section}' should typically be CRITICAL. Why {risk}?"
                })
        
        return issues
    
    def get_edge_cases(self):
        """Find patterns agent might have missed: similar claims across content"""
        edge_cases = []
        
        for keyword in ["qualify", "approve", "guaranteed", "guarantee", "will get", "relief", "eliminate"]:
            matching_flags = [f for f in self.flags if keyword.lower() in f.get("quoted_text", "").lower()]
            if matching_flags:
                edge_cases.append({
                    "keyword": keyword,
                    "instances_flagged": len(matching_flags),
                    "flags": [f["id"] for f in matching_flags],
                    "note": f"Found {len(matching_flags)} instance(s) with '{keyword}'. Are there MORE in the content that weren't flagged?"
                })
        
        return edge_cases


def print_flag_aggressive(flag):
    """Print flag with aggressive tone and section context"""
    risk = flag.get("risk_level", "")
    auto = " [AUTO-APPROVABLE]" if flag.get("_auto_approve") else ""
    section = flag.get("_section_type", "unknown").upper()
    print(f"\n{_line()}")
    print(f"FLAG {flag['id']} — {flag['category']}")
    print(f"{_risk_icon(risk)} {risk}  |  [{section}]{auto}")
    print(f"Rule: {flag.get('rule_ref', 'N/A')}")
    print(f"\nQuoted text:")
    print(f'  "{flag["quoted_text"]}"')
    print(f"\nWhy flagged:")
    print(f"  {flag['violation']}")
    print(f"\nConsumer belief:")
    print(f"  {flag.get('consumer_belief', 'N/A')}")
    print(f"\nLiability:")
    print(f"  {flag.get('liability', 'N/A')}")
    print(f"\nFix:")
    print(f"  {flag['suggested_fix']}")
    if flag.get("_risk_context"):
        print(f"\nSection context:")
        print(f"  {flag['_risk_context']}")


def run_aggressive_qa(json_path: Path):
    """
    Aggressive QA that challenges agent decisions and catches inconsistencies.
    """
    data = json.loads(json_path.read_text(encoding="utf-8"))
    article = json_path.stem.split(" – ")[0] if " – " in json_path.stem else json_path.stem
    flags = data.get("flags", [])

    print(f"\n{'═' * _W}")
    print("AGGRESSIVE QA VALIDATION")
    print(f"Article: {article}")
    print(f"Status: {data.get('status')}  |  Risk: {data.get('overall_risk')}  |  Flags: {len(flags)}")
    print(f"{'═' * _W}")

    analyzer = AggregateAnalyzer(flags)
    
    inconsistencies = analyzer.get_phrase_analysis()
    escalation_challenges = analyzer.get_missing_escalations()
    edge_cases = analyzer.get_edge_cases()
    
    if inconsistencies:
        print(f"\n⚠️  CONSISTENCY ISSUES DETECTED ({len(inconsistencies)}):")
        print(_line("="))
        for inc in inconsistencies:
            print(f"\n🔄 Phrase: \"{inc['phrase'][:60]}...\"")
            print(f"   Flags: {inc['flags']}")
            print(f"   Sections: {inc['sections']}")
            print(f"   Risk levels: {inc['risk_levels']}")
            print(f"   CHALLENGE: {inc['issue']}")
    
    if escalation_challenges:
        print(f"\n⛔  POTENTIAL UNDER-ESCALATIONS ({len(escalation_challenges)}):")
        print(_line("="))
        for challenge in escalation_challenges:
            print(f"\n🔺 Flag {challenge['flag_id']}: {challenge['category']}")
            print(f"   Current: {challenge['current_risk']} | Section: {challenge['section']}")
            print(f"   CHALLENGE: {challenge['issue']}")

    reviewer = input("\nYour initials: ").strip().upper()
    if not reviewer:
        print("Initials required.")
        sys.exit(1)

    auto_flags = [f for f in flags if f.get("_auto_approve")]
    manual_flags = [f for f in flags if not f.get("_auto_approve")]

    confirmed = []
    rejected = []

    if auto_flags:
        print(f"\n{_line('=')}")
        print(f"AUTO-APPROVABLE FLAGS ({len(auto_flags)}) — YOU MUST CONFIRM EACH")
        print("Even 'high confidence' flags need human eyes. Review each.")
        
        for f in auto_flags:
            print_flag_aggressive(f)
            
            while True:
                choice = input("\n[c] Confirm  [r] Reject  [e] Edit/regrade  [skip] Auto-confirm rest: ").strip().lower()
                if choice == "c":
                    confirmed.append(f)
                    section = f.get("_section_type", "unknown")
                    update_confidence(f["category"], f["quoted_text"], section_type=section, decision="confirmed")
                    print("  ✓ Confirmed")
                    break
                elif choice == "r":
                    reason = input("  Why reject: ").strip() or "Rejected by aggressive QA"
                    f["_rejection_reason"] = reason
                    rejected.append(f)
                    section = f.get("_section_type", "unknown")
                    update_confidence(f["category"], f["quoted_text"], section_type=section, decision="rejected")
                    print("  ✗ Rejected — this was auto-approved but you disagreed")
                    break
                elif choice == "e":
                    print("\n  Options:")
                    print("    [r] Upgrade risk level (LOW→MODERATE→HIGH→CRITICAL)")
                    print("    [f] Edit the fix")
                    print("    [c] Keep as-is")
                    subvote = input("  Action: ").strip().lower()
                    
                    if subvote == "r":
                        risk_map = {"LOW": "MODERATE", "MODERATE": "HIGH", "HIGH": "CRITICAL", "CRITICAL": "CRITICAL"}
                        old_risk = f.get("risk_level", "LOW")
                        new_risk = risk_map[old_risk]
                        f["risk_level"] = new_risk
                        print(f"  Risk upgraded: {old_risk} → {new_risk}")
                    elif subvote == "f":
                        new_fix = input(f"  Current fix: {f['suggested_fix']}\n  New fix: ").strip()
                        if new_fix:
                            f["suggested_fix"] = new_fix
                            print(f"  Fix updated")
                    
                    confirmed.append(f)
                    section = f.get("_section_type", "unknown")
                    update_confidence(f["category"], f["quoted_text"], section_type=section, decision="edited")
                    print("  ✓ Confirmed with changes")
                    break
                elif choice == "skip":
                    remaining = auto_flags[auto_flags.index(f):]
                    confirmed.extend(remaining)
                    for remaining_f in remaining:
                        section = remaining_f.get("_section_type", "unknown")
                        update_confidence(remaining_f["category"], remaining_f["quoted_text"], section_type=section, decision="confirmed")
                    print(f"  Auto-confirmed {len(remaining)} remaining flags")
                    break
                else:
                    print("  Invalid. Try again.")

    if manual_flags:
        print(f"\n{_line('=')}")
        print(f"MANUAL REVIEW FLAGS ({len(manual_flags)})")
        print("[c] Confirm  [r] Reject  [u] Upgrade risk  [e] Edit fix  [s] Skip rest\n")
        
        for flag in manual_flags:
            print_flag_aggressive(flag)
            
            while True:
                choice = input("\n  Decision [c/r/u/e/s]: ").strip().lower()
                if choice == "c":
                    confirmed.append(flag)
                    section = flag.get("_section_type", "unknown")
                    update_confidence(flag["category"], flag["quoted_text"], section_type=section, decision="confirmed")
                    print("  ✓ Confirmed")
                    break
                elif choice == "r":
                    reason = input("  Reason: ").strip() or "Rejected by QA"
                    flag["_rejection_reason"] = reason
                    rejected.append(flag)
                    section = flag.get("_section_type", "unknown")
                    update_confidence(flag["category"], flag["quoted_text"], section_type=section, decision="rejected")
                    print("  ✗ Rejected")
                    break
                elif choice == "u":
                    risk_map = {"LOW": "MODERATE", "MODERATE": "HIGH", "HIGH": "CRITICAL"}
                    old = flag.get("risk_level", "LOW")
                    new = risk_map.get(old, "CRITICAL")
                    flag["risk_level"] = new
                    print(f"  ⬆️  Upgraded {old} → {new}")
                    confirmed.append(flag)
                    break
                elif choice == "e":
                    new_fix = input(f"  Current: {flag['suggested_fix']}\n  New: ").strip()
                    if new_fix:
                        flag["suggested_fix"] = new_fix
                    confirmed.append(flag)
                    print("  ✓ Confirmed with edit")
                    break
                elif choice == "s":
                    confirmed.extend(manual_flags[manual_flags.index(flag):])
                    for remaining in manual_flags[manual_flags.index(flag):]:
                        section = remaining.get("_section_type", "unknown")
                        update_confidence(remaining["category"], remaining["quoted_text"], section_type=section, decision="confirmed")
                    print(f"  Skipped remaining")
                    break
                else:
                    print("  Invalid.")

    print(f"\n{'═' * _W}")
    print(f"✓ Confirmed: {len(confirmed)}  |  ✗ Rejected: {len(rejected)}")
    
    link_review = data.get("link_review", {})
    unknown_links = link_review.get("unknown", [])
    if unknown_links:
        print(f"\n{_line()}")
        print(f"LINK REVIEW — {len(unknown_links)} non-gov link(s)")
        print("[s] Safe  [r] Reject  [k] Keep unknown\n")
        for lnk in unknown_links:
            domain = lnk["url"].split("/")[2]
            print(f"  {lnk['url']}")
            while True:
                choice = input("  [s/r/k]: ").strip().lower()
                if choice == "s":
                    save_domain_decision(domain, "safe")
                    print(f"  ✓ {domain} → safe")
                    break
                elif choice == "r":
                    save_domain_decision(domain, "rejected")
                    print(f"  ✗ {domain} → rejected")
                    break
                elif choice == "k":
                    break

    data["flags"] = confirmed
    data["_qa_reviewer"] = reviewer
    data["_qa_date"] = datetime.now().isoformat()
    data["_qa_rejected"] = rejected

    approved_json = json_path.parent / json_path.name.replace("– CCA.json", f"– CCA – {reviewer}.json")
    approved_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\n✓ QA results saved: {approved_json.name}")

    return data, confirmed


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    run_aggressive_qa(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
