"""Review actions: edit an AgentRun's output before sharing, and retry a
failed/partial run.

Edits are stored in AgentRun.edited_output (the original AI output stays
intact). Retry re-enqueues the right stage: full reprocess if transcription
never completed, else just run_insights.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..db import get_session
from ..jobs import enqueue
from ..models import AgentRun, Call, Run

router = APIRouter(prefix="/api/calls", tags=["review"])


def _latest_run(session, call_id: str) -> Run | None:
    return session.scalars(
        select(Run).where(Run.call_id == call_id).order_by(Run.created_at.desc())
    ).first()


class AgentRunEdit(BaseModel):
    output: dict


@router.patch("/{call_id}/agent-runs/{agent_run_id}")
def edit_agent_run(call_id: str, agent_run_id: str, body: AgentRunEdit) -> dict:
    with get_session() as session:
        agent_run = session.get(AgentRun, agent_run_id)
        if agent_run is None or agent_run.call_id != call_id:
            raise HTTPException(404, "no agent run for this call")
        if agent_run.output is None:
            raise HTTPException(409, "agent run has no output to edit yet")
        agent_run.edited_output = body.output
        session.commit()
        return {"ok": True, "edited": True}


@router.post("/{call_id}/agent-runs/{agent_run_id}/reset")
def reset_agent_run(call_id: str, agent_run_id: str) -> dict:
    with get_session() as session:
        agent_run = session.get(AgentRun, agent_run_id)
        if agent_run is None or agent_run.call_id != call_id:
            raise HTTPException(404, "no agent run for this call")
        agent_run.edited_output = None
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
