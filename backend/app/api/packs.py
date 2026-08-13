"""Custom insight packs: compile from prose, review/edit, activate.

An active custom pack overrides the built-in sales/support pack for new runs.
Activating one pack retires any other active pack (one active at a time in
Phase 1). Editing a compiled pack is expected — the compiler drafts, the human
approves.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..db import get_session
from ..models import InsightPack
from ..pack_compiler import compile_pack

router = APIRouter(prefix="/api/packs", tags=["packs"])


def _serialize(p: InsightPack) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "version": p.version,
        "status": p.status,
        "instructions": p.instructions,
        "json_schema": p.json_schema,
        "scoring_spec": p.scoring_spec,
    }


class CompileReq(BaseModel):
    instructions: str


@router.post("/compile")
def compile_(body: CompileReq) -> dict:
    if not body.instructions.strip():
        raise HTTPException(422, "instructions are empty")
    pack, _cost = compile_pack(body.instructions)
    with get_session() as session:
        row = InsightPack(
            name=pack["name"],
            version=1,
            status="draft",
            instructions=body.instructions,
            json_schema=pack["json_schema"],
            scoring_spec=pack["scoring_spec"],
        )
        session.add(row)
        session.commit()
        return _serialize(row)


@router.get("")
def list_packs() -> list[dict]:
    with get_session() as session:
        rows = session.scalars(
            select(InsightPack).where(InsightPack.status != "retired").order_by(InsightPack.created_at.desc())
        ).all()
        return [_serialize(p) for p in rows]


class EditReq(BaseModel):
    name: str | None = None
    json_schema: dict | None = None
    scoring_spec: dict | None = None


@router.patch("/{pack_id}")
def edit_pack(pack_id: str, body: EditReq) -> dict:
    with get_session() as session:
        p = session.get(InsightPack, pack_id)
        if p is None:
            raise HTTPException(404, "pack not found")
        if p.status == "active":
            raise HTTPException(409, "activate a fresh draft to change an active pack")
        if body.name is not None:
            p.name = body.name
        if body.json_schema is not None:
            p.json_schema = body.json_schema
        if body.scoring_spec is not None:
            p.scoring_spec = body.scoring_spec
        session.commit()
        return _serialize(p)


@router.post("/{pack_id}/activate")
def activate(pack_id: str) -> dict:
    with get_session() as session:
        p = session.get(InsightPack, pack_id)
        if p is None:
            raise HTTPException(404, "pack not found")
        # one active pack at a time — retire the others
        for other in session.scalars(select(InsightPack).where(InsightPack.status == "active")).all():
            other.status = "retired"
        p.status = "active"
        session.commit()
        return _serialize(p)


@router.post("/deactivate")
def deactivate() -> dict:
    """Fall back to the built-in sales/support packs."""
    with get_session() as session:
        for p in session.scalars(select(InsightPack).where(InsightPack.status == "active")).all():
            p.status = "retired"
        session.commit()
    return {"ok": True}
