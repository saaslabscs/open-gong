"""M4/M5 (agent-skill architecture cutover): edit-before-share, retry,
exports, and share-link lifecycle — now driven by AgentRun instead of
Run.insights/compliance. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §4.
"""

import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_session
from app.main import app
from app.models import Agent, AgentRun, Call, Run, Transcript

LINES = [
    {"line": 1, "speaker": "Ana", "text": "This call is recorded. How can I help?"},
    {"line": 2, "speaker": "Bob", "text": "My invoice was charged twice this month."},
]

AGENT_OUTPUT = {
    "summary-and-next-steps": {
        "summary": [{"text": "Bob was double-charged.", "evidence": [{"quote": "charged twice this month", "line": 2}]}],
        "next_steps": [{"text": "Refund the duplicate.", "evidence": [{"quote": "charged twice", "line": 2}]}],
    },
    "support-scorecard": {
        "issue_identified": {"value": True, "evidence": [{"quote": "charged twice", "line": 2}]},
    },
    "follow-up-email": {
        "email_draft": [{"text": "Subject: Your refund\n\nHi Bob, refund on the way.", "evidence": [{"quote": "charged twice", "line": 2}]}],
    },
    "compliance-check": {
        "compliance_findings": [{"text": "Should never appear in exports.", "evidence": [{"quote": "charged twice", "line": 2}]}],
    },
}


def _seed(run_status="shipped", agent_run_status="shipped", transcript=True, output=AGENT_OUTPUT):
    with get_session() as session:
        call = Call(title="Billing call", source="upload", external_id=f"s-{uuid.uuid4().hex}", duration_s=120)
        session.add(call)
        session.flush()
        if transcript:
            session.add(Transcript(call_id=call.id, lines=LINES))
        run = Run(
            call_id=call.id, status=run_status,
            stages=[{"name": "transcribe", "status": "ok", "attempts": 1, "cost_usd": 0.0, "error": None}],
        )
        session.add(run)
        session.flush()
        agent_run_id = None
        if output is not None:
            agent = Agent(name="Call Summarizer", description="d", system_prompt="p")
            session.add(agent)
            session.flush()
            agent_run = AgentRun(
                call_id=call.id, run_id=run.id, agent_id=agent.id, status=agent_run_status,
                steps=[{"name": k, "status": "ok", "attempts": 1, "cost_usd": 0.0, "error": None} for k in output],
                output=output,
            )
            session.add(agent_run)
            session.flush()
            agent_run_id = agent_run.id
        session.commit()
        return call.id, agent_run_id


def test_edit_then_export_reflects_edits():
    with TestClient(app) as c:
        call_id, agent_run_id = _seed()
        edited = json.loads(json.dumps(AGENT_OUTPUT))
        edited["follow-up-email"]["email_draft"][0]["text"] = "Subject: Refund confirmed — sorry for the mix-up\n\nHi Bob."
        r = c.patch(f"/api/calls/{call_id}/agent-runs/{agent_run_id}", json={"output": edited})
        assert r.json()["edited"] is True

        detail = c.get(f"/api/calls/{call_id}").json()
        ar = detail["agent_runs"][0]
        assert ar["edited"] is True
        assert "Refund confirmed" in ar["output"]["follow-up-email"]["email_draft"][0]["text"]

        md = c.get(f"/api/calls/{call_id}/export.md").text
        assert "Refund confirmed" in md
        assert "_edited_" in md


def test_original_ai_output_preserved_after_edit():
    with TestClient(app) as c:
        call_id, agent_run_id = _seed()
        edited = json.loads(json.dumps(AGENT_OUTPUT))
        edited["summary-and-next-steps"]["summary"] = [{"text": "Human rewrote this.", "evidence": []}]
        c.patch(f"/api/calls/{call_id}/agent-runs/{agent_run_id}", json={"output": edited})
        c.post(f"/api/calls/{call_id}/agent-runs/{agent_run_id}/reset")
        detail = c.get(f"/api/calls/{call_id}").json()
        ar = detail["agent_runs"][0]
        assert ar["edited"] is False
        assert ar["output"]["summary-and-next-steps"]["summary"][0]["text"] == "Bob was double-charged."


