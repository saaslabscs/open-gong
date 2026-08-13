"""Postgres/SQLite-backed job queue: durable retries without Redis/Celery.

One table, one polling worker. Enqueue writes a row; the worker claims due
jobs, runs the registered handler, and reschedules with backoff on failure.
A per-state timeout sweep catches stuck jobs so nothing hangs silently.
"""

import traceback
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import JSON, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, get_session

MAX_JOB_ATTEMPTS = 3
BACKOFF_SECONDS = [5, 30, 120]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String)  # handler name, e.g. "process_call"
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    # queued | running | done | failed
    status: Mapped[str] = mapped_column(String, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


_handlers: dict[str, Callable[[dict], None]] = {}


def handler(kind: str):
    """Register a job handler: @handler("process_call")."""

    def register(fn: Callable[[dict], None]):
        _handlers[kind] = fn
        return fn

    return register


def enqueue(kind: str, payload: dict, delay_s: float = 0) -> int:
    with get_session() as session:
        job = Job(kind=kind, payload=payload, next_run_at=_now() + timedelta(seconds=delay_s))
        session.add(job)
        session.commit()
        return job.id


def run_due_jobs() -> int:
    """Claim and run every due job once. Returns number of jobs executed.

    Called in a loop by the worker (and directly by tests — deterministic,
    no sleeps inside).
    """
    executed = 0
    while True:
        with get_session() as session:
            job = session.scalars(
                select(Job)
                .where(Job.status == "queued", Job.next_run_at <= _now())
                .order_by(Job.next_run_at)
                .limit(1)
            ).first()
            if job is None:
                return executed
            job.status = "running"
            job.attempts += 1
            session.commit()
            job_id, kind, payload, attempts = job.id, job.kind, job.payload, job.attempts

        try:
            fn = _handlers[kind]
            fn(payload)
            _finish(job_id, "done", None)
        except Exception:  # noqa: BLE001 — reason recorded on the job row
            err = traceback.format_exc(limit=5)
            if attempts >= MAX_JOB_ATTEMPTS:
                _finish(job_id, "failed", err)
            else:
                delay = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
                _reschedule(job_id, err, delay)
        executed += 1


def _finish(job_id: int, status: str, error: str | None) -> None:
    with get_session() as session:
        job = session.get(Job, job_id)
        job.status = status
        job.last_error = error
        session.commit()


def _reschedule(job_id: int, error: str, delay_s: int) -> None:
    with get_session() as session:
        job = session.get(Job, job_id)
        job.status = "queued"
        job.last_error = error
        job.next_run_at = _now() + timedelta(seconds=delay_s)
        session.commit()


def sweep_stuck(max_running_minutes: int = 30) -> int:
    """Requeue jobs stuck in `running` (e.g. worker died mid-job)."""
    cutoff = _now() - timedelta(minutes=max_running_minutes)
    swept = 0
    with get_session() as session:
        stuck = session.scalars(
            select(Job).where(Job.status == "running", Job.updated_at < cutoff)
        ).all()
        for job in stuck:
            job.status = "queued" if job.attempts < MAX_JOB_ATTEMPTS else "failed"
            job.last_error = (job.last_error or "") + "\n[swept: stuck in running]"
            swept += 1
        session.commit()
    return swept


async def worker_loop(poll_interval_s: float = 1.0):
    """The in-process worker; started from the FastAPI lifespan."""
    import asyncio

    sweep_counter = 0
    while True:
        ran = await asyncio.to_thread(run_due_jobs)
        sweep_counter += 1
        if sweep_counter >= 60:
            await asyncio.to_thread(sweep_stuck)
            sweep_counter = 0
        if ran == 0:
            await asyncio.sleep(poll_interval_s)
