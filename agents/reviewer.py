"""
ReviewAgent — flags compliance issues in blog/article content.

Now uses hierarchical tree structure to provide context:
- HIGH-RISK sections (headline, hero, bullets, CTA) get more scrutiny
- Same violation in different sections = different risk levels
- AI understands "Get relief in 30 days" in headline (8x risk) vs FAQ (2x risk)
- Flags include section type so fixes can be surgical and context-aware
"""

import json
from pathlib import Path

from anthropic import Anthropic

from agents.context import auto_approve, check_links, for_review
from agents.document_tree import DocumentTree

MODEL = "claude-sonnet-4-6"
client = Anthropic()

_INSTRUCTIONS = """\
You are an ACDR compliance reviewer for Century Legal Group. Review STRUCTURED content, not flat text.

# SECTION-AWARE RISK WEIGHTING

Risk varies by WHERE a claim appears (provided in document structure above):
- **HEADLINE/HERO (8x risk)**: First impression, consumer takes away as primary guarantee
- **BULLETS/CTA (7-8x risk)**: Scanned first, dominant message, implied guarantees most problematic
- **SECTION/SUBSECTION (3-4x risk)**: Secondary content, requires context
- **FAQ (2x risk)**: Q&A format, consumer expects nuance and variation
- **FOOTER (1x risk)**: Minimal visibility, legal/small print
- **DISCLOSURE (CRITICAL)**: Must match SOP exactly, must be proximate to claims

SAME PHRASE IN DIFFERENT SECTIONS = POTENTIALLY DIFFERENT FLAGS
Example:
- "Get relief in 30 days" in HEADLINE → CRITICAL (outcome claim, implies guarantee)
- "Get relief in 30 days" in FAQ → MODERATE (Q&A answer, acceptable if clearly qualified)
- "Get relief in 30 days" in FOOTER → LOW (no consumer attention, but still flag if unqualified)

# STEP-BY-STEP REVIEW PROCESS

STEP 1 — Identify material type: Blog/Article | Email | Landing Page | PPC/Paid Ad | Image/Visual | Social Post
STEP 2 — For EACH high-risk section (headline, bullets, CTA), flag ALL outcome/speed/guarantee claims
STEP 3 — For EACH claim, assess: "What does a consumer reading ONLY THIS SECTION conclude?"
STEP 4 — Check disclosures: present? proximate? correct font size (≥60% of largest benefit claim)?
STEP 5 — Determine verdict and create flags with section context

# RISK CALIBRATION (WHEN TO FLAG)

**CRITICAL (flag always, no exceptions):**
- Outcome claims: "eliminate debt," "fix credit," "erase," "cure," "guarantee," "approved" (implies certainty)
- Speed + guarantee pairing: "get relief in X days" + "you will" + no risk statement
- Undisclosed settlement effects: "no bankruptcy" without mentioning settlement's own credit damage
- Misplaced disclosures: required language not in headline/description, buried in body only

**HIGH (flag unless FAQ context):**
- Benefit stacking without counterweight: "no fees + no bankruptcy + BBB A+" without adverse disclosure
- "Qualify" language (implies approval likelihood without qualification)
- Social proof without context: "330K+ clients" without (year? stage? type?)
- Incomplete disclosure: disclosure present but not proximate or too small

**MODERATE (flag, consider section):**
- Confusing language that could mislead
- Missing qualifier words ("may," "could," "estimate," "offer")
- Implied speed claims without explicit timeline

**LOW (flag only if in headline/hero):**
- Fine-tuning language that's acceptable in body/FAQ but risky in prominent sections

# FLAG SCHEMA

Return ONLY valid JSON. Each flag must answer:
1. Why is this a violation?
2. Why does it matter?
3. What does consumer believe from THIS SECTION?
4. How do we fix it?

{
  "status": "APPROVED" | "APPROVED WITH REQUIRED EDITS" | "RETURNED FOR REVISION" | "ESCALATED FOR LEGAL REVIEW" | "REJECTED",
  "material_type": "<type>",
  "overall_risk": "LOW" | "MODERATE" | "HIGH" | "CRITICAL",
  "flag_count": <integer>,
  "escalation_required": <boolean>,
  "escalation_reason": "<reason or null>",
  "flags": [
    {
      "id": <integer>,
      "section": "<section heading or 'Header' or 'Footer'>",
      "section_type": "headline | hero | bullet_list | cta | section | subsection | faq | footer | disclosure | table | paragraph",
      "section_risk_multiplier": <1-8>,
      "paragraph": <integer>,
      "risk_level": "LOW" | "MODERATE" | "HIGH" | "CRITICAL",
      "category": "<category: Outcome Claim | Speed Claim | Benefit Stacking | Disclosure Placement | Social Proof | etc>",
      "rule_ref": "<rule reference>",
      "quoted_text": "<verbatim text from content>",
      "violation": "<Why this breaks the rule (1 sentence)>",
      "consumer_belief": "<What consumer reading ONLY THIS SECTION concludes (1 sentence)>",
      "liability": "<What legal harm this creates (1 sentence)>",
      "action_required": "<REMOVE | REVISE | QUALIFY | RELOCATE | ADD DISCLOSURE>",
      "escalation_required": <boolean>,
      "suggested_fix": "<Exact replacement text preserving meaning>"
    }
  ],
  "disclosure_review": {
    "required_disclosures": [<list of required disclosures per rule>],
    "present": [<which are present>],
    "missing": [<which are missing>],
    "placement_issues": [<which are present but not proximate>]
  },
  "operations_summary": "<2-3 sentence summary for ops team>"
}
"""