def test_export_json_excludes_compliance_but_keeps_evidence():
    with TestClient(app) as c:
        call_id, _ = _seed()
        data = c.get(f"/api/calls/{call_id}/export.json").json()
        for ao in data["agent_runs"]:
            assert "compliance-check" not in ao["output"]
        summary = data["agent_runs"][0]["output"]["summary-and-next-steps"]["summary"]
        assert summary[0]["evidence"][0]["line"] == 2


def test_markdown_transcript_opt_in():
    with TestClient(app) as c:
        call_id, _ = _seed()
        assert "## Transcript" not in c.get(f"/api/calls/{call_id}/export.md").text
        assert "## Transcript" in c.get(f"/api/calls/{call_id}/export.md?transcript=true").text


def test_markdown_excludes_compliance():
    with TestClient(app) as c:
        call_id, _ = _seed()
        md = c.get(f"/api/calls/{call_id}/export.md").text
        assert "Should never appear in exports" not in md


def test_share_link_freezes_snapshot_and_excludes_transcript():
    with TestClient(app) as c:
        call_id, agent_run_id = _seed()
        token = c.post(f"/api/calls/{call_id}/share").json()["token"]

        # edit AFTER sharing — the shared page must not change
        edited = json.loads(json.dumps(AGENT_OUTPUT))
        edited["summary-and-next-steps"]["summary"] = [{"text": "changed later", "evidence": []}]
        c.patch(f"/api/calls/{call_id}/agent-runs/{agent_run_id}", json={"output": edited})

        snap = c.get(f"/api/share/{token}").json()["snapshot"]
        frozen_summary = snap["agent_runs"][0]["output"]["summary-and-next-steps"]["summary"]
        assert frozen_summary[0]["text"] == "Bob was double-charged."  # frozen
        assert "transcript" not in snap
        for ao in snap["agent_runs"]:
            assert "compliance-check" not in ao["output"]


def test_share_revoke_404s():
    with TestClient(app) as c:
        call_id, _ = _seed()
        token = c.post(f"/api/calls/{call_id}/share").json()["token"]
        assert c.get(f"/api/share/{token}").status_code == 200
        c.post(f"/api/share/{token}/revoke")
        assert c.get(f"/api/share/{token}").status_code == 404


def test_retry_only_on_failed_or_partial():
    with TestClient(app) as c:
        shipped, _ = _seed(run_status="shipped")
        assert c.post(f"/api/calls/{shipped}/retry").status_code == 409

        partial, _ = _seed(run_status="partial")
        with get_session() as session:
            run = session.scalars(select(Run).where(Run.call_id == partial)).first()
            stages = [dict(s) for s in run.stages]
            stages[-1]["status"] = "failed"
            stages[-1]["error"] = "bad json"
            run.stages = stages
            session.commit()

        r = c.post(f"/api/calls/{partial}/retry")
        assert r.json()["from_stage"] == "run_insights"  # transcript exists

        with get_session() as session:
            run = session.scalars(select(Run).where(Run.call_id == partial)).first()
            assert run.status == "running"
            assert run.stages[-1]["status"] == "pending"


def test_retry_reprocesses_when_no_transcript():
    with TestClient(app) as c:
        call_id, _ = _seed(run_status="failed", transcript=False, output=None)
        with get_session() as session:
            run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
            run.stages = [{"name": "transcribe", "status": "failed", "attempts": 3, "cost_usd": 0.0, "error": "403"}]
            session.commit()
        r = c.post(f"/api/calls/{call_id}/retry")
        assert r.json()["from_stage"] == "process_call"
