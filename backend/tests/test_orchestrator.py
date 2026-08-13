"""Orchestrator dispatch: call -> which agents (Task 6 of the agent-skill
architecture plan). See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §1, §4.
"""

import app.llm as llm_mod
from app.orchestrator import dispatch
from fakes import fake_llm

LINES = [
    {"line": 1, "speaker": "Ana", "text": "Thanks for calling in about your renewal price."},
    {"line": 2, "speaker": "Bob", "text": "Yeah, it went up forty percent and I'm not happy."},
]

AGENTS = [
    {"id": "agent-1", "name": "Call Summarizer", "description": "Summarizes and scores every call"},
    {"id": "agent-2", "name": "QA Coach", "description": "Assesses rep troubleshooting skill on support calls"},
]

SYSTEM_PROMPT = "Decide which agents this call needs. Always include the Call Summarizer."


def test_dispatch_selects_agents_and_returns_reasoning(monkeypatch):
    monkeypatch.setattr(
        llm_mod,
        "complete_json",
        fake_llm({"dispatch": {"agent_ids": ["agent-1"], "reasoning": "This is a support call; only the summarizer is needed."}}),
    )
    selected, reasoning, cost = dispatch(LINES, AGENTS, SYSTEM_PROMPT)
    assert selected == ["agent-1"]
    assert "support call" in reasoning
    assert cost == 0.002


def test_dispatch_can_select_zero_agents(monkeypatch):
    monkeypatch.setattr(
        llm_mod,
        "complete_json",
        fake_llm({"dispatch": {"agent_ids": [], "reasoning": "Transcript too short to classify meaningfully."}}),
    )
    selected, reasoning, cost = dispatch(LINES, AGENTS, SYSTEM_PROMPT)
    assert selected == []
    assert "too short" in reasoning


def test_dispatch_can_select_multiple_agents(monkeypatch):
    monkeypatch.setattr(
        llm_mod,
        "complete_json",
        fake_llm({"dispatch": {"agent_ids": ["agent-1", "agent-2"], "reasoning": "Both summarization and QA coaching apply."}}),
    )
    selected, reasoning, cost = dispatch(LINES, AGENTS, SYSTEM_PROMPT)
    assert selected == ["agent-1", "agent-2"]


def test_dispatch_with_no_agents_configured_returns_empty(monkeypatch):
    monkeypatch.setattr(
        llm_mod,
        "complete_json",
        fake_llm({"dispatch": {"agent_ids": [], "reasoning": "No agents are configured."}}),
    )
    selected, reasoning, cost = dispatch(LINES, [], SYSTEM_PROMPT)
    assert selected == []
