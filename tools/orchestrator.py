"""
Century Legal Group — Compliance Orchestrator

Coordinates all agents in the compliance pipeline. Single source of truth
for flow decisions, state management, and error handling.

Agents called (in order):
  1. ReviewAgent   — flags compliance issues
  2. QA            — human validates flags (skipped if all auto-approvable)
  3. FixAgent      — applies approved fixes
  4. VerifyAgent   — confirms fixes resolved issues, catches regressions
  5. Delivery      — saves final output, produces handoff summary

State is saved after every stage so interrupted runs can resume.
All routing decisions live here — agents only do their one job.
"""

import json
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent.parent
OUTPUT_DIR = BASE / "output"
SESSION_DIR = OUTPUT_DIR / "sessions"
DRIVE_CONFIG = BASE / "knowledge" / "memory" / "drive_config.json"


def _get_drive_folder_id(content_type: str) -> str | None:
    """Return the Drive folder ID for a given content type."""
    if not DRIVE_CONFIG.exists():
        return None
    config = json.loads(DRIVE_CONFIG.read_text(encoding="utf-8"))
    folder = config.get("output_folders", {}).get(content_type)
    return folder["id"] if folder else None

# ── State management ──────────────────────────────────────────────────────────

STAGES = [
    "start",
    "review_complete",
    "qa_complete",
    "fix_complete",
    "verify_complete",
    "done",
]


def _session_path(filename: str) -> Path:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return SESSION_DIR / f"{stem}_{ts}.json"


def _save(state: dict, path: Path) -> None:
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _new_state(filename: str, content_type: str) -> dict:
    return {
        "filename": filename,
        "content_type": content_type,
        "stage": "start",
        "created": datetime.now().isoformat(),
        "review_result": None,
        "approved_flags": None,
        "fixed_text": None,
        "verify_result": None,
        "output_path": None,
    }


# ── Stage handlers ────────────────────────────────────────────────────────────

def _stage_review(content: str, filename: str, content_type: str, state: dict, session: Path) -> dict:
    from agents.reviewer import review

    print(f"\n[1/4] ReviewAgent — scanning '{filename}'…")
    result = review(content, filename, content_type)
    result["_submission_date"] = datetime.now().strftime("%Y-%m-%d")

    flags = result.get("flags", [])
    auto_count = sum(1 for f in flags if f.get("_auto_approve"))
    link_summary = result.get("link_review_summary", "")

    print(f"      Status  : {result['status']}")
    print(f"      Risk    : {result['overall_risk']}")
    print(f"      Flags   : {len(flags)}  ({auto_count} auto-approvable · {len(flags)-auto_count} need review)")
    if link_summary:
        print(f"      Links   : {link_summary}")

    # Save draft docs
    from run import save_draft
    md_path, json_path = save_draft(result, filename)
    print(f"      Draft   : {md_path.name}")

    state["stage"] = "review_complete"
    state["review_result"] = result
    state["draft_json"] = str(json_path)
    _save(state, session)
    return result


def _stage_qa(result: dict, filename: str, state: dict, session: Path) -> list:
    from tools.qa import run_qa

    flags = result.get("flags", [])
    all_auto = flags and all(f.get("_auto_approve") for f in flags)

    if result["status"] == "APPROVED" or not flags:
        print("\n[2/4] QA — no flags to review, skipping.")
        state["stage"] = "qa_complete"
        state["approved_flags"] = []
        _save(state, session)
        return []

    if result.get("escalation_required"):
        print(f"\n⛔ ESCALATED — requires Legal review.")
        print(f"   Reason: {result.get('escalation_reason')}")
        print(f"   Send feedback document to attorney. Pipeline stopped.")
        state["stage"] = "escalated"
        _save(state, session)
        raise SystemExit(0)

    if result["status"] == "REJECTED":
        print("\n⛔ REJECTED — content is not permissible.")
        print("   Return to content team for full rewrite.")
        state["stage"] = "rejected"
        _save(state, session)
        raise SystemExit(0)

    if all_auto:
        print(f"\n[2/4] QA — all {len(flags)} flags are HIGH confidence, auto-approving.")
        approved = flags
    else:
        print(f"\n[2/4] QA — {len(flags) - sum(1 for f in flags if f.get('_auto_approve'))} flag(s) need human review.")
        json_path = Path(state["draft_json"])
        data, approved = run_qa(json_path)

    state["stage"] = "qa_complete"
    state["approved_flags"] = approved
    _save(state, session)
    return approved


