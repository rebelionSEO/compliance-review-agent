#!/usr/bin/env python3
"""
Century Legal Group — Paid Media Compliance Review

Reviews Meta ad copy, PPC variants, email copy, and video scripts
against ACDR, FTC UDAP, Vendor Packet, and paid media-specific patterns.

Usage:
  python3 paid_media.py <path_to_csv_or_spreadsheet.csv>
  python3 paid_media.py --paste          # paste copy directly

Input format (CSV columns, flexible detection):
  Campaign | Variant | Primary Text | Headline | Description | CTA

Output:
  output/[filename] – Paid Media Compliance – MM.DD.YYYY – CCA.md
"""

import csv
import io
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent import MODEL, client

RULES_PATH = Path(__file__).parent / "rules" / "compliance_rules.md"
PAID_MEDIA_PATTERNS_PATH = Path(__file__).parent / "feedback_library" / "patterns_paid_media.md"
OUTPUT_DIR = Path(__file__).parent / "output"
_W = 65


def _line(char="─"):
    return char * _W


def _load_kb() -> tuple[str, str]:
    rules = RULES_PATH.read_text(encoding="utf-8") if RULES_PATH.exists() else ""
    patterns = PAID_MEDIA_PATTERNS_PATH.read_text(encoding="utf-8") if PAID_MEDIA_PATTERNS_PATH.exists() else ""
    return rules, patterns


# ── CSV parsing ───────────────────────────────────────────────────────────────

_COL_ALIASES = {
    "primary text": "primary_text",
    "primarytext": "primary_text",
    "body": "primary_text",
    "copy": "primary_text",
    "headline": "headline",
    "description": "description",
    "desc": "description",
    "cta": "cta",
    "call to action": "cta",
    "variant": "variant",
    "variant / angle": "variant",
    "experiment": "experiment",
    "campaign": "campaign",
    "campaign type": "campaign",
    "ad set / audience": "audience",
    "audience": "audience",
}


def _normalize_col(name: str) -> str:
    return _COL_ALIASES.get(name.strip().lower(), name.strip().lower().replace(" ", "_"))


def parse_csv(content: str) -> list[dict]:
    """Parse CSV into ad variant rows, skipping section headers and empty rows."""
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        return []

    normalized_fields = {f: _normalize_col(f) for f in reader.fieldnames}
    variants = []

    for row in reader:
        norm = {normalized_fields[k]: v.strip() for k, v in row.items() if k}
        primary = norm.get("primary_text", "")
        headline = norm.get("headline", "")

        # Skip blank rows and section header rows
        if not primary and not headline:
            continue
        if len(primary) < 10 and len(headline) < 10:
            continue
        # Skip rows that look like section banners (all-caps, no real copy)
        if primary.isupper() and len(primary) < 80:
            continue

        variants.append(norm)

    return variants


def format_variants_for_prompt(variants: list[dict]) -> str:
    lines = []
    for i, v in enumerate(variants, 1):
        campaign = v.get("campaign", "")
        variant = v.get("variant", f"Variant {i}")
        audience = v.get("audience", "")
        label = f"[{i}] {campaign} — {variant}"
        if audience:
            label += f" ({audience})"
        lines.append(label)
        if v.get("primary_text"):
            lines.append(f"  Primary Text: {v['primary_text'][:300]}")
        if v.get("headline"):
            lines.append(f"  Headline: {v['headline'][:100]}")
        if v.get("description"):
            lines.append(f"  Description: {v['description'][:100]}")
        if v.get("cta"):
            lines.append(f"  CTA: {v['cta']}")
        lines.append("")
    return "\n".join(lines)


# ── Claude reviews ────────────────────────────────────────────────────────────

