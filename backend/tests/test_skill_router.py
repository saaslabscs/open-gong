"""Agent skill-router: agent + call -> which skills to run (Task 7 of the
agent-skill architecture plan). See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §1.
"""

import app.llm as llm_mod
from app.skill_router import route_skills
from fakes import fake_llm

LINES = [
    {"line": 1, "speaker": "Ana", "text": "So tell me about your budget for this project."},
    {"line": 2, "speaker": "Bob", "text": "We're looking at fifty thousand a year, roughly."},
]

SKILLS = [
    {"id": "skill-1", "name": "sales-scorecard", "description": "Scores sales discovery", "when_to_use": "The call is a sales conversation"},
    {"id": "skill-2", "name": "support-scorecard", "description": "Scores support resolution", "when_to_use": "The call is a support conversation"},
    {"id": "skill-3", "name": "compliance-check", "description": "Flags compliance risk", "when_to_use": "Always run this"},
]

AGENT_PROMPT = "Use the sales skill for sales calls, support skill for support calls. Always run compliance."


def test_route_selects_matching_skills(monkeypatch):
    monkeypatch.setattr(
        llm_mod,
        "complete_json",
        fake_llm({"skill_route": {"skill_ids": ["skill-1", "skill-3"], "reasoning": "Sales call — sales scorecard plus mandatory compliance."}}),
    )
    selected, reasoning, cost = route_skills(LINES, AGENT_PROMPT, SKILLS)
    assert selected == ["skill-1", "skill-3"]
    assert "Sales call" in reasoning


def test_route_can_select_zero_skills(monkeypatch):
    monkeypatch.setattr(
        llm_mod,
        "complete_json",
        fake_llm({"skill_route": {"skill_ids": [], "reasoning": "No skill applies to this fragment."}}),
    )
    selected, reasoning, cost = route_skills(LINES, AGENT_PROMPT, SKILLS)
    assert selected == []


def test_route_with_no_skills_attached_returns_empty(monkeypatch):
    selected, reasoning, cost = route_skills(LINES, AGENT_PROMPT, [])
    assert selected == []
    assert cost == 0.0