def _stage_fix(content: str, approved_flags: list, state: dict, session: Path) -> str:
    from agents.fixer import fix_and_verify

    if not approved_flags:
        print("\n[3/4] FixAgent — no approved fixes to apply, skipping.")
        state["stage"] = "fix_complete"
        state["fixed_text"] = content
        state["verify_result"] = {"all_clean": True, "verified": [], "summary": "No fixes needed."}
        _save(state, session)
        return content

    print(f"\n[3/4] FixAgent + VerifyAgent — applying {len(approved_flags)} fix(es)…")
    result = fix_and_verify(content, approved_flags)

    verification = result["verification"]
    verified = verification.get("verified", [])
    resolved = sum(1 for v in verified if v.get("resolved"))
    failed = [v for v in verified if not v.get("resolved")]
    regressions = [v for v in verified if v.get("new_issue_introduced")]

    print(f"      Resolved    : {resolved}")
    print(f"      Still open  : {len(failed)}")
    print(f"      Regressions : {len(regressions)}")
    print(f"      {verification.get('summary', '')}")

    state["stage"] = "fix_complete"
    state["fixed_text"] = result["fixed_text"]
    state["verify_result"] = verification
    _save(state, session)

    # Handle regressions — orchestrator decides, not the individual tool
    if regressions:
        print(f"\n⚠  {len(regressions)} regression(s) detected after fixes.")
        print("   [c] Continue to delivery  [s] Stop — review manually")
        while True:
            choice = input("   Choice [c/s]: ").strip().lower()
            if choice == "c":
                break
            elif choice == "s":
                print(f"   Session saved: {session.name}")
                print(f"   Resume with: python3 run.py --resume {session}")
                raise SystemExit(0)
            else:
                print("   Please enter c or s.")

    return result["fixed_text"]


def _stage_deliver(fixed_text: str, filename: str, state: dict, session: Path) -> Path:
    from run import save_final

    content_type = state.get("content_type", "blog")
    print("\n[4/4] Delivery…")

    # Save locally first
    out_path = save_final(fixed_text, filename)

    verify = state.get("verify_result", {})
    verified = verify.get("verified", [])
    resolved = sum(1 for v in verified if v.get("resolved"))
    regressions = [v for v in verified if v.get("new_issue_introduced")]

    # Save to Google Drive output folder
    drive_folder_id = _get_drive_folder_id(content_type)
    drive_url = None
    if drive_folder_id:
        try:
            # Import Drive config for folder name
            config = json.loads(DRIVE_CONFIG.read_text(encoding="utf-8"))
            folder_name = config["output_folders"][content_type]["name"]
            state["drive_folder_id"] = drive_folder_id
            state["drive_folder_name"] = folder_name
            print(f"      Drive   : Ready to upload to '{folder_name}'")
            print(f"      Note    : Run deliver_to_drive() to push to Google Drive")
        except Exception as e:
            print(f"      Drive   : Config loaded but upload requires Claude Code session ({e})")

    state["stage"] = "done"
    state["output_path"] = str(out_path)
    _save(state, session)

    print(f"\n{'═' * 65}")
    print("HANDOFF SUMMARY")
    print(f"  File         : {out_path.name}")
    print(f"  Fixed        : {resolved} issue(s) resolved and verified")
    print(f"  Drive folder : {state.get('drive_folder_name', 'not configured')}")
    if regressions:
        print(f"  ⚠ Warning  : {len(regressions)} regression(s) — review before submitting")
    else:
        print(f"  Status       : Clean — ready for Ops submission")
    print(f"  Session      : {session.name}")
    print(f"{'═' * 65}\n")

    return out_path


# ── Drive delivery (Claude Code session only) ─────────────────────────────────

