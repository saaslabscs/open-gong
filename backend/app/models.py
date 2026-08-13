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
    pack_id: Mapped[str | None] = mapped_column(ForeignKey("insight_packs.id"), nullable=True)
    # pending | running | shipped | partial | failed
    status: Mapped[str] = mapped_column(String, default="pending")
    # [{"name","status","attempts","cost_usd","error","dropped_claims"}]
    stages: Mapped[list] = mapped_column(JSON, default=list)
    insights: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # immutable AI output
    # human edits made in review; overrides `insights` for export/share only.
    # kept separate so the original AI output (and its receipts) is never lost.
    edited_insights: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    compliance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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
