"""Skill CRUD + .md upload. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §2, §4.
"""

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from ..db import get_session
from ..models import AgentSkill, Skill
from ..skills.loader import SkillParseError, parse_skill_md

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _serialize(s: Skill) -> dict:
    return {
        "id": s.id, "name": s.name, "description": s.description, "when_to_use": s.when_to_use,
        "body_md": s.body_md, "fields": s.fields, "source": s.source, "version": s.version,
    }


class SkillCreate(BaseModel):
    name: str
    description: str
    when_to_use: str
    body_md: str
    fields: dict | None = None


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    when_to_use: str | None = None
    body_md: str | None = None
    fields: dict | None = None


@router.post("")
def create_skill(body: SkillCreate) -> dict:
    with get_session() as session:
        skill = Skill(
            name=body.name, description=body.description, when_to_use=body.when_to_use,
            body_md=body.body_md, fields=body.fields, source="ui",
        )
        session.add(skill)
        session.commit()
        return _serialize(skill)


@router.get("")
def list_skills() -> list[dict]:
    with get_session() as session:
        skills = session.scalars(select(Skill).order_by(Skill.created_at)).all()
        return [_serialize(s) for s in skills]


@router.get("/{skill_id}")
def get_skill(skill_id: str) -> dict:
    with get_session() as session:
        skill = session.get(Skill, skill_id)
        if skill is None:
            raise HTTPException(404, "skill not found")
        return _serialize(skill)


@router.patch("/{skill_id}")
def update_skill(skill_id: str, body: SkillUpdate) -> dict:
    with get_session() as session:
        skill = session.get(Skill, skill_id)
        if skill is None:
            raise HTTPException(404, "skill not found")
        if body.name is not None:
            skill.name = body.name
        if body.description is not None:
            skill.description = body.description
        if body.when_to_use is not None:
            skill.when_to_use = body.when_to_use
        if body.body_md is not None:
            skill.body_md = body.body_md
        if body.fields is not None:
            skill.fields = body.fields
        skill.version += 1
        session.commit()
        return _serialize(skill)


@router.delete("/{skill_id}")
def delete_skill(skill_id: str) -> dict:
    with get_session() as session:
        skill = session.get(Skill, skill_id)
        if skill is None:
            raise HTTPException(404, "skill not found")
        # Drop the agent attachments first — left behind they are dangling FK
        # rows (silent orphans on SQLite, an IntegrityError on Postgres) that
        # would also make every attached agent's skill list unresolvable.
        # Mirrors delete_agent's cleanup in api/agents.py.
        for link in session.scalars(select(AgentSkill).where(AgentSkill.skill_id == skill_id)).all():
            session.delete(link)
        session.delete(skill)
        session.commit()
        return {"ok": True}


@router.post("/upload")
async def upload_skill(file: UploadFile) -> dict:
    content = (await file.read()).decode("utf-8")
    try:
        parsed = parse_skill_md(content)
    except SkillParseError as e:
        raise HTTPException(422, str(e)) from e

    with get_session() as session:
        skill = Skill(
            name=parsed["name"], description=parsed["description"], when_to_use=parsed["when_to_use"],
            body_md=parsed["body"], fields=parsed["fields"], source="upload",
        )
        session.add(skill)
        session.commit()
        return _serialize(skill)