_SYSTEMIC_INSTRUCTIONS = """\
You are a compliance reviewer for Century Legal Group paid media assets (Meta ads, PPC, email, video scripts).

Review the following ad variants for SYSTEMIC compliance issues — patterns that apply across most or all variants.
Focus on:
1. Qualification + speed pairing ("qualify in X minutes" = implied approval likelihood)
2. Benefit stacking without risk counterweight ("no fees + no bankruptcy + BBB A+" with no adverse cues)
3. Disclosure placement failures — not in headline/description, only in body copy
4. "No bankruptcy" framing — implies settlement is safe/clean without disclosing settlement's own credit damage
5. Outcome/estimate language — "options," "estimate," "qualify," "what it could look like" as implicit outcome framing
6. Social proof claims (330K+, $2.3B+) without adjacent qualification

Return ONLY valid JSON:
{
  "systemic_issues": [
    {
      "issue": "<short name>",
      "risk_level": "HIGH | MODERATE | LOW",
      "description": "<1-2 sentences>",
      "applies_to": "<which variants or 'all'>",
      "required_action": "<REMOVE | REVISE | QUALIFY | ADD DISCLOSURE>"
    }
  ],
  "overall_risk": "HIGH | MODERATE | LOW",
  "summary": "<2-3 sentence overall assessment>"
}
"""

_VARIANT_INSTRUCTIONS = """\
You are a compliance reviewer for Century Legal Group paid media assets.
Review each ad variant individually against ACDR, FTC UDAP, Vendor Packet, and paid media compliance rules.

For each variant, assess:
1. Primary text — qualification language, benefit stacking, outcome claims, required disclosures
2. Headline — outcome implication, speed claims, approval likelihood (40-char limit context)
3. Description — disclosure placement, benefit vs. risk balance
4. CTA — action language compliance

Return ONLY valid JSON:
{
  "variants": [
    {
      "index": <integer matching variant number>,
      "label": "<campaign — variant name>",
      "risk_level": "HIGH | MODERATE-HIGH | MODERATE | LOW",
      "flags": [
        {
          "field": "primary_text | headline | description | cta",
          "quoted_text": "<exact phrase>",
          "issue": "<brief description>",
          "action": "<REMOVE | REVISE | QUALIFY | ADD DISCLOSURE>"
        }
      ],
      "compliant_elements": ["<things that are correctly done>"],
      "net_impression": "<consumer skim takeaway — 1 sentence>"
    }
  ]
}
"""


