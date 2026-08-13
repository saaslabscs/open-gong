"""Agent CRUD + skill attach/detach. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §4.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..db import get_session
from ..models import Agent, AgentSkill, Skill

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _serialize(a: Agent, skills: list[Skill] | None = None) -> dict:
    out = {
        "id": a.id, "name": a.name, "description": a.description,
        "system_prompt": a.system_prompt, "enabled": a.enabled,
    }
    if skills is not None:
        out["skills"] = [{"id": s.id, "name": s.name} for s in skills]
    return out


class AgentCreate(BaseModel):
    name: str
    description: str
    system_prompt: str


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    enabled: bool | None = None


@router.post("")
def create_agent(body: AgentCreate) -> dict:
    with get_session() as session:
        agent = Agent(name=body.name, description=body.description, system_prompt=body.system_prompt)
        session.add(agent)
        session.commit()
        return _serialize(agent)


@router.get("")
def list_agents() -> list[dict]:
    with get_session() as session:
        agents = session.scalars(select(Agent).order_by(Agent.created_at)).all()
        return [_serialize(a) for a in agents]


def _skills_for(session, agent_id: str) -> list[Skill]:
    links = session.scalars(select(AgentSkill).where(AgentSkill.agent_id == agent_id)).all()
    if not links:
        return []
    skill_ids = [l.skill_id for l in links]
    return session.scalars(select(Skill).where(Skill.id.in_(skill_ids))).all()


@router.get("/{agent_id}")
def get_agent(agent_id: str) -> dict:
    with get_session() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(404, "agent not found")
        return _serialize(agent, _skills_for(session, agent_id))


@router.patch("/{agent_id}")
def update_agent(agent_id: str, body: AgentUpdate) -> dict:
    with get_session() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(404, "agent not found")
        if body.name is not None:
            agent.name = body.name
        if body.description is not None:
            agent.description = body.description
        if body.system_prompt is not None:
            agent.system_prompt = body.system_prompt
        if body.enabled is not None:
            agent.enabled = body.enabled
        session.commit()
        return _serialize(agent, _skills_for(session, agent_id))


@router.delete("/{agent_id}")
def delete_agent(agent_id: str) -> dict:
    with get_session() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(404, "agent not found")
        for link in session.scalars(select(AgentSkill).where(AgentSkill.agent_id == agent_id)).all():
            session.delete(link)
        session.delete(agent)
        session.commit()
        return {"ok": True}


class SkillAttach(BaseModel):
    skill_id: str


@router.post("/{agent_id}/skills")
def attach_skill(agent_id: str, body: SkillAttach) -> dict:
    with get_session() as session:
        agent = session.get(Agent, agent_id)
        skill = session.get(Skill, body.skill_id)
        if agent is None:
            raise HTTPException(404, "agent not found")
        if skill is None:
            raise HTTPException(404, "skill not found")
        existing = session.scalars(
            select(AgentSkill).where(AgentSkill.agent_id == agent_id, AgentSkill.skill_id == body.skill_id)
        ).first()
        if existing is None:
            session.add(AgentSkill(agent_id=agent_id, skill_id=body.skill_id))
            session.commit()
        return {"ok": True}


@router.delete("/{agent_id}/skills/{skill_id}")
def detach_skill(agent_id: str, skill_id: str) -> dict:
    with get_session() as session:
        link = session.scalars(
            select(AgentSkill).where(AgentSkill.agent_id == agent_id, AgentSkill.skill_id == skill_id)
        ).first()
        if link:
            session.delete(link)
            session.commit()
        return {"ok": True}
