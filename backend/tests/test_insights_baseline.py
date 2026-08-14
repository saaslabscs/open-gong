"""The guaranteed baseline: summarize + compose_email, and the evidence gate
that keeps unproven claims out of a shipped summary.
"""

from app import insights
from app.evidence import validate_extraction
from fakes import fake_llm

LINES = [
    {"line": 1, "speaker": "Maya", "text": "Thanks for making the time."},
    {"line": 2, "speaker": "Daniel", "text": "We've got eleven account managers."},
]


def test_summarize_returns_the_three_fields(monkeypatch):
    monkeypatch.setattr(
        insights.llm,
        "complete_json",
        fake_llm({"extract": {"summary": [], "objections": [], "next_steps": []}}),
    )
    out, cost = insights.summarize(LINES)
    assert set(out) == {"summary", "objections", "next_steps"}
    assert cost > 0


def test_summarize_schema_requires_evidence_on_every_claim():
    props = insights.SUMMARY_SCHEMA["properties"]
    for field in ("summary", "objections", "next_steps"):
        assert "evidence" in props[field]["items"]["required"], field


def test_unproven_claims_are_dropped_by_the_gate(monkeypatch):
    """A claim whose quote is not in the cited line does not survive."""
    monkeypatch.setattr(
        insights.llm,
        "complete_json",
        fake_llm({"extract": {
            "summary": [
                {"text": "eleven AMs", "evidence": [{"quote": "eleven account managers", "line": 2}]},
                {"text": "invented", "evidence": [{"quote": "we agreed to a discount", "line": 2}]},
            ],
            "objections": [],
            "next_steps": [],
        }}),
    )
    raw, _ = insights.summarize(LINES)
    cleaned, dropped = validate_extraction(raw, LINES)
    assert [c["text"] for c in cleaned["summary"]] == ["eleven AMs"]
    assert len(dropped) == 1


def test_compose_email_returns_subject_and_body(monkeypatch):
    monkeypatch.setattr(insights.llm, "complete_json", fake_llm({}))
    out, cost = insights.compose_email(LINES, {"summary": [], "next_steps": [], "objections": []})
    assert set(out) == {"subject", "body"}
    assert cost > 0