def review_systemic(variants_text: str, rules: str, patterns: str) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=[
            {"type": "text", "text": f"# COMPLIANCE RULES\n\n{rules}", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": f"# PAID MEDIA PATTERNS\n\n{patterns}", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": _SYSTEMIC_INSTRUCTIONS},
        ],
        messages=[{"role": "user", "content": f"Review these ad variants for systemic issues:\n\n{variants_text}"}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
    return json.loads(raw)


def review_variants(variants_text: str, rules: str, patterns: str) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=[
            {"type": "text", "text": f"# COMPLIANCE RULES\n\n{rules}", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": f"# PAID MEDIA PATTERNS\n\n{patterns}", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": _VARIANT_INSTRUCTIONS},
        ],
        messages=[{"role": "user", "content": f"Review each ad variant:\n\n{variants_text}"}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
    return json.loads(raw)


# ── Output ────────────────────────────────────────────────────────────────────

_RISK_ICON = {"HIGH": "🔴", "MODERATE-HIGH": "🟠", "MODERATE": "🟡", "LOW": "🟢"}


def save_feedback_md(systemic: dict, variant_review: dict, filename: str) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    stem = Path(filename).stem
    date_str = datetime.now().strftime("%m.%d.%Y")
    out_path = OUTPUT_DIR / f"{stem} – Paid Media Compliance – {date_str} – CCA.md"

    overall_risk = systemic.get("overall_risk", "HIGH")
    icon = _RISK_ICON.get(overall_risk, "🔴")

    lines = [
        f"# Paid Media Compliance Feedback — {stem}",
        "",
        f"**Material Name:** {stem}",
        f"**Review Date:** {datetime.now().strftime('%Y-%m-%d')}",
        f"**Reviewer:** Century Compliance Agent (CCA) — requires Operations human validation",
        f"**Material Type:** Paid Media / Meta Ad Copy",
        f"**Overall Risk:** {icon} {overall_risk}",
        "",
        f"> {systemic.get('summary', '')}",
        "",
        "---",
        "",
        "## Systemic Issues (Apply Across All Variants)",
        "",
    ]

    for issue in systemic.get("systemic_issues", []):
        risk = issue.get("risk_level", "MODERATE")
        icon_s = _RISK_ICON.get(risk, "🟡")
        lines += [
            f"### {icon_s} {issue['issue']}  `{risk}`",
            f"**Applies to:** {issue.get('applies_to', 'all variants')}  ",
            f"**Action:** {issue.get('required_action', 'REVISE')}",
            "",
            issue.get("description", ""),
            "",
        ]

    lines += ["---", "", "## Per-Variant Review", ""]

    for v in variant_review.get("variants", []):
        risk = v.get("risk_level", "MODERATE")
        icon_v = _RISK_ICON.get(risk, "🟡")
        lines += [
            f"### {icon_v} Variant {v['index']} — {v.get('label', '')}  `{risk}`",
            f"**Net impression:** {v.get('net_impression', '')}",
            "",
        ]

        flags = v.get("flags", [])
        if flags:
            lines.append("**Flags:**")
            lines.append("")
            for flag in flags:
                lines.append(f"- **{flag.get('field', '').upper()}** `{flag.get('action', '')}` — \"{flag.get('quoted_text', '')}\"")
                lines.append(f"  {flag.get('issue', '')}")
            lines.append("")

        compliant = v.get("compliant_elements", [])
        if compliant:
            lines.append("**Compliant elements:**")
            for c in compliant:
                lines.append(f"- ✓ {c}")
            lines.append("")

        lines.append("---")
        lines.append("")

    lines += [
        "## Disclosure Standard Reminder",
        "",
        "**Required image disclosure text:**",
        "> Results not guaranteed. Will negatively affect credit. Fees apply. 24–48 mo. program.",
        "",
        "**Sizing:** Minimum 60% of the smallest benefit claim text on the image.",
        "**Placement:** Proximate to claims — same visual area, not a disconnected corner.",
        "**Checklist creatives:** Add 4th item with ⓘ icon (not ✓) for disclosure.",
        "",
        "---",
        "",
        f"*Agent review: Century Compliance Agent (CCA) · {datetime.now().strftime('%Y-%m-%d')}*",
        "*⚠️ DRAFT — Requires human validation before use.*",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "--paste":
        print("Paste your CSV content below (include header row).")
        print("Press Ctrl+D when done:\n")
        content = sys.stdin.read()
        filename = "pasted_ad_copy"
    else:
        p = Path(sys.argv[1])
        if not p.exists():
            print(f"Error: file not found — {p}")
            sys.exit(1)
        content = p.read_text(encoding="utf-8")
        filename = p.name

    variants = parse_csv(content)
    if not variants:
        print("No ad variants found. Make sure the CSV has headers and copy rows.")
        sys.exit(1)

    print(f"\nFound {len(variants)} ad variant(s) in '{filename}'")
    print(f"\n{_line()}")
    print("Running compliance review...")
    print("  Phase 1: Systemic issues across all variants...")

    rules, patterns = _load_kb()
    variants_text = format_variants_for_prompt(variants)

    systemic = review_systemic(variants_text, rules, patterns)

    print(f"  Phase 2: Per-variant review ({len(variants)} variants)...")
    variant_review = review_variants(variants_text, rules, patterns)

    print(f"\n{_line()}")
    print(f"Overall Risk : {systemic.get('overall_risk', 'N/A')}")
    print(f"Systemic Issues: {len(systemic.get('systemic_issues', []))}")
    print(f"Variants reviewed: {len(variant_review.get('variants', []))}")

    print("\nSystemic issues found:")
    for issue in systemic.get("systemic_issues", []):
        print(f"  [{issue.get('risk_level', ''):8}] {issue['issue']}")

    out_path = save_feedback_md(systemic, variant_review, filename)
    print(f"\n✓ Feedback saved to:\n  {out_path}")
    print("\nNext step: review flagged items and send to the creative team for revision.")


if __name__ == "__main__":
    main()
