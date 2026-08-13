"""Exports (Markdown / JSON) and share links.

Exports render the effective insights (human edits if present, else AI output).
Share links freeze a snapshot at creation time — later edits don't change an
already-shared page — and are revocable. Shared snapshots exclude the raw
transcript and the internal compliance panel.
"""

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from ..db import get_session
from ..models import Call, Run, ShareLink
from ..render import effective_insights, export_json, share_snapshot, to_markdown

router = APIRouter(tags=["share"])


def _call_and_run(session, call_id: str) -> tuple[Call, Run]:
    call = session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, "call not found")
    run = session.scalars(
        select(Run).where(Run.call_id == call_id).order_by(Run.created_at.desc())
    ).first()
    if run is None or run.insights is None:
        raise HTTPException(409, "call has no insights to export yet")
    return call, run


@router.get("/api/calls/{call_id}/export.md", response_class=PlainTextResponse)
def export_markdown(call_id: str, transcript: bool = False) -> str:
    with get_session() as session:
        call, run = _call_and_run(session, call_id)
        ins = effective_insights(run)
        return to_markdown(call, run, ins, include_transcript=transcript, transcript=call.transcript)


@router.get("/api/calls/{call_id}/export.json")
def export_call_json(call_id: str) -> dict:
    with get_session() as session:
        call, run = _call_and_run(session, call_id)
        return export_json(call, run, effective_insights(run))


@router.post("/api/calls/{call_id}/share")
def create_share(call_id: str) -> dict:
    with get_session() as session:
        call, run = _call_and_run(session, call_id)
        snap = share_snapshot(call, run, effective_insights(run))
        link = ShareLink(run_id=run.id, content_snapshot=snap)
        session.add(link)
        session.commit()
        return {"token": link.token, "url": f"/share/{link.token}"}


@router.get("/api/share/{token}")
def get_share(token: str) -> dict:
    with get_session() as session:
        link = session.get(ShareLink, token)
        if link is None or link.revoked:
            raise HTTPException(404, "share link not found or revoked")
        return {"snapshot": link.content_snapshot, "created_at": link.created_at.isoformat()}


@router.post("/api/share/{token}/revoke")
def revoke_share(token: str) -> dict:
    with get_session() as session:
        link = session.get(ShareLink, token)
        if link is None:
            raise HTTPException(404, "share link not found")
        link.revoked = True
        session.commit()
        return {"ok": True, "revoked": True}
