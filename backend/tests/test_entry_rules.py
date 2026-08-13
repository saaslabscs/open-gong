"""Entry rule resolution: bypass the orchestrator when a call's source is
pinned to a specific agent (Task 8 of the agent-skill architecture plan).
phone_line matching is deferred — see the note in the task text for why.
See docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md
§4, Non-goals.
"""

from app.db import get_session
from app.entry_rules import resolve_entry_rule
from app.models import Agent, Call, EntryRule


def _seed_agent() -> str:
    with get_session() as session:
        agent = Agent(name="Support Triage", description="d", system_prompt="p")
        session.add(agent)
        session.commit()
        return agent.id


def test_matches_call_by_source():
    agent_id = _seed_agent()
    with get_session() as session:
        session.add(EntryRule(match_kind="source", match_value="sample", agent_id=agent_id))
        call = Call(title="t", source="sample", external_id="e1")
        session.add(call)
        session.commit()
        call_id = call.id

    with get_session() as session:
        call = session.get(Call, call_id)
        assert resolve_entry_rule(session, call) == agent_id


def test_no_match_returns_none():
    agent_id = _seed_agent()
    with get_session() as session:
        session.add(EntryRule(match_kind="source", match_value="sample", agent_id=agent_id))
        call = Call(title="t", source="upload", external_id="e2")
        session.add(call)
        session.commit()
        call_id = call.id

    with get_session() as session:
        call = session.get(Call, call_id)
        assert resolve_entry_rule(session, call) is None


def test_no_rules_configured_returns_none():
    with get_session() as session:
        call = Call(title="t", source="upload", external_id="e3")
        session.add(call)
        session.commit()
        call_id = call.id

    with get_session() as session:
        call = session.get(Call, call_id)
        assert resolve_entry_rule(session, call) is None


def test_ignores_a_phone_line_rule_it_cannot_evaluate_yet():
    """A phone_line rule may exist in the table (the column allows it), but
    this phase's Call has no caller_phone to match against — it must be
    silently ignored, not crash or false-match."""
    agent_id = _seed_agent()
    with get_session() as session:
        session.add(EntryRule(match_kind="phone_line", match_value="+15550001111", agent_id=agent_id))
        call = Call(title="t", source="upload", external_id="e4")
        session.add(call)
        session.commit()
        call_id = call.id

    with get_session() as session:
        call = session.get(Call, call_id)
        assert resolve_entry_rule(session, call) is None
