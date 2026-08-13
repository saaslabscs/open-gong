"""Pipeline job handlers.

`process_call`: submit audio to PyAI (mock by default); transcript returns via
the signed webhook, which enqueues `run_insights`.

`run_insights`: the insight chain inside the run-state harness —
detect_intent → extract → validate (evidence gate) → score → compose_email.
Every run finishes shipped | partial | failed, with reasons and costs on each
stage and the budget cap enforced.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from .adapters.pyai.base import get_adapter
from .db import get_session
from .compliance import run_compliance_check
from .evidence import validate_extraction
from .insights import compose_email, detect_intent, extract, prettify_transcript, score
from .jobs import enqueue, handler
from .models import Call, Run
from .packs import BUILTIN_PACKS
from .run_state import BudgetExceeded, RunState, StageFailed
from .transcription import deliver_transcript, update_transcript_lines

# how long to wait between polls of a PyAI transcription job (seconds)
POLL_INTERVAL_S = 5
MAX_POLLS = 120  # ~10 min ceiling before giving up


def select_pack(intent: str | None) -> dict:
    """An active custom pack (compiled from prose) wins over the built-in
    sales/support pack for the detected intent."""
    from .models import InsightPack

    with get_session() as session:
        active = session.scalars(
            select(InsightPack).where(InsightPack.status == "active").order_by(InsightPack.created_at.desc())
        ).first()
        if active:
            return {
                "name": active.name,
                "version": active.version,
                "json_schema": active.json_schema,
                "scoring_spec": active.scoring_spec,
            }
    return BUILTIN_PACKS.get(intent, BUILTIN_PACKS["sales"])


def _update_stage(run: Run, name: str, **updates) -> None:
    stages = [dict(s) for s in run.stages]
    for s in stages:
        if s["name"] == name:
            s.update(updates)
    run.stages = stages


@handler("process_call")
def process_call(payload: dict) -> None:
    call_id = payload["call_id"]
    with get_session() as session:
        call = session.get(Call, call_id)
        if call is None or not call.audio_path:
            raise ValueError(f"call {call_id} missing or has no audio")
        run = session.scalars(
            select(Run).where(Run.call_id == call_id).order_by(Run.created_at.desc())
        ).first()
        _update_stage(run, "transcribe", attempts=1)
        session.commit()
        audio_path = call.audio_path

    job_ref = get_adapter().submit_recording(call_id, audio_path)
    enqueue("poll_transcription", {"call_id": call_id, "job_ref": job_ref, "polls": 0})


@handler("poll_transcription")
def poll_transcription(payload: dict) -> None:
    """Poll the transcription job; deliver when ready, else re-enqueue.

    Works for both adapters: the mock returns a transcript immediately, the
    real one returns None until PyAI finishes. Terminal failure marks the run
    failed (no zombie 'running' state) rather than retrying forever.
    """
    call_id, job_ref, polls = payload["call_id"], payload["job_ref"], payload.get("polls", 0)
    try:
        transcript = get_adapter().fetch_transcript(job_ref)
    except Exception as e:  # noqa: BLE001 — terminal transcription failure
        _fail_transcribe(call_id, str(e))
        return

    if transcript is None:
        if polls >= MAX_POLLS:
            _fail_transcribe(call_id, f"transcription {job_ref} did not complete in time")
            return
        enqueue(
            "poll_transcription",
            {"call_id": call_id, "job_ref": job_ref, "polls": polls + 1},
            delay_s=POLL_INTERVAL_S,
        )
        return
    deliver_transcript(call_id, transcript)


def _fail_transcribe(call_id: str, reason: str) -> None:
    with get_session() as session:
        run = session.scalars(
            select(Run).where(Run.call_id == call_id).order_by(Run.created_at.desc())
        ).first()
        if run is None:
            return
        stages = [dict(s) for s in run.stages]
        for s in stages:
            if s["name"] == "transcribe":
                s["status"] = "failed"
                s["error"] = reason
        run.stages = stages
        run.status = "failed"
        session.commit()


@handler("run_insights")
def run_insights(payload: dict) -> None:
    call_id = payload["call_id"]
    with get_session() as session:
        call = session.get(Call, call_id)
        if call is None or call.transcript is None:
            raise ValueError(f"call {call_id} has no transcript")
        run = session.scalars(
            select(Run).where(Run.call_id == call_id).order_by(Run.created_at.desc())
        ).first()
        lines = call.transcript.lines
        run_id = run.id

    rs = RunState()
    rs._get("transcribe").status = "ok"  # done upstream via webhook
    rs._get("transcribe").attempts = 1

    # Prettify raw STT for readability (non-fatal, skips already-clean text).
    # Runs before insights so the cleaned text is what quotes cite and the UI
    # shows. Its cost folds into the transcribe stage.
    try:
        pretty, fmt_cost = prettify_transcript(lines)
        if pretty is not lines:
            update_transcript_lines(call_id, pretty)
            lines = pretty
        rs._get("transcribe").cost_usd += fmt_cost
    except Exception:
        pass  # keep raw lines

    insights: dict = {}

    def staged(stage_name: str, fn):
        """Run an LLM step; record its cost against the budget."""

        def wrapped():
            result, cost = fn()
            rs.charge(stage_name, cost)
            return result

        return rs.execute(stage_name, wrapped)

    try:
        # 1. intent → pack; on failure fall back to the sales pack rather than
        # killing the run (intent only picks which pack to use)
        try:
            intent = staged("detect_intent", lambda: detect_intent(lines))
        except StageFailed:
            intent = {"value": "sales", "confidence": 0.0, "evidence": [], "fallback": True}
        insights["intent"] = intent
        pack = select_pack(intent.get("value"))

        # 2. extraction
        raw_extraction = staged("extract", lambda: extract(lines, pack))

        # 3. evidence gate — pure code, no LLM, cannot be skipped
        cleaned, dropped = rs.execute(
            "validate", lambda: validate_extraction(raw_extraction, lines)
        )
        rs._get("validate").dropped_claims = len(dropped)
        insights["summary"] = cleaned.get("summary", [])
        insights["objections"] = cleaned.get("objections", [])
        insights["next_steps"] = cleaned.get("next_steps", [])
        insights["dropped_claims"] = dropped

        # 4. scorecard (deterministic in code + one judgment LLM call)
        insights["scorecard"] = staged("score", lambda: score(lines, pack, cleaned))

        # 5. compliance — non-critical, in-house fallback (see compliance.py
        # for why: PyAI Trace needs a paid add-on most orgs don't have).
        # Never blocks the run; absence just means no compliance panel.
        compliance = None
        try:
            compliance = staged("compliance", lambda: run_compliance_check(lines))
        except StageFailed:
            pass

        # 6. follow-up email — non-critical; failure means partial, not dead
        try:
            insights["follow_up_email"] = staged(
                "compose_email", lambda: compose_email(lines, insights)
            )
        except StageFailed:
            insights["follow_up_email"] = None

    except BudgetExceeded as e:
        # attribute the breach, skip the rest, finalize cleanly
        for s in rs.stages:
            if s.status == "pending":
                s.status = "skipped"
        _persist(run_id, rs, insights, note=str(e))
        return
    except StageFailed as e:
        rs.skip_remaining(e.stage)
        _persist(run_id, rs, insights)
        return

    _persist(run_id, rs, insights, compliance=compliance)


def _persist(run_id: str, rs: RunState, insights: dict, note: str | None = None, compliance: dict | None = None) -> None:
    with get_session() as session:
        run = session.get(Run, run_id)
        run.stages = rs.as_dicts()
        run.status = rs.final_status()
        run.insights = insights or None
        run.compliance = compliance
        run.cost_usd = round(rs.spent, 4)
        run.finished_at = datetime.now(timezone.utc)
        if note:
            stages = [dict(s) for s in run.stages]
            for s in stages:
                if s["status"] == "failed" and not s.get("error"):
                    s["error"] = note
            run.stages = stages
        session.commit()
