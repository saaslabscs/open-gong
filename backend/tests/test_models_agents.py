"""New Agent/Skill/AgentRun/EntryRule tables (Task 1 of the agent-skill
architecture plan). Purely additive — see
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §4.

The standalone Orchestrator table from Task 1 was later removed in favor of
an `Agent.is_orchestrator` flag — see the orchestrator-designation follow-up
that replaced it.
"""

from app.db import get_session
from app.models import Agent, AgentRun, AgentSkill, EntryRule, Skill


def test_agent_round_trips():
    with get_session() as session:
        agent = Agent(
            name="Call Summarizer",
            description="Summarizes calls and scores them against sales/support rubrics",
            system_prompt="Use the sales skill for sales calls, support skill for support calls.",
        )
        session.add(agent)
        session.commit()
        agent_id = agent.id

    with get_session() as session:
        row = session.get(Agent, agent_id)
        assert row.name == "Call Summarizer"
        assert row.enabled is True
        assert row.created_at is not None


def test_skill_round_trips_with_fields():
    with get_session() as session:
        skill = Skill(
            name="sales-scorecard",
            description="Scores discovery quality and MEDDIC coverage on sales calls",
            when_to_use="The call is a sales conversation",
            body_md="Score this call against MEDDIC...",
            fields={
                "checks": ["budget_discussed"],
                "scores": [{"name": "discovery_quality", "max": 5}],
                "claims": ["summary", "next_steps"],
            },
            source="ui",
        )
        session.add(skill)
        session.commit()
        skill_id = skill.id

    with get_session() as session:
        row = session.get(Skill, skill_id)
        assert row.fields["checks"] == ["budget_discussed"]
        assert row.fields["scores"][0]["max"] == 5
        assert row.version == 1
        assert row.source == "ui"


def test_skill_fields_defaults_to_none_for_narrative_only_skill():
    with get_session() as session:
        skill = Skill(name="plain-summary", description="d", when_to_use="w", body_md="Summarize.", source="upload")
        session.add(skill)
        session.commit()
        skill_id = skill.id

    with get_session() as session:
        assert session.get(Skill, skill_id).fields is None


def test_agent_skill_join_links_agent_to_skill():
    with get_session() as session:
        agent = Agent(name="a", description="d", system_prompt="p")
        skill = Skill(name="s", description="d", when_to_use="w", body_md="b", source="ui")
        session.add_all([agent, skill])
        session.flush()
        session.add(AgentSkill(agent_id=agent.id, skill_id=skill.id))
        session.commit()
        agent_id, skill_id = agent.id, skill.id

    with get_session() as session:
        from sqlalchemy import select

        link = session.scalars(
            select(AgentSkill).where(AgentSkill.agent_id == agent_id, AgentSkill.skill_id == skill_id)
        ).first()
        assert link is not None


def test_agent_is_orchestrator_defaults_false_and_round_trips():
    with get_session() as session:
        agent = Agent(name="a", description="d", system_prompt="p")
        session.add(agent)
        session.commit()
        agent_id = agent.id

    with get_session() as session:
        assert session.get(Agent, agent_id).is_orchestrator is False

    with get_session() as session:
        row = session.get(Agent, agent_id)
        row.is_orchestrator = True
        session.commit()

    with get_session() as session:
        assert session.get(Agent, agent_id).is_orchestrator is True


def test_agent_run_round_trips_with_parent_seam_unset():
    with get_session() as session:
        agent = Agent(name="a", description="d", system_prompt="p")
        session.add(agent)
        session.flush()
        agent_run = AgentRun(
            call_id="call-1",
            run_id="run-1",
            agent_id=agent.id,
            status="shipped",
            steps=[{"name": "sales-scorecard", "status": "ok", "attempts": 1, "cost_usd": 0.01, "error": None}],
            output={"summary": []},
            cost_usd=0.01,
        )
        session.add(agent_run)
        session.commit()
        agent_run_id = agent_run.id

    with get_session() as session:
        row = session.get(AgentRun, agent_run_id)
        assert row.status == "shipped"
        assert row.parent_agent_run_id is None
        assert row.edited_output is None
        assert row.finished_at is None
        assert row.steps[0]["name"] == "sales-scorecard"


def test_entry_rule_round_trips():
    with get_session() as session:
        agent = Agent(name="Support Triage", description="d", system_prompt="p")
        session.add(agent)
        session.flush()
        rule = EntryRule(match_kind="phone_line", match_value="+15550001111", agent_id=agent.id)
        session.add(rule)
        session.commit()
        rule_id = rule.id

    with get_session() as session:
        row = session.get(EntryRule, rule_id)
        assert row.match_kind == "phone_line"
        assert row.match_value == "+15550001111"
