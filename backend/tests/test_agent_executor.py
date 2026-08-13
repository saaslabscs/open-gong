"""Agent executor integration (Task 9 of the agent-skill architecture
plan): orchestrator dispatch -> per-agent AgentRun -> skill router -> skill
steps, with isolation and the zero-agents edge case. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §4, §5.
"""

import app.orchestrator as orchestrator_mod
import app.skill_router as skill_router_mod
import app.skills.executor as executor_mod
from app.db import get_session
from app.jobs import run_due_jobs
from app.models import Agent, AgentRun, Call, Orchestrator, Run, Skill, Transcript
from sqlalchemy import select

LINES = [
    {"line": 1, "speaker": "Ana", "text": "Thanks for calling about your renewal."},
    {"line": 2, "speaker": "Bob", "text": "Our budget is fifty thousand a year."},
]


def _seed(agent_names: list[str], skill_names: dict[str, list[str]]) -> tuple[str, dict[str, str], dict[str, str]]:
    """agent_names: list of agent names to create. skill_names: {agent_name: [skill_name, ...]}."""
    with get_session() as session:
        session.add(Orchestrator(system_prompt="Decide which agents this call needs."))
        agent_ids = {}
        skill_ids = {}
        for name in agent_names:
            agent = Agent(name=name, description=f"{name} agent", system_prompt=f"{name} prompt")
            session.add(agent)
            session.flush()
            agent_ids[name] = agent.id
            for skill_name in skill_names.get(name, []):
                skill = Skill(
                    name=skill_name, description="d", when_to_use="w",
                    body_md="Do the thing.", fields=None, source="ui",
                )
                session.add(skill)
                session.flush()
                skill_ids[skill_name] = skill.id
                from app.models import AgentSkill

                session.add(AgentSkill(agent_id=agent.id, skill_id=skill.id))

        call = Call(title="t", source="upload", external_id="exec-test-1", audio_path="/tmp/x.wav")
        session.add(call)
        session.flush()
        session.add(Transcript(call_id=call.id, lines=LINES))
        run = Run(call_id=call.id, status="running", stages=[{"name": "transcribe", "status": "ok", "attempts": 1, "cost_usd": 0.0, "error": None}])
        session.add(run)
        session.commit()
        return call.id, agent_ids, skill_ids


def test_dispatched_agent_ships_and_run_finalizes(monkeypatch):
    call_id, agent_ids, skill_ids = _seed(["Call Summarizer"], {"Call Summarizer": ["plain-summary"]})

    monkeypatch.setattr(
        orchestrator_mod, "dispatch",
        lambda lines, agents, prompt: ([agent_ids["Call Summarizer"]], "Only summarizer needed.", 0.002),
    )
    monkeypatch.setattr(
        skill_router_mod, "route_skills",
        lambda lines, prompt, skills: ([skill_ids["plain-summary"]], "Always run.", 0.002),
    )
    monkeypatch.setattr(
        executor_mod, "run_skill",
        lambda skill, lines: ({"summary_text": "A clean call."}, [], 0.01),
    )

    from app.pipeline import run_insights

    run_insights({"call_id": call_id})

    with get_session() as session:
        run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
        assert run.status == "shipped"
        assert "Only summarizer needed" in run.orchestrator_reasoning

        agent_run = session.scalars(select(AgentRun).where(AgentRun.call_id == call_id)).first()
        assert agent_run.status == "shipped"
        assert agent_run.output["plain-summary"]["summary_text"] == "A clean call."