def get_pending_deliveries() -> list[dict]:
    """
    Return all completed sessions that haven't been uploaded to Drive yet.
    Used by Claude Code to identify what needs to be pushed.
    """
    pending = []
    for session_file in SESSION_DIR.glob("*.json"):
        try:
            state = json.loads(session_file.read_text(encoding="utf-8"))
            if (state.get("stage") == "done"
                    and state.get("output_path")
                    and state.get("drive_folder_id")
                    and not state.get("drive_uploaded")):
                pending.append({
                    "session": str(session_file),
                    "filename": state["filename"],
                    "output_path": state["output_path"],
                    "drive_folder_id": state["drive_folder_id"],
                    "drive_folder_name": state.get("drive_folder_name", ""),
                    "content_type": state.get("content_type", "blog"),
                })
        except Exception:
            continue
    return pending


def mark_uploaded(session_path: str, drive_file_url: str) -> None:
    """Mark a session as uploaded to Drive."""
    p = Path(session_path)
    if p.exists():
        state = json.loads(p.read_text(encoding="utf-8"))
        state["drive_uploaded"] = True
        state["drive_file_url"] = drive_file_url
        state["drive_uploaded_at"] = datetime.now().isoformat()
        p.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ── Public API ────────────────────────────────────────────────────────────────

def run(
    content: str,
    filename: str,
    content_type: str = "blog",
    resume_from: Path = None,
) -> dict:
    """
    Run the full compliance pipeline for a single piece of content.

    Returns final state dict with output_path on success.
    Raises SystemExit on ESCALATED, REJECTED, or user-stopped runs.
    State is saved after every stage — pass resume_from to continue an interrupted run.
    """
    if resume_from and resume_from.exists():
        state = json.loads(resume_from.read_text(encoding="utf-8"))
        session = resume_from
        stage = state.get("stage", "start")
        print(f"Resuming from stage: {stage}")
    else:
        session = _session_path(filename)
        state = _new_state(filename, content_type)
        stage = "start"

    print(f"\n{'═' * 65}")
    print("CENTURY COMPLIANCE SYSTEM")
    print(f"File    : {filename}")
    print(f"Type    : {content_type}")
    print(f"Session : {session.name}")
    print(f"{'═' * 65}")

    # Stage 1 — Review
    if stage in ("start",):
        review_result = _stage_review(content, filename, content_type, state, session)
    else:
        review_result = state["review_result"]

    # Stage 2 — QA
    if stage in ("start", "review_complete"):
        approved_flags = _stage_qa(review_result, filename, state, session)
    else:
        approved_flags = state.get("approved_flags", [])

    # Stage 3 — Fix + Verify
    if stage in ("start", "review_complete", "qa_complete"):
        fixed_text = _stage_fix(content, approved_flags, state, session)
    else:
        fixed_text = state.get("fixed_text", content)

    # Stage 4 — Deliver
    if stage in ("start", "review_complete", "qa_complete", "fix_complete"):
        _stage_deliver(fixed_text, filename, state, session)

    return state


def run_delta(
    content: str,
    filename: str,
    v1_json_path: Path,
    content_type: str = "blog",
) -> None:
    """
    Delta review — checks a revised article against v1 flags.
    Lighter than a full run: no fix/verify, just shows what changed.
    """
    from legacy.agent import review_revision

    session = _session_path(f"{filename}_delta")
    state = _new_state(filename, content_type)

    v1_data = json.loads(v1_json_path.read_text(encoding="utf-8"))
    v1_flags = v1_data.get("flags", [])

    if not v1_flags:
        print("No v1 flags found — running full review instead.")
        run(content, filename, content_type)
        return

    print(f"\n{'═' * 65}")
    print("REVISION CHECK")
    print(f"File    : {filename}")
    print(f"v1 flags: {len(v1_flags)}")
    print(f"{'═' * 65}")

    print("\n[1/1] Comparing against v1 flags…")
    delta = review_revision(content, v1_flags, filename)

    resolved = len(delta.get("resolved", []))
    still = len(delta.get("still_present", []))
    new = len(delta.get("new_issues", []))

    print(f"\n  Resolved     : {resolved}")
    print(f"  Still present: {still}")
    print(f"  New issues   : {new}")
    print(f"\n  {delta.get('summary', '')}")

    from run import _write_delta_md
    out = OUTPUT_DIR / f"{Path(filename).stem} – Revision Check – {datetime.now().strftime('%m.%d.%Y')} – CCA.md"
    _write_delta_md(delta, Path(filename).stem, out, v1_json_path.name)
    print(f"\n✓ Revision check saved: {out.name}")
