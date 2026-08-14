"""The guarantee: a summary on every call, with no agent configured at all.

66da4e2 made summaries conditional on an orchestrator dispatching an agent that
owned a relevant skill, so they silently stopped appearing. These tests are the
regression fence.
"""

import json

from fastapi.testclient import TestClient

from app import insights, pipeline
from app.jobs import run_due_jobs
from app.main import app
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