def review(content: str, filename: str = "article", content_type: str = "blog") -> dict:
    """
    ReviewAgent — full compliance scan with hierarchical structure.
    
    Parses content into tree → provides section context to Claude →
    Claude flags violations with risk awareness → attaches section metadata.
    """
    # ✅ NEW: Parse into tree structure BEFORE review
    doc_tree = DocumentTree(content)
    tree_structure = doc_tree.serialize()
    
    # Build document structure summary for Claude
    leaf_nodes = doc_tree.flatten()
    sections_preview = []
    for i, node in enumerate(leaf_nodes[:20]):  # Limit to prevent token bloat
        section_info = f"- **{node.section_type.value.upper()}** (lines {node.start_line}-{node.end_line})"
        if node.heading:
            section_info += f": {node.heading[:50]}"
        section_info += f"\n  Risk: {node.risk_context}"
        if node.content and len(node.content) > 20:
            preview = node.content[:120].replace('\n', ' ')
            section_info += f"\n  Preview: {preview}..."
        sections_preview.append(section_info)
    
    structure_context = "\n".join(sections_preview)
    
    # Build system prompt with structure + risk guidance
    system = for_review(content_type)
    system.append({
        "type": "text",
        "text": f"""
# DOCUMENT STRUCTURE

Your article is organized as follows. Use these section types to calibrate risk:

{structure_context}

# RISK WEIGHTING BY SECTION TYPE

- **HEADLINE (H1)**: 8x risk multiplier — most visible, consumer's primary impression
- **HERO**: 6-8x risk multiplier — above-the-fold, first read
- **BULLET_LIST**: 7-8x risk multiplier — consumers scan bullets FIRST, dominant message
- **CTA**: 8x risk multiplier — direct call-to-action, highest liability
- **SECTION (H2)**: 3x risk multiplier — secondary level, provides context
- **SUBSECTION (H3)**: 2-3x risk multiplier — supporting detail
- **FAQ**: 2x risk multiplier — Q&A format, consumer expects nuance
- **FOOTER**: 1x risk multiplier — minimal attention, legal/contact info
- **DISCLOSURE**: CRITICAL — must be exact match to SOP, must be proximate to claims

**Action:** When you find a violation, note which section it's in. High-risk sections demand stricter enforcement.
Same violation in HEADLINE = CRITICAL; same violation in FAQ = MODERATE; same violation in FOOTER = LOW.
""",
        "cache_control": {"type": "ephemeral"}
    })
    
    system.append({"type": "text", "text": _INSTRUCTIONS})
    
    response = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=system,
        messages=[{
            "role": "user",
            "content": f"""Review for compliance violations.

Filename: {filename}

IMPORTANT: Refer to the document structure above. Use section types to calibrate risk.
When you flag something, ALWAYS note which section it appears in and consider the risk multiplier.
Higher-risk sections (headline, bullets, CTA) get more scrutiny.
Lower-risk sections (FAQ, footer) get more leniency for the same phrasing.

---

{content}"""
        }],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[:raw.rfind("```")]

    result = json.loads(raw)

    # ✅ NEW: Attach tree structure to result for QA/delivery stages
    result["_doc_tree"] = tree_structure
    result["_section_map"] = {
        f"lines_{node.start_line}_{node.end_line}": {
            "type": node.section_type.value,
            "heading": node.heading,
            "risk_context": node.risk_context,
        }
        for node in leaf_nodes
    }
    
    # ✅ NEW: Map each flag to its section for better fixes
    for flag in result.get("flags", []):
        quoted = flag.get("quoted_text", "")
        # Try to find which node contains this quote
        for node in leaf_nodes:
            if quoted in node.content:
                flag["_section_type"] = node.section_type.value
                flag["_section_heading"] = node.heading or flag.get("section", "")
                flag["_risk_context"] = node.risk_context
                flag["_lines"] = f"{node.start_line}-{node.end_line}"
                break

    # Run link check and attach to result
    link_report = check_links(content)
    result["link_review"] = link_report
    flagged_links = len(link_report.get("flagged", []))
    unknown_links = len(link_report.get("unknown", []))
    if flagged_links or unknown_links:
        result["link_review_summary"] = f"{flagged_links} internal link(s) not in compliance registry. {unknown_links} external non-gov link(s) need review."
    else:
        result["link_review_summary"] = "All links compliant — internal links verified, external links are .gov or approved domains."

    # Attach confidence-based auto-approve hint to each flag (using new section-aware thresholds)
    for flag in result.get("flags", []):
        flag["_auto_approve"] = auto_approve(
            flag.get("category", ""),
            flag.get("quoted_text", ""),
            section_type=flag.get("_section_type", "unknown"),  # ✅ NEW: section-aware
        )

    return result
