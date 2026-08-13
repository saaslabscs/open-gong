"""API cutover (Task 9): GET /api/calls and GET /api/calls/{id} read from
AgentRun instead of Run.insights/compliance. Covers the exact agent_runs
shape the frontend (Task 11) depends on — in particular that an AgentRun's
own `id` (primary key, needed to address
PATCH /api/calls/{id}/agent-runs/{agent_run_id}) is distinct from `agent_id`
(the Agent it belongs to) — plus edit/reset edge cases not already covered
by test_share.py's happy-path exercises. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §4.
"""

import uuid

from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.models import Agent, AgentRun, Call, Run, Transcript

LINES = [
    {"line": 1, "speaker": "Ana", "text": "This call is recorded. How can I help?"},
    {"line": 2, "speaker": "Bob", "text": "My invoice was charged twice this month."},
]


def _seed_with_agent_run(output=None, status="shipped"):
    with get_session() as session:
        call = Call(title="t", source="upload", external_id=f"cutover-{uuid.uuid4().hex}", audio_path="/tmp/x.wav")
        session.add(call)
        session.flush()
        session.add(Transcript(call_id=call.id, lines=LINES))
        run = Run(
            call_id=call.id, status="shipped",
            stages=[{"name": "transcribe", "status": "ok", "attempts": 1, "cost_usd": 0.0, "error": None}],
            orchestrator_reasoning="Dispatched Call Summarizer.",
        )
        session.add(run)
        session.flush()
        agent = Agent(name="Call Summarizer", description="d", system_prompt="p")
        session.add(agent)
        session.flush()
        agent_run = AgentRun(
            call_id=call.id, run_id=run.id, agent_id=agent.id, status=status,
            steps=[{"name": "summary-and-next-steps", "status": "ok", "attempts": 1, "cost_usd": 0.01, "error": None}],
            output=output,
            cost_usd=0.01,
        )
        session.add(agent_run)
        session.commit()
        return call.id, agent.id, agent_run.id


def test_get_call_agent_runs_shape_has_distinct_id_and_agent_id():
    with TestClient(app) as c:
        call_id, agent_id, agent_run_id = _seed_with_agent_run(
            output={"summary-and-next-steps": {"summary": [{"text": "ok", "evidence": []}]}}
        )
        detail = c.get(f"/api/calls/{call_id}").json()
        assert detail["run"]["orchestrator_reasoning"] == "Dispatched Call Summarizer."

        agent_runs = detail["agent_runs"]
        assert len(agent_runs) == 1
        ar = agent_runs[0]
        assert set(ar.keys()) == {
            "id", "agent_id", "agent_name", "status", "output", "edited", "steps", "cost_usd",
        }
        assert ar["id"] == agent_run_id
        assert ar["agent_id"] == agent_id
        assert ar["id"] != ar["agent_id"]  # the AgentRun row, not the Agent it belongs to
        assert ar["agent_name"] == "Call Summarizer"
        assert ar["status"] == "shipped"
        assert ar["edited"] is False
        assert ar["cost_usd"] == 0.01


def test_list_calls_has_no_intent_field():
    with TestClient(app) as c:
        call_id, _, _ = _seed_with_agent_run()
        calls = c.get("/api/calls").json()
        call = next(x for x in calls if x["id"] == call_id)
        assert "intent" not in call


def test_edit_agent_run_404s_for_wrong_call_id():
    with TestClient(app) as c:
        _, _, agent_run_id = _seed_with_agent_run(output={"s": {}})
        other_call_id, _, _ = _seed_with_agent_run(output={"s": {}})
        # agent_run_id belongs to the first call, not this second one
        r = c.patch(f"/api/calls/{other_call_id}/agent-runs/{agent_run_id}", json={"output": {"s": {}}})
        assert r.status_code == 404


def test_edit_agent_run_409s_when_no_output_yet():
    with TestClient(app) as c:
        call_id, _, agent_run_id = _seed_with_agent_run(output=None)
        r = c.patch(f"/api/calls/{call_id}/agent-runs/{agent_run_id}", json={"output": {"s": {}}})
        assert r.status_code == 409


def test_reset_agent_run_404s_for_unknown_id():
    with TestClient(app) as c:
        call_id, _, _ = _seed_with_agent_run(output={"s": {}})
        r = c.post(f"/api/calls/{call_id}/agent-runs/does-not-exist/reset")
        assert r.status_code == 404
