"""The five Phase-1 entities: Call, Transcript, Run (+stages inline), InsightPack, ShareLink.

JSON columns hold the inherently flexible shapes (transcript lines, insights,
pack schemas). SQLite and Postgres both support these via SQLAlchemy's JSON type.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String, default="Untitled call")
    source: Mapped[str] = mapped_column(String)  # upload | url | sample
    external_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)  # idempotency
    audio_path: Mapped[str | None] = mapped_column(String, nullable=True)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    transcript: Mapped["Transcript | None"] = relationship(back_populates="call", uselist=False)
    runs: Mapped[list["Run"]] = relationship(back_populates="call", order_by="Run.created_at")


class Transcript(Base):
    __tablename__ = "transcripts"

    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"), primary_key=True)
    language: Mapped[str] = mapped_column(String, default="en")
    # [{"line": 1, "speaker": "...", "text": "..."}] — the anchor space for all evidence
    lines: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    call: Mapped[Call] = relationship(back_populates="transcript")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"))
    # pending | running | shipped | partial | failed — aggregate across this call's AgentRuns
    status: Mapped[str] = mapped_column(String, default="pending")
    # [{"name","status","attempts","cost_usd","error"}] — transcribe-stage bookkeeping only;
    # per-skill steps live on AgentRun.steps now
    stages: Mapped[list] = mapped_column(JSON, default=list)
    # {summary, objections, next_steps, follow_up_email, dropped_claims} — the guaranteed
    # baseline's output, written before agent dispatch. Independent of AgentRun.output.
    insights: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    orchestrator_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    call: Mapped[Call] = relationship(back_populates="runs")


class InsightPack(Base):
    __tablename__ = "insight_packs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String)  # e.g. "sales-default"
    version: Mapped[int] = mapped_column(Integer, default=1)
    # draft | active | retired
    status: Mapped[str] = mapped_column(String, default="draft")
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)  # the prose, if compiled
    json_schema: Mapped[dict] = mapped_column(JSON)
    scoring_spec: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ShareLink(Base):
    __tablename__ = "share_links"

    token: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    # frozen at share time; deliberately excludes transcript audio + compliance
    content_snapshot: Mapped[dict] = mapped_column(JSON)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)  # what the orchestrator sees
    system_prompt: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # at most one Agent may have this set — enforced in api/agents.py, not the DB.
    # When set, this agent's system_prompt is used as the dispatch-decision prompt
    # instead of the generic default, and this agent is excluded from the pool of
    # agents dispatch() can select (its job is routing, not producing call notes).
    is_orchestrator: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    when_to_use: Mapped[str] = mapped_column(Text)
    body_md: Mapped[str] = mapped_column(Text)
    # {"checks": [...], "scores": [{"name","max"}], "claims": [...]} or None (narrative-only)
    fields: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String)  # ui | upload
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentSkill(Base):
    __tablename__ = "agent_skills"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"))


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    # P3 seam — unset and unread until agent-to-agent invocation (P3) lands
    parent_agent_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending | shipped | partial | failed
    # [{"name","status","attempts","cost_usd","error","dropped_claims"}] — one per skill run
    steps: Mapped[list] = mapped_column(JSON, default=list)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # immutable AI output, keyed by skill name
    edited_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # human review edits
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Why the skill router selected what it did. Without this, an agent run with
    # zero steps is indistinguishable from one that silently did nothing.
    routing_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EntryRule(Base):
    __tablename__ = "entry_rules"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    match_kind: Mapped[str] = mapped_column(String)  # phone_line | source
    match_value: Mapped[str] = mapped_column(String)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
