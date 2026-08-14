"""API tests (DB-backed) and fixture invariants."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.jobs import run_due_jobs
from app.main import app
from scripts.seed import SAMPLES_DIR, seed


def client() -> TestClient:
    # lifespan auto-seeds when the DB is empty
    return TestClient(app)


def test_healthz():
    with client() as c:
        assert c.get("/healthz").json() == {"ok": True}


def test_list_calls_returns_seeded_samples():
    with client() as c:
        calls = c.get("/api/calls").json()
        assert len(calls) == len(list(SAMPLES_DIR.glob("*.json")))
        for call in calls:
            assert call["run_status"] in {"shipped", "partial", "failed"}


def test_get_call_detail_and_404():
    with client() as c:
        calls = c.get("/api/calls").json()
        detail = c.get(f"/api/calls/{calls[0]['id']}").json()
        assert detail["transcript"]["lines"]
        assert isinstance(detail["agent_runs"], list)
        assert detail["run"]["status"] in {"shipped", "partial", "failed"}
        assert c.get("/api/calls/nope").status_code == 404


def test_seed_is_idempotent():
    n1 = seed()
    n2 = seed()
    assert n1 == n2
    with client() as c:
        assert len(c.get("/api/calls").json()) == n1


def test_call_detail_serves_insights_and_list_serves_agent_count(monkeypatch):
    from app import insights as insights_mod
    from fakes import fake_llm

    monkeypatch.setattr(
        insights_mod.llm,
        "complete_json",
        fake_llm({"extract": {"summary": [], "objections": [], "next_steps": []}}),
    )
    wav = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00" + b"\x00" * 20
    with TestClient(app) as c:
        call_id = c.post("/api/ingest/upload", files={"file": ("a.wav", wav, "audio/wav")}).json()["call_id"]
        for _ in range(10):
            if run_due_jobs() == 0:
                break

        detail = c.get(f"/api/calls/{call_id}").json()
        assert detail["insights"] is not None
        assert "summary" in detail["insights"]
        assert "follow_up_email" in detail["insights"]

        row = next(r for r in c.get("/api/calls").json() if r["id"] == call_id)
        assert row["agent_count"] == 0


def test_seeded_agent_does_not_duplicate_the_guaranteed_baseline():
    """The pipeline itself writes the summary and the follow-up email on every
    call. Linking those two skills to the seeded agent as well made a fresh
    install pay for both twice and show both in both tabs. The Skill rows
    stay, so a user's own links to them keep working."""
    from sqlalchemy import select

    from app.db import get_session
    from app.models import Agent, AgentSkill, Skill

    seed()
    with get_session() as session:
        agent = session.scalars(select(Agent).where(Agent.name == "Call Summarizer")).one()
        linked_ids = {
            l.skill_id
            for l in session.scalars(select(AgentSkill).where(AgentSkill.agent_id == agent.id)).all()
        }
        by_id = {s.id: s.name for s in session.scalars(select(Skill)).all()}

    linked = {by_id[i] for i in linked_ids}
    assert linked == {"sales-scorecard", "support-scorecard", "compliance-check"}
    assert {"summary-and-next-steps", "follow-up-email"} <= set(by_id.values())


def test_seeding_needs_no_api_keys_and_still_shows_notes(monkeypatch):
    """README.md: "Five sample calls ship in the repo with precomputed results
    — no API keys needed". Seeding must therefore call no provider at all;
    regenerating the insights failed `summarize` three times per sample without
    a key and left the whole demo `failed`."""
    import app.llm as llm_mod

    def no_llm(*a, **k):
        raise AssertionError("seeding must not call an LLM")

    monkeypatch.setattr(llm_mod, "complete_json", no_llm)

    n = seed()
    with client() as c:
        rows = c.get("/api/calls").json()
        assert len(rows) == n
        for row in rows:
            assert row["run_status"] in {"shipped", "partial"}
            detail = c.get(f"/api/calls/{row['id']}").json()
            assert detail["insights"]["summary"], f"{row['id']} has no summary"


def test_precutover_sample_insights_survive_every_serializer():
    """Spec test 6 (backward compatibility). The seeded samples carry the
    pre-cutover shape — `intent`, `scorecard`, and on sample-04 a null
    `follow_up_email` — so every database holds such a row. It must serialize
    through the detail endpoint, the Markdown renderer and the JSON export
    without crashing and with its citations intact."""
    with client() as c:
        detail = c.get("/api/calls/sample-04").json()
        ins = detail["insights"]

        # stored verbatim, retired sections and all
        assert ins["intent"]["value"] == "support"
        assert ins["scorecard"]["pack"] == "support-default"
        assert ins["follow_up_email"] is None
        assert detail["run"]["status"] == "partial"  # no email == a failed compose_email
        assert [s["name"] for s in detail["run"]["stages"]] == ["transcribe", "summarize", "compose_email"]

        lines = {l["line"]: l["text"] for l in detail["transcript"]["lines"]}
        cited = ins["summary"][0]["evidence"][0]
        assert cited["quote"] in lines[cited["line"]]

        md = c.get("/api/calls/sample-04/export.md").text
        assert ins["summary"][0]["text"] in md
        assert f"[L{cited['line']}]" in md

        exported = c.get("/api/calls/sample-04/export.json").json()
        assert exported["insights"]["summary"] == ins["summary"]
        assert "intent" not in exported["insights"]  # retired sections filtered on the way out
        assert "scorecard" not in exported["insights"]


# ---------------------------------------------------------------------------
# Fixture invariants: the demo data must satisfy the same guarantees the live
# pipeline enforces ("no proof, no claim").
# ---------------------------------------------------------------------------


def _iter_fixtures():
    for path in SAMPLES_DIR.glob("*.json"):
        yield path, json.loads(Path(path).read_text())


def test_fixture_evidence_invariant():
    for path, data in _iter_fixtures():
        lines = {l["line"]: l["text"] for l in data["transcript"]["lines"]}

        def check(evidence_list, where):
            for ev in evidence_list:
                assert ev["line"] in lines, f"{path.name}: {where} cites missing line {ev['line']}"
                assert ev["quote"] in lines[ev["line"]], (
                    f"{path.name}: {where} quote not found in line {ev['line']}: {ev['quote']!r}"
                )

        ins = data["insights"]
        check(ins["intent"]["evidence"], "intent")
        for i, s in enumerate(ins["summary"]):
            check(s["evidence"], f"summary[{i}]")
        for i, o in enumerate(ins["objections"]):
            check(o["evidence"], f"objections[{i}]")
        for i, n in enumerate(ins["next_steps"]):
            check(n["evidence"], f"next_steps[{i}]")
        for f in ins["scorecard"]["fields"]:
            check(f["evidence"], f"scorecard.{f['name']}")
        if data.get("compliance"):
            for i, finding in enumerate(data["compliance"]["findings"]):
                check(finding["evidence"], f"compliance.findings[{i}]")


def test_fixture_line_numbering():
    for path, data in _iter_fixtures():
        numbers = [l["line"] for l in data["transcript"]["lines"]]
        assert numbers == list(range(1, len(numbers) + 1)), f"{path.name}: lines not sequential"
