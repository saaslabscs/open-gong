"""API tests (DB-backed) and fixture invariants."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

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
