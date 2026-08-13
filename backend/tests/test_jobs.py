"""Job queue guarantees: durable retries with backoff, reasons attached, stuck-sweep."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.db as db
from app.db import Base


@pytest.fixture(autouse=True)
def memory_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    Base.metadata.create_all(engine)
    yield


from app import jobs  # noqa: E402 — imported after Base so Job is registered


def get(job_id):
    with db.get_session() as s:
        return s.get(jobs.Job, job_id)


def test_job_runs_and_completes():
    seen = []
    jobs._handlers["t_ok"] = lambda p: seen.append(p["x"])
    job_id = jobs.enqueue("t_ok", {"x": 1})
    assert jobs.run_due_jobs() == 1
    assert seen == [1]
    assert get(job_id).status == "done"


def test_failed_job_retries_with_reason_then_fails():
    jobs._handlers["t_bad"] = lambda p: (_ for _ in ()).throw(ValueError("boom"))
    job_id = jobs.enqueue("t_bad", {})

    jobs.run_due_jobs()
    job = get(job_id)
    assert job.status == "queued" and job.attempts == 1
    assert "boom" in job.last_error
    assert job.next_run_at > datetime.now(timezone.utc).replace(tzinfo=job.next_run_at.tzinfo)

    # force due and exhaust the cap
    for _ in range(jobs.MAX_JOB_ATTEMPTS - 1):
        with db.get_session() as s:
            j = s.get(jobs.Job, job_id)
            j.next_run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            s.commit()
        jobs.run_due_jobs()

    job = get(job_id)
    assert job.status == "failed"
    assert job.attempts == jobs.MAX_JOB_ATTEMPTS
    assert "boom" in job.last_error


def test_sweep_requeues_stuck_running_jobs():
    jobs._handlers["t_stuck"] = lambda p: None
    job_id = jobs.enqueue("t_stuck", {})
    with db.get_session() as s:
        j = s.get(jobs.Job, job_id)
        j.status = "running"
        j.attempts = 1
        j.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
        s.commit()

    assert jobs.sweep_stuck(max_running_minutes=30) == 1
    job = get(job_id)
    assert job.status == "queued"
    assert "swept" in job.last_error
