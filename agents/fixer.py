"""
FixAgent + VerifyAgent — combined into one API call.

Applies approved fixes AND verifies each one resolved its issue in a single round trip.
Saves confirmed fix pairs to fix_examples.json for future calibration.
"""

import json

from anthropic import Anthropic

from agents.context import for_fix, save_fix_example

MODEL = "claude-sonnet-4-6"
client = Anthropic()

_VERIFY_ONLY_INSTRUCTIONS = """\
You are a compliance verifier. Surgical text fixes have already been applied to the article.
Confirm whether each fix resolved its compliance issue. Do NOT rewrite anything.

Return ONLY valid JSON:
{
  "verified": [
    {
      "flag_id": <integer>,
      "resolved": <boolean>,
      "new_issue_introduced": <boolean>,
      "note": "<1 sentence>"
    }
  ],
  "all_clean": <boolean>,
  "remaining_count": <integer>,
  "summary": "<1 sentence>"
}\
"""

_INSTRUCTIONS = """\
You are a compliance editor and verifier. Do two things in one pass:

1. APPLY the approved fixes to the article — make only the specified changes, nothing else.
2. VERIFY each fix — confirm it resolved the issue without introducing a new one.

Return the corrected article followed by a JSON verification block in this exact format:

===ARTICLE===
[full corrected article text here]

===VERIFICATION===
{
  "verified": [
    {
      "flag_id": <integer>,
      "resolved": <boolean>,
      "new_issue_introduced": <boolean>,
      "note": "<1 sentence>"
    }
  ],
  "all_clean": <boolean>,
  "remaining_count": <integer>,
  "summary": "<1 sentence>"
}\
"""


def fix_and_verify(content: str, approved_flags: list[dict]) -> dict:
    """
    FixAgent: surgical string replacement (preserves all formatting).
    VerifyAgent: Claude confirms each fix resolved its issue.

    Surgical replacement guarantees zero formatting changes — only the exact
    flagged phrases are swapped. Claude is used only to verify, not to rewrite.
    """
    if not approved_flags:
        return {"fixed_text": content, "verification": {"all_clean": True, "verified": [], "remaining_count": 0, "summary": "No fixes to apply."}}

    # ── Step 1: Surgical text replacement (Python, no LLM) ────────────────────
    fixed_text = content
    applied = []
    skipped = []

    for flag in approved_flags:
        original = flag.get("quoted_text", "")
        fix      = flag.get("suggested_fix", "")
        if not original or not fix or original == fix:
            skipped.append(flag["id"])
            continue
        if original in fixed_text:
            fixed_text = fixed_text.replace(original, fix, 1)
            applied.append(flag["id"])
        else:
            # Quoted text not found verbatim — try case-insensitive
            import re
            pattern = re.compile(re.escape(original), re.IGNORECASE)
            if pattern.search(fixed_text):
                fixed_text = pattern.sub(fix, fixed_text, count=1)
                applied.append(flag["id"])
            else:
                skipped.append(flag["id"])

    # ── Step 2: VerifyAgent — Claude confirms each fix (minimal context) ──────
    system = for_fix(approved_flags)
    system.append({"type": "text", "text": _VERIFY_ONLY_INSTRUCTIONS})

    fixes_summary = json.dumps(
        [{"flag_id": f["id"],
          "category": f["category"],
          "original": f["quoted_text"],
          "applied_fix": f.get("suggested_fix", ""),
          "was_applied": f["id"] in applied}
         for f in approved_flags],
        indent=2,
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": f"APPLIED FIXES:\n{fixes_summary}\n\nFIXED ARTICLE:\n---\n{fixed_text}"}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[:raw.rfind("```")]

    try:
        verification = json.loads(raw)
    except Exception:
        verification = {"all_clean": len(skipped) == 0, "verified": [], "remaining_count": len(skipped), "summary": f"Applied {len(applied)} fix(es) surgically. {len(skipped)} could not be matched verbatim."}

    # Save confirmed fix pairs
    if verification.get("all_clean"):
        flags_by_id = {f["id"]: f for f in approved_flags}
        for v in verification.get("verified", []):
            if v.get("resolved") and not v.get("new_issue_introduced"):
                flag = flags_by_id.get(v.get("flag_id"))
                if flag and flag.get("suggested_fix"):
                    save_fix_example(flag["category"], flag["quoted_text"], flag["suggested_fix"])

    verification["_applied_ids"] = applied
    verification["_skipped_ids"] = skipped
    return {"fixed_text": fixed_text, "verification": verification}
