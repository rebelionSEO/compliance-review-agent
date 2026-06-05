"""
Route-based context loader.

Loads only the KB sections relevant for a given content type and pipeline step.
Avoids injecting the full 42KB knowledge base on every call.
"""

from pathlib import Path

BASE = Path(__file__).parent.parent

# Knowledge paths
RULES          = BASE / "knowledge" / "rules" / "compliance_rules.md"
BLOG_PATTERNS  = BASE / "knowledge" / "patterns" / "blog.md"
PPC_PATTERNS   = BASE / "knowledge" / "patterns" / "paid_media.md"
PROCESS        = BASE / "knowledge" / "process" / "century_review_process.md"
FIX_EXAMPLES   = BASE / "knowledge" / "memory" / "fix_examples.json"
CONFIDENCE     = BASE / "knowledge" / "memory" / "confidence.json"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def for_review(content_type: str = "blog") -> list[dict]:
    """Full context for ReviewAgent — rules + relevant patterns + process."""
    blocks = [
        {"type": "text", "text": f"# COMPLIANCE RULES\n\n{_read(RULES)}", "cache_control": {"type": "ephemeral"}},
    ]
    if content_type in ("blog", "article", "landing_page", "email"):
        blocks.append({"type": "text", "text": f"# REVIEW PATTERNS (BLOG/ARTICLE)\n\n{_read(BLOG_PATTERNS)}", "cache_control": {"type": "ephemeral"}})
    elif content_type in ("paid_media", "ppc", "meta", "social"):
        blocks.append({"type": "text", "text": f"# REVIEW PATTERNS (PAID MEDIA)\n\n{_read(PPC_PATTERNS)}", "cache_control": {"type": "ephemeral"}})
    blocks.append({"type": "text", "text": f"# CENTURY REVIEW PROCESS\n\n{_read(PROCESS)}", "cache_control": {"type": "ephemeral"}})
    return blocks


def for_fix(flags: list[dict]) -> list[dict]:
    """
    Minimal context for FixAgent — rules + fix examples only.
    No process guide. Loads only rule categories relevant to the flags being fixed.
    """
    # Extract only the rule categories needed
    needed_categories = {f.get("category", "") for f in flags}
    rules_text = _read(RULES)

    # Load fix examples for extra grounding
    import json
    fix_examples_raw = _read(FIX_EXAMPLES)
    try:
        examples_data = json.loads(fix_examples_raw)
        examples = examples_data.get("examples", [])
        # Filter to relevant categories
        relevant = [e for e in examples if e.get("category") in needed_categories]
        examples_text = ""
        if relevant:
            examples_text = "# CONFIRMED FIX EXAMPLES\n\n"
            for ex in relevant[:20]:  # cap at 20 examples
                examples_text += f"Category: {ex['category']}\n"
                examples_text += f"Original: \"{ex['original']}\"\n"
                examples_text += f"Fixed:    \"{ex['fixed']}\"\n\n"
    except Exception:
        examples_text = ""

    blocks = [
        {"type": "text", "text": f"# COMPLIANCE RULES\n\n{rules_text}", "cache_control": {"type": "ephemeral"}},
    ]
    if examples_text:
        blocks.append({"type": "text", "text": examples_text, "cache_control": {"type": "ephemeral"}})
    return blocks


def for_delta(content_type: str = "blog") -> list[dict]:
    """Context for delta/revision review — rules + patterns, no process guide."""
    blocks = [
        {"type": "text", "text": f"# COMPLIANCE RULES\n\n{_read(RULES)}", "cache_control": {"type": "ephemeral"}},
    ]
    if content_type in ("paid_media", "ppc", "meta"):
        blocks.append({"type": "text", "text": f"# PATTERNS\n\n{_read(PPC_PATTERNS)}", "cache_control": {"type": "ephemeral"}})
    else:
        blocks.append({"type": "text", "text": f"# PATTERNS\n\n{_read(BLOG_PATTERNS)}", "cache_control": {"type": "ephemeral"}})
    return blocks


def get_confidence(category: str, quoted_text: str) -> dict:
    """Return confidence data for a flag type."""
    import json
    data = json.loads(_read(CONFIDENCE)) if CONFIDENCE.exists() else {"patterns": {}}
    key = f"{category}::{quoted_text[:60]}"
    return data["patterns"].get(key, {"confirmed": 0, "rejected": 0, "edits": 0})


