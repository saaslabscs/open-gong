"""Skill executor: build a schema from fields:, run one LLM call, gate the
output (Task 5 of the agent-skill architecture plan). See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §2.
"""

import app.llm as llm_mod
from app.skills.executor import run_skill
from fakes import fake_llm

LINES = [
    {"line": 1, "speaker": "Ana", "text": "This call is recorded, just so you know."},
    {"line": 2, "speaker": "Bob", "text": "Our budget for this is around fifty thousand a year."},
]

SKILL = {
    "name": "sales-scorecard",
    "description": "Scores discovery quality on sales calls",
    "when_to_use": "The call is a sales conversation",
    "body_md": "Evaluate this call against MEDDIC. Cite evidence for every field.",
    "fields": {
        "checks": ["budget_discussed"],
        "scores": [{"name": "discovery_quality", "max": 5}],
        "claims": ["summary"],
    },
}


def test_run_skill_builds_schema_and_gates_output(monkeypatch):
    good_output = {
        "budget_discussed": {"value": True, "evidence": [{"quote": "fifty thousand a year", "line": 2}]},
        "discovery_quality": {"score": 4, "justification": "Solid discovery.", "evidence": [{"quote": "budget for this", "line": 2}]},
        "summary": [{"text": "Budget of $50k/year discussed.", "evidence": [{"quote": "fifty thousand a year", "line": 2}]}],
    }
    monkeypatch.setattr(llm_mod, "complete_json", fake_llm({"skill": good_output}))

    cleaned, dropped, cost = run_skill(SKILL, LINES)

    assert cleaned["budget_discussed"]["value"] is True
    assert cleaned["discovery_quality"]["score"] == 4
    assert cleaned["summary"][0]["text"] == "Budget of $50k/year discussed."
    assert dropped == []
    assert cost == 0.01


def test_run_skill_drops_fabricated_check(monkeypatch):
    bad_output = {
        "budget_discussed": {"value": True, "evidence": [{"quote": "we need a million dollars", "line": 2}]},
        "discovery_quality": {"score": 3, "justification": "j", "evidence": [{"quote": "budget for this", "line": 2}]},
        "summary": [],
    }
    monkeypatch.setattr(llm_mod, "complete_json", fake_llm({"skill": bad_output}))

    cleaned, dropped, cost = run_skill(SKILL, LINES)

    assert cleaned["budget_discussed"] == {"value": None, "evidence": []}
    assert any(d["where"] == "budget_discussed" for d in dropped)


def test_run_skill_with_no_fields_returns_raw_output_ungated(monkeypatch):
    narrative_skill = {
        "name": "plain-summary",
        "description": "d",
        "when_to_use": "w",
        "body_md": "Summarize this call in plain prose.",
        "fields": None,
    }
    monkeypatch.setattr(llm_mod, "complete_json", fake_llm({"skill": {"summary_text": "A short prose summary."}}))

    cleaned, dropped, cost = run_skill(narrative_skill, LINES)

    assert cleaned == {"summary_text": "A short prose summary."}
    assert dropped == []
