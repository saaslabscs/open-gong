"""The guarantee: a summary on every call, with no agent configured at all.

66da4e2 made summaries conditional on an orchestrator dispatching an agent that
owned a relevant skill, so they silently stopped appearing. These tests are the
regression fence.
"""

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import insights, pipeline
from app.db import get_session
from app.jobs import run_due_jobs
from app.main import app
from app.models import Run
from fakes import fake_llm

WAV = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00" + b"\x00" * 20


def _drain():
    for _ in range(10):
        if run_due_jobs() == 0:
            return


def _ingest(c) -> str:
    return c.post("/api/ingest/upload", files={"file": ("a.wav", WAV, "audio/wav")}).json()["call_id"]


def test_summary_and_email_ship_with_no_agents_configured(monkeypatch):
    monkeypatch.setattr(
        insights.llm,
        "complete_json",
        fake_llm({"extract": {"summary": [], "objections": [], "next_steps": []}}),
    )
    with TestClient(app) as c:
        call_id = _ingest(c)
        _drain()
        detail = c.get(f"/api/calls/{call_id}").json()

    assert detail["run"]["status"] == "shipped"
    names = [s["name"] for s in detail["run"]["stages"]]
    assert names == ["transcribe", "summarize", "compose_email"]
    assert all(s["status"] == "ok" for s in detail["run"]["stages"])


def test_summarize_failure_fails_the_run(monkeypatch):
    monkeypatch.setattr(
        insights.llm, "complete_json", fake_llm({"extract": RuntimeError("model down")})
    )
    with TestClient(app) as c:
        call_id = _ingest(c)
        _drain()
        detail = c.get(f"/api/calls/{call_id}").json()

    assert detail["run"]["status"] == "failed"
    stage = next(s for s in detail["run"]["stages"] if s["name"] == "summarize")
    assert stage["status"] == "failed"
    assert stage["attempts"] == 3


def test_email_failure_leaves_the_run_partial(monkeypatch):
    monkeypatch.setattr(
        insights.llm,
        "complete_json",
        fake_llm({
            "extract": {"summary": [], "objections": [], "next_steps": []},
            "email": RuntimeError("model down"),
        }),
    )
    with TestClient(app) as c:
        call_id = _ingest(c)
        _drain()
        detail = c.get(f"/api/calls/{call_id}").json()

    assert detail["run"]["status"] == "partial"
    assert next(s for s in detail["run"]["stages"] if s["name"] == "summarize")["status"] == "ok"


def test_dropped_claims_make_the_run_partial(monkeypatch):
    """An unproven claim is removed and the run says it needs review."""
    monkeypatch.setattr(
        insights.llm,
        "complete_json",
        fake_llm({"extract": {
            "summary": [{"text": "invented", "evidence": [{"quote": "never said", "line": 1}]}],
            "objections": [],
            "next_steps": [],
        }}),
    )
    with TestClient(app) as c:
        call_id = _ingest(c)
        _drain()
        detail = c.get(f"/api/calls/{call_id}").json()

    assert detail["run"]["status"] == "partial"


def test_retry_does_not_compound_run_cost(monkeypatch):
    """A retry re-enters run_insights for the same run_id (see
    api/review.py's retry endpoint, which never touches run.cost_usd itself).
    Every cost write inside run_insights is additive, so the total must be
    zeroed once on entry — otherwise a retried run's cost keeps adding the
    new attempt's cost on top of the old one forever.
    """
    monkeypatch.setattr(
        insights.llm,
        "complete_json",
        fake_llm({"extract": {"summary": [], "objections": [], "next_steps": []}}),
    )
    with TestClient(app) as c:
        call_id = _ingest(c)
        _drain()

    with get_session() as session:
        run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
        run_id = run.id
        first_attempt_cost = run.cost_usd
    assert first_attempt_cost > 0

    pipeline.run_insights({"call_id": call_id})  # re-enters the same run_id, as a retry does

    with get_session() as session:
        run = session.get(Run, run_id)
        assert run.cost_usd == first_attempt_cost  # one attempt's cost, not two
