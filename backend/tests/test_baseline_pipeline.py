"""The guarantee: a summary on every call, with no agent configured at all.

66da4e2 made summaries conditional on an orchestrator dispatching an agent that
owned a relevant skill, so they silently stopped appearing. These tests are the
regression fence.
"""

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

import app.llm as llm_mod
from app import insights, pipeline
from app.agent_runtime import new_agent_run_state
from app.db import get_session
from app.jobs import run_due_jobs
from app.main import app
from app.models import Call, Run, Transcript
from fakes import fake_llm
from test_ingest import WAV_BYTE_RATE, _serve, _wav

WAV = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00" + b"\x00" * 20


def _drain():
    for _ in range(10):
        if run_due_jobs() == 0:
            return


def _ingest(c) -> str:
    return c.post("/api/ingest/upload", files={"file": ("a.wav", WAV, "audio/wav")}).json()["call_id"]


def _latest_run_id(call_id: str) -> str:
    with get_session() as session:
        return session.scalars(select(Run).where(Run.call_id == call_id)).first().id


def _make_retryable(call_id: str, summarize_status: str = "ok") -> None:
    """Put the run in the state POST /retry accepts, with `summarize` left in
    the given state — `ok` is what a partial run (failed agent, dropped claim,
    failed email) actually looks like."""
    with get_session() as session:
        run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
        stages = [dict(s) for s in run.stages]
        for s in stages:
            if s["name"] == "summarize":
                s.update({"status": summarize_status})
                if summarize_status == "failed":
                    s.update({"attempts": 3, "error": "model down"})
        run.stages = stages
        run.status = "partial"
        session.commit()


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


GOOD_SUMMARY = {
    "summary": [{"text": "Customer asked about upgrading before month end.",
                 "evidence": [{"quote": "upgrading our plan before the end of the month", "line": 2}]}],
    "objections": [],
    "next_steps": [],
}


def test_partial_retry_reuses_a_summary_that_already_succeeded(monkeypatch):
    """POST /retry accepts `partial`, and a partial run's `summarize` is
    normally already `ok` (a failed agent, a failed email, a dropped claim).
    Re-running it pays for a second summary and is the mechanism that could
    replace good insights with a failed attempt's nothing."""
    monkeypatch.setattr(insights.llm, "complete_json", fake_llm({"extract": GOOD_SUMMARY}))
    with TestClient(app) as c:
        call_id = _ingest(c)
        _drain()
        original = c.get(f"/api/calls/{call_id}").json()["insights"]
        assert original["summary"]

        _make_retryable(call_id)
        # Any further summarize/compose_email call is the bug under test.
        monkeypatch.setattr(
            insights.llm, "complete_json",
            fake_llm({"extract": AssertionError("summarize must not run twice"),
                      "email": AssertionError("compose_email must not run twice")}),
        )
        c.post(f"/api/calls/{call_id}/retry")
        _drain()
        detail = c.get(f"/api/calls/{call_id}").json()

    assert detail["insights"] == original
    summarize = next(s for s in detail["run"]["stages"] if s["name"] == "summarize")
    assert summarize == {"name": "summarize", "status": "ok", "attempts": 1,
                         "cost_usd": 0.02, "error": None}  # one attempt's worth, not two
    assert detail["run"]["status"] == "shipped"


def test_a_failed_retry_never_wipes_the_stored_summary(monkeypatch):
    """Even when `summarize` does re-run and fails all three attempts, the
    insights the user already had must survive — the same protection this
    pipeline already gives AgentRun.edited_output."""
    monkeypatch.setattr(insights.llm, "complete_json", fake_llm({"extract": GOOD_SUMMARY}))
    with TestClient(app) as c:
        call_id = _ingest(c)
        _drain()
        original = c.get(f"/api/calls/{call_id}").json()["insights"]

        _make_retryable(call_id, summarize_status="failed")
        monkeypatch.setattr(
            insights.llm, "complete_json", fake_llm({"extract": RuntimeError("model down")})
        )
        c.post(f"/api/calls/{call_id}/retry")
        _drain()
        detail = c.get(f"/api/calls/{call_id}").json()

    assert detail["run"]["status"] == "failed"  # the failure is still reported
    assert detail["insights"] == original  # ...but the notes are not destroyed


