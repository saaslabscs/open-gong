"""Generalized fields: evidence gate (Task 3 of the agent-skill architecture
plan). Same "no proof, no claim" contract as validate_extraction, driven by
an arbitrary skill fields spec instead of one hardcoded pack schema. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §2, §4.
"""

from app.evidence import validate_fields

LINES = [
    {"line": 1, "speaker": "Ana", "text": "This call is recorded, just so you know."},
    {"line": 2, "speaker": "Bob", "text": "Our budget for this is around fifty thousand a year."},
]

FIELDS_SPEC = {
    "checks": ["budget_discussed"],
    "scores": [{"name": "discovery_quality", "max": 5}],
    "claims": ["summary"],
}


def test_check_with_true_value_and_valid_evidence_is_kept():
    raw = {
        "budget_discussed": {"value": True, "evidence": [{"quote": "budget for this is around fifty thousand", "line": 2}]},
        "discovery_quality": {"score": 4, "justification": "Good discovery.", "evidence": [{"quote": "budget for this", "line": 2}]},
        "summary": [{"text": "Budget discussed at $50k/year.", "evidence": [{"quote": "fifty thousand a year", "line": 2}]}],
    }
    cleaned, dropped = validate_fields(FIELDS_SPEC, raw, LINES)
    assert cleaned["budget_discussed"]["value"] is True
    assert dropped == []


def test_check_with_true_value_and_fabricated_evidence_is_dropped_to_null():
    raw = {
        "budget_discussed": {"value": True, "evidence": [{"quote": "we need a million dollars", "line": 2}]},
        "discovery_quality": {"score": 4, "justification": "j", "evidence": [{"quote": "budget for this", "line": 2}]},
        "summary": [],
    }
    cleaned, dropped = validate_fields(FIELDS_SPEC, raw, LINES)
    assert cleaned["budget_discussed"] == {"value": None, "evidence": []}
    assert any(d["where"] == "budget_discussed" for d in dropped)


def test_check_with_false_value_needs_no_evidence():
    raw = {
        "budget_discussed": {"value": False, "evidence": []},
        "discovery_quality": {"score": 2, "justification": "j", "evidence": [{"quote": "budget for this", "line": 2}]},
        "summary": [],
    }
    cleaned, dropped = validate_fields(FIELDS_SPEC, raw, LINES)
    assert cleaned["budget_discussed"] == {"value": False, "evidence": []}
    assert dropped == []


def test_score_with_fabricated_evidence_drops_the_score_field():
    raw = {
        "budget_discussed": {"value": False, "evidence": []},
        "discovery_quality": {"score": 5, "justification": "j", "evidence": [{"quote": "totally made up quote", "line": 1}]},
        "summary": [],
    }
    cleaned, dropped = validate_fields(FIELDS_SPEC, raw, LINES)
    assert "discovery_quality" not in cleaned
    assert any(d["where"] == "discovery_quality" for d in dropped)


def test_claims_list_drops_only_the_fabricated_item():
    raw = {
        "budget_discussed": {"value": False, "evidence": []},
        "discovery_quality": {"score": 3, "justification": "j", "evidence": [{"quote": "budget for this", "line": 2}]},
        "summary": [
            {"text": "Real claim.", "evidence": [{"quote": "fifty thousand a year", "line": 2}]},
            {"text": "Fabricated claim.", "evidence": [{"quote": "we hate this vendor", "line": 2}]},
        ],
    }
    cleaned, dropped = validate_fields(FIELDS_SPEC, raw, LINES)
    assert len(cleaned["summary"]) == 1
    assert cleaned["summary"][0]["text"] == "Real claim."
    assert any(d["where"] == "summary[1]" for d in dropped)


def test_missing_field_in_raw_output_is_treated_as_absent_not_an_error():
    raw = {"budget_discussed": {"value": False, "evidence": []}}
    cleaned, dropped = validate_fields(FIELDS_SPEC, raw, LINES)
    assert cleaned["budget_discussed"] == {"value": False, "evidence": []}
    assert "discovery_quality" not in cleaned
    assert cleaned["summary"] == []


def test_empty_fields_spec_returns_raw_output_unchanged():
    cleaned, dropped = validate_fields({}, {"anything": "goes"}, LINES)
    assert cleaned == {"anything": "goes"}
    assert dropped == []