def test_one_agent_failing_does_not_affect_sibling(monkeypatch):
    call_id, agent_ids, skill_ids = _seed(
        ["Call Summarizer", "QA Coach"],
        {"Call Summarizer": ["plain-summary"], "QA Coach": ["qa-rubric"]},
    )

    monkeypatch.setattr(
        orchestrator_mod, "dispatch",
        lambda lines, agents, prompt: ([agent_ids["Call Summarizer"], agent_ids["QA Coach"]], "Both needed.", 0.002),
    )

    def fake_route(lines, prompt, skills):
        names = [s["name"] for s in skills]
        return [s["id"] for s in skills], "route all", 0.001

    monkeypatch.setattr(skill_router_mod, "route_skills", fake_route)

    def fake_run_skill(skill, lines):
        if skill["name"] == "qa-rubric":
            raise ValueError("simulated LLM failure")
        return {"summary_text": "A clean call."}, [], 0.01

    monkeypatch.setattr(executor_mod, "run_skill", fake_run_skill)

    from app.pipeline import run_insights

    run_insights({"call_id": call_id})

    with get_session() as session:
        agent_runs = {ar.agent_id: ar for ar in session.scalars(select(AgentRun).where(AgentRun.call_id == call_id)).all()}
        summarizer_run = agent_runs[agent_ids["Call Summarizer"]]
        qa_run = agent_runs[agent_ids["QA Coach"]]
        assert summarizer_run.status == "shipped"
        assert qa_run.status == "failed"

        run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
        assert run.status == "partial"  # one agent shipped, one failed — never silently "shipped"


def test_zero_agents_selected_ships_with_reasoning_stored(monkeypatch):
    call_id, agent_ids, skill_ids = _seed(["Call Summarizer"], {"Call Summarizer": ["plain-summary"]})

    monkeypatch.setattr(
        orchestrator_mod, "dispatch",
        lambda lines, agents, prompt: ([], "Transcript too short to classify.", 0.001),
    )

    from app.pipeline import run_insights

    run_insights({"call_id": call_id})

    with get_session() as session:
        run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
        assert run.status == "shipped"
        assert "too short" in run.orchestrator_reasoning
        assert session.scalars(select(AgentRun).where(AgentRun.call_id == call_id)).first() is None


def test_aggregate_budget_exceeded_skips_remaining_agents_cleanly(monkeypatch):
    """Global Constraints: per-AgentRun cap AND the per-call aggregate cap
    both apply. Two agents each spend WITHIN their own per-agent budget
    (proving this isn't just the per-agent cap firing), but their combined
    spend crosses MAX_COST_PER_RUN before a third agent starts — that third
    agent's AgentRun must never be created. No zombie runs, clean finalize."""
    call_id, agent_ids, skill_ids = _seed(
        ["Call Summarizer", "QA Coach", "Knowledgebase"],
        {"Call Summarizer": ["plain-summary"], "QA Coach": ["qa-rubric"], "Knowledgebase": ["kb-lookup"]},
    )
    monkeypatch.setenv("MAX_COST_PER_RUN", "0.05")

    monkeypatch.setattr(
        orchestrator_mod, "dispatch",
        lambda lines, agents, prompt: (
            [agent_ids["Call Summarizer"], agent_ids["QA Coach"], agent_ids["Knowledgebase"]],
            "All three needed.", 0.0,
        ),
    )
    monkeypatch.setattr(
        skill_router_mod, "route_skills",
        lambda lines, prompt, skills: ([s["id"] for s in skills], "route all", 0.0),
    )
    monkeypatch.setattr(
        executor_mod, "run_skill",
        lambda skill, lines: ({"summary_text": "A call."}, [], 0.03),  # under the 0.05 per-agent cap alone
    )

    from app.pipeline import run_insights

    run_insights({"call_id": call_id})

    with get_session() as session:
        run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
        agent_runs = session.scalars(select(AgentRun).where(AgentRun.call_id == call_id)).all()
        assert len(agent_runs) == 2  # Call Summarizer + QA Coach ran and shipped; Knowledgebase never started
        assert all(ar.status == "shipped" for ar in agent_runs)  # each individually under its own cap
        assert run.status == "shipped"  # finalized cleanly, not stuck "running"
        assert "budget" in run.orchestrator_reasoning.lower()
        assert "Knowledgebase" in run.orchestrator_reasoning


# --- Task 9 post-review fixes -----------------------------------------------
# The three tests below cover the Important findings from the Task 9 code
# review (retry/re-seed duplicating AgentRuns, unexpected dispatch/routing
# failures leaving a Run or AgentRun stuck "running" forever, and the
# order-dependent budget re-query). See task-9-report.md's "Fix report"
# addendum.


