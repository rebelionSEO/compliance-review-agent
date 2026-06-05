"""
ReviewAgent — flags compliance issues in blog/article content.

Uses route-based context loading (context.py) to inject only what's needed.
Returns confidence-scored flags so the orchestrator can auto-approve high-confidence ones.
"""

import json
from pathlib import Path

from anthropic import Anthropic

from agents.context import auto_approve, check_links, for_review

MODEL = "claude-sonnet-4-6"
client = Anthropic()

_INSTRUCTIONS = """\
You are an Operations compliance reviewer for Century Legal Group, regulated by ACDR.

Review the content against the compliance rules, patterns, and review process in your knowledge base.

STEP 1 — Identify material type: Blog/Article | Email | Landing Page | PPC/Paid Ad | Image/Visual | Social Post
STEP 2 — Flag all individual issues. Do NOT group or summarize. Each issue = one flag.
STEP 3 — Assess NET IMPRESSION: what does a consumer who skims only headlines and bullets conclude?
STEP 4 — Assess DISCLOSURE REVIEW: are required disclosures present AND proximate?
STEP 5 — Determine verdict using Century's taxonomy.

Return ONLY valid JSON:
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
      "section": "<section heading>",
      "paragraph": <integer>,
      "risk_level": "LOW" | "MODERATE" | "HIGH" | "CRITICAL",
      "category": "<category>",
      "rule_ref": "<rule reference>",
      "quoted_text": "<verbatim text>",
      "violation": "<1 sentence>",
      "action_required": "<action>",
      "escalation_required": <boolean>,
      "suggested_fix": "<concise fix>"
    }
  ],
  "disclosure_review": "<2 sentences max>",
  "operations_summary": "<2 sentences max>"
}\
"""


def review(content: str, filename: str = "article", content_type: str = "blog") -> dict:
    """
    ReviewAgent — full compliance scan.
    Attaches auto_approve hint to each flag based on confidence scores.
    """
    system = for_review(content_type)
    system.append({"type": "text", "text": _INSTRUCTIONS})

    response = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=system,
        messages=[{"role": "user", "content": f"Review for compliance violations.\n\nFilename: {filename}\n\n---\n\n{content}"}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[:raw.rfind("```")]

    result = json.loads(raw)

    # Attach confidence-based auto-approve hint to each flag
    for flag in result.get("flags", []):
        flag["_auto_approve"] = auto_approve(
            flag.get("category", ""),
            flag.get("quoted_text", ""),
        )

    # Run link check and attach to result
    link_report = check_links(content)
    result["link_review"] = link_report
    flagged_links = len(link_report.get("flagged", []))
    unknown_links = len(link_report.get("unknown", []))
    if flagged_links or unknown_links:
        result["link_review_summary"] = f"{flagged_links} internal link(s) not in compliance registry. {unknown_links} external non-gov link(s) need review."
    else:
        result["link_review_summary"] = "All links compliant — internal links verified, external links are .gov or approved domains."

    return result
