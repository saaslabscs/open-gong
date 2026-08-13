"""Review actions: edit insights before sharing, and retry a failed/partial run.

Edits are stored in Run.edited_insights (the original AI output stays intact).
Retry re-enqueues the right stage: full reprocess if transcription never
completed, else just the insight chain.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..db import get_session
from ..jobs import enqueue
from ..models import Call, Run

router = APIRouter(prefix="/api/calls", tags=["review"])


def _latest_run(session, call_id: str) -> Run | None:
    return session.scalars(
        select(Run).where(Run.call_id == call_id).order_by(Run.created_at.desc())
    ).first()


class InsightEdit(BaseModel):
    # the whole insights blob as edited in the UI; stored verbatim as the
    # human-authored override. Original AI output is preserved separately.
    insights: dict


@router.patch("/{call_id}/insights")
def edit_insights(call_id: str, body: InsightEdit) -> dict:
    with get_session() as session:
        run = _latest_run(session, call_id)
        if run is None:
            raise HTTPException(404, "no run for this call")
        if run.insights is None:
            raise HTTPException(409, "run has no insights to edit yet")
        run.edited_insights = body.insights
        session.commit()
        return {"ok": True, "edited": True}


@router.post("/{call_id}/insights/reset")
def reset_insights(call_id: str) -> dict:
    """Discard human edits, revert to the original AI output."""
    with get_session() as session:
        run = _latest_run(session, call_id)
        if run is None:
            raise HTTPException(404, "no run for this call")
        run.edited_insights = None
        session.commit()
        return {"ok": True, "edited": False}


@router.post("/{call_id}/retry")
def retry(call_id: str) -> dict:
    with get_session() as session:
        call = session.get(Call, call_id)
        if call is None:
            raise HTTPException(404, "call not found")
        run = _latest_run(session, call_id)
        if run is None:
            raise HTTPException(404, "no run for this call")
        if run.status not in ("failed", "partial"):
            raise HTTPException(409, f"run is {run.status}, nothing to retry")

        transcribed = call.transcript is not None
        # reset stage bookkeeping so retried stages start clean
        stages = [dict(s) for s in run.stages]
        for s in stages:
            if s["status"] in ("failed", "skipped"):
                s.update({"status": "pending", "attempts": 0, "error": None})
        run.stages = stages
        run.status = "running"
        session.commit()

    if transcribed:
        enqueue("run_insights", {"call_id": call_id})
    else:
        enqueue("process_call", {"call_id": call_id})
    return {"ok": True, "from_stage": "run_insights" if transcribed else "process_call"}