def auto_approve(category: str, quoted_text: str, min_reviews: int = 5, min_score: float = 0.8) -> bool:
    """Return True if this flag type has enough confirmed history to auto-approve."""
    conf = get_confidence(category, quoted_text)
    total = conf["confirmed"] + conf["rejected"]
    if total < min_reviews:
        return False
    score = conf["confirmed"] / total
    return score >= min_score


def update_confidence(category: str, quoted_text: str, decision: str) -> None:
    """
    Update confidence score after a QA decision.
    decision: 'confirmed' | 'rejected' | 'edited'
    """
    import json
    path = CONFIDENCE
    data = json.loads(_read(path)) if path.exists() else {"patterns": {}}
    key = f"{category}::{quoted_text[:60]}"
    entry = data["patterns"].get(key, {"confirmed": 0, "rejected": 0, "edits": 0})
    if decision == "confirmed":
        entry["confirmed"] += 1
    elif decision == "rejected":
        entry["rejected"] += 1
    elif decision == "edited":
        entry["edits"] = entry.get("edits", 0) + 1
        entry["confirmed"] += 1  # edited = still valid, counts as confirmed
    data["patterns"][key] = entry
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


URL_REGISTRY = BASE / "knowledge" / "memory" / "url_registry.json"


def check_links(content: str) -> dict:
    """
    Extract and classify all links in the content.
    Returns: { compliant: [], flagged: [], unknown: [] }
    Each entry: { url, type, reason }
    """
    import re
    import json

    registry = json.loads(_read(URL_REGISTRY)) if URL_REGISTRY.exists() else {}
    safe_domains = set(registry.get("safe_external_domains", []))
    rejected_domains = set(registry.get("rejected_external_domains", []))
    product_urls = {p["url"].rstrip("/") for p in registry.get("product_pages", [])}
    reviewed_urls = {b["url"].rstrip("/") for b in registry.get("blog_posts_known_compliant", [])}
    all_safe_internal = product_urls | reviewed_urls

    # Extract all URLs from content
    urls = re.findall(r'https?://[^\s\)\]"\'<>]+', content)
    urls = list(set(u.rstrip(".,)") for u in urls))

    compliant = []
    flagged = []
    unknown = []

    for url in urls:
        url_clean = url.rstrip("/")
        domain = url.split("/")[2] if "/" in url else url

        # Internal link
        if "centuryss.com" in domain:
            if url_clean in all_safe_internal:
                compliant.append({"url": url, "type": "internal", "reason": "known compliant page"})
            else:
                flagged.append({"url": url, "type": "internal", "reason": "not in compliance registry — verify publish date and compliance status"})

        # External link — check rejected list first
        elif any(rej in domain for rej in rejected_domains):
            flagged.append({"url": url, "type": "external", "reason": f"PREVIOUSLY REJECTED domain ({domain}) — do not use as source"})
        elif any(safe in domain for safe in safe_domains):
            compliant.append({"url": url, "type": "external", "reason": f".gov or approved domain ({domain})"})
        else:
            unknown.append({"url": url, "type": "external", "reason": f"non-gov third party ({domain}) — verify no implied endorsement or affiliation"})

    return {"compliant": compliant, "flagged": flagged, "unknown": unknown}


def save_domain_decision(domain: str, decision: str) -> None:
    """
    Record a QA decision about an external domain.
    decision: 'safe' → adds to safe_external_domains
              'rejected' → adds to rejected_external_domains
    """
    path = URL_REGISTRY
    data = json.loads(_read(path)) if path.exists() else {}
    if decision == "safe":
        safe = set(data.get("safe_external_domains", []))
        safe.add(domain)
        data["safe_external_domains"] = sorted(safe)
    elif decision == "rejected":
        rejected = set(data.get("rejected_external_domains", []))
        rejected.add(domain)
        data["rejected_external_domains"] = sorted(rejected)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def save_fix_example(category: str, original: str, fixed: str, article: str = "") -> None:
    """Save a confirmed fix pair to the fix examples library."""
    import json
    from datetime import datetime
    path = FIX_EXAMPLES
    data = json.loads(_read(path)) if path.exists() else {"examples": []}
    data["examples"].append({
        "category": category,
        "original": original,
        "fixed": fixed,
        "source": article,
        "date": datetime.now().strftime("%Y-%m-%d"),
    })
    # Keep last 200 examples to avoid bloating context
    data["examples"] = data["examples"][-200:]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