def test_prettify_runs_before_the_summary_it_is_cited_against(monkeypatch):
    """The rail displays the prettified lines and `jumpTo(line)` scrolls to
    them, so `summarize` must verify its quotes against that same text. If
    prettify ran afterwards, a stored quote could be absent from the line the
    reader is shown — in a product whose whole pitch is verifiable receipts."""
    raw = [
        {"line": 1, "speaker": "Speaker 1", "text": "hi tom this call is recorded is that okay"},
        {"line": 2, "speaker": "Speaker 2", "text": "yeah thats fine go ahead"},
    ]
    pretty = [
        {"line": 1, "speaker": "Tom", "text": "Hi Tom, this call is recorded. Is that okay?"},
        {"line": 2, "speaker": "Ana", "text": "Yeah, that's fine — go ahead."},
    ]
    seen: dict[str, str] = {}

    def fake(system, user, schema, max_tokens=2000):
        if "Reformat this raw call transcript" in user:
            return {"lines": pretty}, 0.003
        if "Extract the following" in user:
            seen["summarize"] = user
            return {
                "summary": [{"text": "Recording was disclosed.",
                             "evidence": [{"quote": "this call is recorded. Is that okay?", "line": 1}]}],
                "objections": [], "next_steps": [],
            }, 0.02
        if "follow-up email" in user:
            return {"subject": "s", "body": "b"}, 0.004
        raise AssertionError(f"unexpected prompt: {user[:60]}")

    monkeypatch.setattr(llm_mod, "complete_json", fake)

    with get_session() as session:
        call = Call(title="t", source="upload", external_id="pretty-order")
        session.add(call)
        session.flush()
        session.add(Transcript(call_id=call.id, lines=raw))
        session.add(Run(call_id=call.id, status="running", stages=[
            {"name": "transcribe", "status": "ok", "attempts": 1, "cost_usd": 0.0, "error": None},
        ]))
        session.commit()
        call_id = call.id

    pipeline.run_insights({"call_id": call_id})

    assert "Hi Tom, this call is recorded. Is that okay?" in seen["summarize"]
    assert "hi tom this call is recorded is that okay" not in seen["summarize"]

    with get_session() as session:
        call = session.get(Call, call_id)
        run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
        # The claim's quote verifies against the very lines the rail renders.
        assert call.transcript.lines == pretty
        quote = run.insights["summary"][0]["evidence"][0]
        assert quote["quote"] in call.transcript.lines[quote["line"] - 1]["text"]
        # prettify 0.003 + summarize 0.02 + email 0.004; dispatch is free with
        # no agents configured. The prettify cost is folded into the run.
        assert run.cost_usd == 0.027


def test_a_human_title_is_never_rewritten():
    """_derive_title replaces bare recording ids only, and the write is
    irreversible in the UI — a tweak to _ID_TITLE must not start rewriting
    real titles."""
    with get_session() as session:
        call = Call(title="Discovery call — Brightline Logistics", source="sample", external_id="title-noop")
        session.add(call)
        session.flush()
        session.add(Run(call_id=call.id, status="running", stages=[]))
        session.commit()
        call_id, run_id = call.id, call.runs[0].id

    rs = new_agent_run_state(["summarize"])
    rs._get("summarize").status = "ok"
    pipeline._persist_baseline(run_id, rs, {"summary": [{"text": "Brightline wants a pilot.", "evidence": []}]})

    with get_session() as session:
        assert session.get(Call, call_id).title == "Discovery call — Brightline Logistics"


def test_identifier_titles_are_replaced_after_summarizing(monkeypatch, url_ingest):
    # The evidence quote must actually verify against the mock adapter's CANNED
    # transcript (app/adapters/pyai/mock.py) — the URL-ingested audio's hash-derived
    # filename never matches a fixtures/samples/*.json stem, so CANNED is what's
    # transcribed here, and the evidence gate would otherwise drop this claim,
    # leaving summary empty and defeating the point of the test.
    monkeypatch.setattr(
        insights.llm,
        "complete_json",
        fake_llm({"extract": {
            "summary": [{"text": "Caller asked to be transferred to billing.",
                         "evidence": [{"quote": "thanks for calling", "line": 1}]}],
            "objections": [], "next_steps": [],
        }}),
    )
    url_ingest(_serve(_wav(WAV_BYTE_RATE * 3), honor_range=True))
    with TestClient(app) as c:
        call_id = c.post(
            "/api/ingest/url", json={"url": "https://x.test/stream/CA1/RE0123456789abcdef0123456789abcdef"}
        ).json()["call_id"]
        _drain()
        assert c.get(f"/api/calls/{call_id}").json()["call"]["title"] != "RE0123456789abcdef0123456789abcdef"