def test_retry_rerun_does_not_duplicate_agent_runs(monkeypatch):
    """A retry (or a re-seed) re-enters run_insights for the same run_id.
    Without clearing prior AgentRuns first, a second dispatched run would
    leave two AgentRun rows per agent on one run_id — corrupting exports,
    aggregate status, and cost accounting."""
    call_id, agent_ids, skill_ids = _seed(["Call Summarizer"], {"Call Summarizer": ["plain-summary"]})

    monkeypatch.setattr(
        orchestrator_mod, "dispatch",
        lambda lines, agents, prompt: ([agent_ids["Call Summarizer"]], "Only summarizer needed.", 0.002),
    )
    monkeypatch.setattr(
        skill_router_mod, "route_skills",
        lambda lines, prompt, skills: ([skill_ids["plain-summary"]], "Always run.", 0.002),
    )
    monkeypatch.setattr(
        executor_mod, "run_skill",
        lambda skill, lines: ({"summary_text": "A clean call."}, [], 0.01),
    )

    from app.pipeline import run_insights

    run_insights({"call_id": call_id})
    run_insights({"call_id": call_id})  # simulates retry / re-seed re-entering the same run_id

    with get_session() as session:
        agent_runs = session.scalars(select(AgentRun).where(AgentRun.call_id == call_id)).all()
        assert len(agent_runs) == 1  # not 2 — the prior attempt's row was cleared, not duplicated
        assert agent_runs[0].status == "shipped"

        run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
        assert run.status == "shipped"


def test_dispatch_raising_finalizes_run_as_failed_not_stuck_running(monkeypatch):
    """An unexpected exception from orchestrator dispatch (e.g. malformed LLM
    JSON) must still leave the Run terminal, never stuck "running" — which
    would also make POST /retry reject it with 409, permanently stranding
    the call."""
    call_id, agent_ids, skill_ids = _seed(["Call Summarizer"], {"Call Summarizer": ["plain-summary"]})

    def boom(lines, agents, prompt):
        raise ValueError("malformed LLM JSON")

    monkeypatch.setattr(orchestrator_mod, "dispatch", boom)

    from app.pipeline import run_insights

    run_insights({"call_id": call_id})

    with get_session() as session:
        run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
        assert run.status == "failed"  # not stuck "running"
        assert run.finished_at is not None
        assert "malformed LLM JSON" in run.orchestrator_reasoning
        # dispatch never returned, so no AgentRun was ever created
        assert session.scalars(select(AgentRun).where(AgentRun.call_id == call_id)).first() is None


def test_route_skills_raising_fails_only_that_agent(monkeypatch):
    """An unexpected exception from skill routing for one agent must finalize
    that agent's own AgentRun to "failed" (not leave it stuck "running") and
    must not affect a sibling agent that routes cleanly."""
    call_id, agent_ids, skill_ids = _seed(
        ["Call Summarizer", "QA Coach"],
        {"Call Summarizer": ["plain-summary"], "QA Coach": ["qa-rubric"]},
    )

    monkeypatch.setattr(
        orchestrator_mod, "dispatch",
        lambda lines, agents, prompt: (
            [agent_ids["Call Summarizer"], agent_ids["QA Coach"]], "Both needed.", 0.0,
        ),
    )

    def fake_route(lines, prompt, skills):
        names = [s["name"] for s in skills]
        if "qa-rubric" in names:
            raise ValueError("router returned malformed JSON")
        return [s["id"] for s in skills], "route all", 0.0

    monkeypatch.setattr(skill_router_mod, "route_skills", fake_route)
    monkeypatch.setattr(
        executor_mod, "run_skill",
        lambda skill, lines: ({"summary_text": "A clean call."}, [], 0.01),
    )

    from app.pipeline import run_insights

    run_insights({"call_id": call_id})

    with get_session() as session:
        agent_runs = {ar.agent_id: ar for ar in session.scalars(select(AgentRun).where(AgentRun.call_id == call_id)).all()}
        summarizer_run = agent_runs[agent_ids["Call Summarizer"]]
        qa_run = agent_runs[agent_ids["QA Coach"]]
        assert summarizer_run.status == "shipped"  # sibling unaffected
        assert qa_run.status == "failed"  # not stuck "running"
        assert qa_run.finished_at is not None
        assert "malformed JSON" in qa_run.error

        run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
        assert run.status == "partial"  # one agent shipped, one failed
