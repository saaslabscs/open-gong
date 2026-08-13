"""M4/M5: edit-before-share, retry, exports, and share-link lifecycle."""

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_session
from app.main import app
from app.models import Call, Run, Transcript
from app.run_state import STAGES

LINES = [
    {"line": 1, "speaker": "Ana", "text": "This call is recorded. How can I help?"},
    {"line": 2, "speaker": "Bob", "text": "My invoice was charged twice this month."},
]

INSIGHTS = {
    "intent": {"value": "support", "confidence": 0.9, "evidence": [{"quote": "charged twice", "line": 2}]},
    "summary": [{"text": "Bob was double-charged.", "evidence": [{"quote": "charged twice this month", "line": 2}]}],
    "objections": [],
    "next_steps": [{"text": "Refund the duplicate.", "owner": "Ana", "evidence": [{"quote": "charged twice", "line": 2}]}],
    "follow_up_email": {"subject": "Your refund", "body": "Hi Bob, refund on the way."},
    "scorecard": {"pack": "support-default", "pack_version": 1, "fields": [
        {"name": "issue_identified", "kind": "deterministic", "value": True, "evidence": [{"quote": "charged twice", "line": 2}]},
    ]},
}


import uuid


def _seed(status="shipped", stages=None, transcript=True, insights=INSIGHTS):
    with get_session() as session:
        call = Call(title="Billing call", source="upload", external_id=f"s-{uuid.uuid4().hex}", duration_s=120)
        session.add(call)
        session.flush()
        if transcript:
            session.add(Transcript(call_id=call.id, lines=LINES))
        session.add(Run(
            call_id=call.id, status=status, insights=insights,
            stages=stages or [{"name": s, "status": "ok", "attempts": 1, "cost_usd": 0.0, "error": None} for s in STAGES],
        ))
        session.commit()
        return call.id


def test_edit_then_export_reflects_edits():
    with TestClient(app) as c:
        call_id = _seed()
        edited = json.loads(json.dumps(INSIGHTS))
        edited["follow_up_email"]["subject"] = "Refund confirmed — sorry for the mix-up"
        r = c.patch(f"/api/calls/{call_id}/insights", json={"insights": edited})
        assert r.json()["edited"] is True

        detail = c.get(f"/api/calls/{call_id}").json()
        assert detail["run"]["edited"] is True
        assert detail["insights"]["follow_up_email"]["subject"].startswith("Refund confirmed")

        md = c.get(f"/api/calls/{call_id}/export.md").text
        assert "Refund confirmed — sorry for the mix-up" in md
        assert "_edited_" in md


def test_original_ai_output_preserved_after_edit():
    with TestClient(app) as c:
        call_id = _seed()
        edited = json.loads(json.dumps(INSIGHTS))
        edited["summary"] = [{"text": "Human rewrote this.", "evidence": []}]
        c.patch(f"/api/calls/{call_id}/insights", json={"insights": edited})
        c.post(f"/api/calls/{call_id}/insights/reset")
        detail = c.get(f"/api/calls/{call_id}").json()
        assert detail["run"]["edited"] is False
        assert detail["insights"]["summary"][0]["text"] == "Bob was double-charged."


def test_export_json_excludes_compliance_but_keeps_evidence():
    with TestClient(app) as c:
        call_id = _seed()
        data = c.get(f"/api/calls/{call_id}/export.json").json()
        assert "compliance" not in data
        assert data["insights"]["summary"][0]["evidence"][0]["line"] == 2


def test_markdown_transcript_opt_in():
    with TestClient(app) as c:
        call_id = _seed()
        assert "## Transcript" not in c.get(f"/api/calls/{call_id}/export.md").text
        assert "## Transcript" in c.get(f"/api/calls/{call_id}/export.md?transcript=true").text


def test_share_link_freezes_snapshot_and_excludes_transcript():
    with TestClient(app) as c:
        call_id = _seed()
        token = c.post(f"/api/calls/{call_id}/share").json()["token"]

        # edit AFTER sharing — the shared page must not change
        edited = json.loads(json.dumps(INSIGHTS))
        edited["summary"] = [{"text": "changed later", "evidence": []}]
        c.patch(f"/api/calls/{call_id}/insights", json={"insights": edited})

        snap = c.get(f"/api/share/{token}").json()["snapshot"]
        assert snap["summary"][0]["text"] == "Bob was double-charged."  # frozen
        assert "transcript" not in snap
        assert "compliance" not in snap
        assert snap["follow_up_email"]["subject"] == "Your refund"


def test_share_revoke_404s():
    with TestClient(app) as c:
        call_id = _seed()
        token = c.post(f"/api/calls/{call_id}/share").json()["token"]
        assert c.get(f"/api/share/{token}").status_code == 200
        c.post(f"/api/share/{token}/revoke")
        assert c.get(f"/api/share/{token}").status_code == 404


def test_retry_only_on_failed_or_partial():
    with TestClient(app) as c:
        shipped = _seed(status="shipped")
        assert c.post(f"/api/calls/{shipped}/retry").status_code == 409

        stages = [{"name": s, "status": "ok", "attempts": 1, "cost_usd": 0.0, "error": None} for s in STAGES]
        stages[-1] = {"name": "compose_email", "status": "failed", "attempts": 3, "cost_usd": 0.0, "error": "bad json"}
        partial = _seed(status="partial", stages=stages)
        r = c.post(f"/api/calls/{partial}/retry")
        assert r.json()["from_stage"] == "run_insights"  # transcript exists

        with get_session() as session:
            run = session.scalars(select(Run).where(Run.call_id == partial)).first()
            assert run.status == "running"
            assert next(s for s in run.stages if s["name"] == "compose_email")["status"] == "pending"


def test_retry_reprocesses_when_no_transcript():
    with TestClient(app) as c:
        stages = [{"name": "transcribe", "status": "failed", "attempts": 3, "cost_usd": 0.0, "error": "403"}]
        call_id = _seed(status="failed", stages=stages, transcript=False, insights=None)
        r = c.post(f"/api/calls/{call_id}/retry")
        assert r.json()["from_stage"] == "process_call"
