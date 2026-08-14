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


def _seed(run_status="shipped", agent_run_status="shipped", transcript=True, output=AGENT_OUTPUT, insights=None):
    with get_session() as session:
        call = Call(title="Billing call", source="upload", external_id=f"s-{uuid.uuid4().hex}", duration_s=120)
        session.add(call)
        session.flush()
        if transcript:
            session.add(Transcript(call_id=call.id, lines=LINES))
        run = Run(
            call_id=call.id, status=run_status,
            stages=[{"name": "transcribe", "status": "ok", "attempts": 1, "cost_usd": 0.0, "error": None}],
            insights=insights,
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
    insights = {
        "summary": [{"text": "Bob was double-charged.", "evidence": [{"quote": "charged twice", "line": 2}]}],
        "objections": [],
        "next_steps": [],
        "follow_up_email": None,
    }
    with TestClient(app) as c:
        call_id, agent_run_id = _seed(insights=insights)
        token = c.post(f"/api/calls/{call_id}/share").json()["token"]

        # edit AFTER sharing — the shared page must not change
        edited = json.loads(json.dumps(AGENT_OUTPUT))
        edited["summary-and-next-steps"]["summary"] = [{"text": "changed later", "evidence": []}]
        c.patch(f"/api/calls/{call_id}/agent-runs/{agent_run_id}", json={"output": edited})

        snap = c.get(f"/api/share/{token}").json()["snapshot"]
        # The public /share/[token] page types and renders exactly this shape
        # (web/lib/api.ts: ShareSnapshot / SharedAgentRun) — a silent change
        # here breaks that page at runtime, not at build time.
        assert set(snap) == {"title", "recorded_at", "duration_s", "agent_runs", "insights"}
        assert set(snap["agent_runs"][0]) == {"agent_name", "output", "edited"}
        frozen_summary = snap["agent_runs"][0]["output"]["summary-and-next-steps"]["summary"]
        assert frozen_summary[0]["text"] == "Bob was double-charged."  # frozen
        assert snap["insights"]["summary"][0]["text"] == "Bob was double-charged."  # guaranteed baseline carried through
        assert "transcript" not in snap
        for ao in snap["agent_runs"]:
            assert "compliance-check" not in ao["output"]


LEGACY_INSIGHTS = {
    "intent": {"value": "support", "confidence": 0.94, "evidence": []},
    "summary": [{"text": "Bob was double-charged.", "evidence": [{"quote": "charged twice", "line": 2}]}],
    "objections": [],
    "next_steps": [],
    "follow_up_email": None,
    "dropped_claims": [{"where": "summary[1]", "reason": "quote not found in line 2"}],
    "scorecard": {"pack": "support-default", "fields": [{"name": "issue_identified", "value": True}]},
}


def test_retired_insight_sections_never_leave_the_building():
    """The 9 pre-cutover rows still hold `intent` and `scorecard`. Those stay
    retired — passing Run.insights through verbatim would republish them from
    the public share endpoint and in export.json."""
    with TestClient(app) as c:
        call_id, _ = _seed(insights=LEGACY_INSIGHTS)
        token = c.post(f"/api/calls/{call_id}/share").json()["token"]
        snap = c.get(f"/api/share/{token}").json()["snapshot"]
        exported = c.get(f"/api/calls/{call_id}/export.json").json()

    for ins in (snap["insights"], exported["insights"]):
        assert "scorecard" not in ins
        assert "intent" not in ins
        assert ins["summary"][0]["text"] == "Bob was double-charged."  # the kept sections survive
        assert ins["follow_up_email"] is None
        # dropped_claims keeps the stored list shape end to end
        assert ins["dropped_claims"] == [{"where": "summary[1]", "reason": "quote not found in line 2"}]


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


def test_markdown_export_includes_the_summary_with_citations():
    from app.render import render_insights_markdown

    md = render_insights_markdown({
        "summary": [{"text": "Eleven AMs log half their calls.",
                     "evidence": [{"quote": "eleven account managers", "line": 4}]}],
        "objections": [],
        "next_steps": [{"text": "Maya sends docs.", "owner": "Maya",
                        "evidence": [{"quote": "I'll send you our security overview", "line": 31}]}],
        "follow_up_email": {"subject": "Security docs", "body": "Hi Daniel,"},
    })
    assert "Eleven AMs log half their calls." in md
    assert "[L4]" in md
    assert "Maya sends docs." in md
    assert "Security docs" in md
