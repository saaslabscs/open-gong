"""M3 verification: the evidence gate drops fabricated claims (run → partial),
malformed LLM JSON never ships, budget breach stops cleanly, happy path ships."""

import pytest
from sqlalchemy import select

import app.llm as llm_mod
from app.db import get_session
from app.evidence import validate_extraction
from app.jobs import run_due_jobs
from app.llm import LLMBadJson
from app.models import Call, Run, Transcript
from app.pipeline import run_insights
from app.run_state import STAGES

LINES = [
    {"line": 1, "speaker": "Ana", "text": "Hi Bob, quick heads up that this call is recorded."},
    {"line": 2, "speaker": "Bob", "text": "Fine by me. Look, our renewal price went up forty percent and I'm not happy."},
    {"line": 3, "speaker": "Ana", "text": "I hear you. Let me pull up your account and see what we can do about the increase."},
    {"line": 4, "speaker": "Bob", "text": "If it stays this high we will cancel at the end of the quarter."},
    {"line": 5, "speaker": "Ana", "text": "I can offer the legacy rate for twelve months if you commit annually. I'll email the terms today."},
    {"line": 6, "speaker": "Bob", "text": "Send it over and I'll look with my cofounder."},
]


def _seed_call() -> str:
    stages = [
        {"name": s, "status": "ok" if s == "transcribe" else "pending", "attempts": 1 if s == "transcribe" else 0, "cost_usd": 0.0, "error": None}
        for s in STAGES
    ]
    with get_session() as session:
        call = Call(title="t", source="upload", external_id="t1", audio_path="/tmp/x.wav")
        session.add(call)
        session.flush()
        session.add(Transcript(call_id=call.id, lines=LINES))
        session.add(Run(call_id=call.id, status="running", stages=stages))
        session.commit()
        return call.id


def _run_for(call_id):
    with get_session() as session:
        return session.scalars(select(Run).where(Run.call_id == call_id)).first()


GOOD_INTENT = {"value": "support", "confidence": 0.95, "evidence": [{"quote": "our renewal price went up forty percent", "line": 2}]}

GOOD_EXTRACTION = {
    "summary": [
        {"text": "Renewal price rose 40% and Bob threatened to cancel.", "evidence": [{"quote": "our renewal price went up forty percent", "line": 2}, {"quote": "we will cancel at the end of the quarter", "line": 4}]},
    ],
    "objections": [
        {"label": "price", "detail": "40% renewal increase", "status": "open", "evidence": [{"quote": "renewal price went up forty percent", "line": 2}]},
        # fabricated: neither quote nor claim exists in the transcript
        {"label": "security", "detail": "Bob demanded SOC2 certification", "status": "open", "evidence": [{"quote": "we need SOC2 or we walk", "line": 4}]},
    ],
    "next_steps": [
        {"text": "Ana emails legacy-rate terms today.", "owner": "Ana", "evidence": [{"quote": "I'll email the terms today", "line": 5}]},
    ],
    "recording_disclosure": {"value": True, "evidence": [{"quote": "this call is recorded", "line": 1}]},
    "issue_identified": {"value": True, "evidence": [{"quote": "renewal price went up forty percent", "line": 2}]},
    "resolution_provided": {"value": True, "evidence": [{"quote": "legacy rate for twelve months", "line": 5}]},
    "timeline_communicated": {"value": True, "evidence": [{"quote": "I'll email the terms today", "line": 5}]},
    # unproven "true": no evidence — the gate must null this out
    "churn_risk_flagged": {"value": True, "evidence": []},
}

GOOD_SCORE = {
    "empathy_and_tone": {"score": 4, "justification": "Acknowledged frustration.", "evidence": [{"quote": "I hear you", "line": 3}]},
    "resolution_quality": {"score": 3, "justification": "Offer made, not closed.", "evidence": [{"quote": "legacy rate for twelve months", "line": 5}]},
}

GOOD_COMPLIANCE: dict = {"findings": []}
GOOD_EMAIL = {"subject": "Legacy rate terms", "body": "Hi Bob, as discussed..."}


def fake_llm(responses: dict):
    """complete_json replacement keyed by a marker found in the user prompt."""

    def fake(system, user, schema, max_tokens=2000):
        if "Classify this call's intent" in user:
            return responses.get("intent", GOOD_INTENT), 0.002
        if "Extract the following" in user:
            r = responses.get("extract", GOOD_EXTRACTION)
            if isinstance(r, Exception):
                raise r
            return r, 0.02
        if "Score this call" in user:
            return responses.get("score", GOOD_SCORE), 0.008
        if "Check this call for these specific issues" in user:
            r = responses.get("compliance", GOOD_COMPLIANCE)
            if isinstance(r, Exception):
                raise r
            return r, 0.003
        if "follow-up email" in user:
            r = responses.get("email", GOOD_EMAIL)
            if isinstance(r, Exception):
                raise r
            return r, 0.004
        raise AssertionError(f"unexpected prompt: {user[:80]}")

    return fake


def test_fabricated_claim_dropped_and_run_partial(monkeypatch):
    monkeypatch.setattr(llm_mod, "complete_json", fake_llm({}))
    call_id = _seed_call()
    run_insights({"call_id": call_id})

    run = _run_for(call_id)
    assert run.status == "partial"  # dropped claims → partial, never silent
    labels = [o["label"] for o in run.insights["objections"]]
    assert "price" in labels and "security" not in labels
    dropped_wheres = [d["where"] for d in run.insights["dropped_claims"]]
    assert "objections[1]" in dropped_wheres
    assert "churn_risk_flagged" in dropped_wheres
    churn = next(f for f in run.insights["scorecard"]["fields"] if f["name"] == "churn_risk_flagged")
    assert churn["value"] is None  # unproven "true" nulled, not shipped
    validate = next(s for s in run.stages if s["name"] == "validate")
    assert validate["dropped_claims"] == 2


def test_malformed_email_json_marks_partial_with_reason(monkeypatch):
    clean_extraction = {**GOOD_EXTRACTION}
    clean_extraction["objections"] = [GOOD_EXTRACTION["objections"][0]]
    clean_extraction["churn_risk_flagged"] = {"value": False, "evidence": []}
    monkeypatch.setattr(
        llm_mod,
        "complete_json",
        fake_llm({"extract": clean_extraction, "email": LLMBadJson("unrepairable JSON: unterminated string")}),
    )
    call_id = _seed_call()
    run_insights({"call_id": call_id})

    run = _run_for(call_id)
    assert run.status == "partial"
    assert run.insights["follow_up_email"] is None
    email_stage = next(s for s in run.stages if s["name"] == "compose_email")
    assert email_stage["status"] == "failed"
    assert "unrepairable JSON" in email_stage["error"]
    assert email_stage["attempts"] == 3
    # everything else still shipped
    assert run.insights["scorecard"]["fields"]


def test_critical_extract_failure_fails_run(monkeypatch):
    monkeypatch.setattr(
        llm_mod, "complete_json", fake_llm({"extract": LLMBadJson("no JSON object found")})
    )
    call_id = _seed_call()
    run_insights({"call_id": call_id})

    run = _run_for(call_id)
    assert run.status == "failed"
    extract_stage = next(s for s in run.stages if s["name"] == "extract")
    assert extract_stage["status"] == "failed" and "no JSON" in extract_stage["error"]
    assert all(s["status"] == "skipped" for s in run.stages if s["name"] in ("validate", "score", "compliance", "compose_email"))


def test_budget_breach_stops_cleanly(monkeypatch):
    monkeypatch.setenv("MAX_COST_PER_RUN", "0.01")
    monkeypatch.setattr(llm_mod, "complete_json", fake_llm({}))
    call_id = _seed_call()
    run_insights({"call_id": call_id})

    run = _run_for(call_id)
    assert run.status == "failed"
    assert run.finished_at is not None  # finalized, not hung
    assert run.cost_usd <= 0.03  # stopped near the cap, not a runaway


def test_clean_run_ships(monkeypatch):
    clean_extraction = {**GOOD_EXTRACTION}
    clean_extraction["objections"] = [GOOD_EXTRACTION["objections"][0]]
    clean_extraction["churn_risk_flagged"] = {"value": False, "evidence": []}
    monkeypatch.setattr(llm_mod, "complete_json", fake_llm({"extract": clean_extraction}))
    call_id = _seed_call()
    run_insights({"call_id": call_id})

    run = _run_for(call_id)
    assert run.status == "shipped"
    assert run.insights["intent"]["value"] == "support"
    assert run.insights["follow_up_email"]["subject"]
    assert run.cost_usd > 0
    assert run.compliance["verdict"] == "PASS"  # clean transcript, no findings


def test_compliance_failure_does_not_fail_the_run(monkeypatch):
    from app.llm import LLMBadJson

    clean_extraction = {**GOOD_EXTRACTION}
    clean_extraction["objections"] = [GOOD_EXTRACTION["objections"][0]]
    clean_extraction["churn_risk_flagged"] = {"value": False, "evidence": []}
    monkeypatch.setattr(
        llm_mod, "complete_json",
        fake_llm({"extract": clean_extraction, "compliance": LLMBadJson("malformed")}),
    )
    call_id = _seed_call()
    run_insights({"call_id": call_id})

    run = _run_for(call_id)
    assert run.status == "partial"  # a failed non-critical stage, not a dead run
    assert run.compliance is None  # absence, not a crash
    compliance_stage = next(s for s in run.stages if s["name"] == "compliance")
    assert compliance_stage["status"] == "failed"
    assert run.insights["follow_up_email"] is not None  # later stages still ran


def test_evidence_validator_fuzzy_and_strict():
    lines = [{"line": 1, "speaker": "A", "text": "We budgeted fifteen thousand a year for this."}]
    extraction = {
        "summary": [
            {"text": "ok exact", "evidence": [{"quote": "fifteen thousand a year", "line": 1}]},
            {"text": "ok fuzzy", "evidence": [{"quote": "We budgeted fifteen thousand a year for this", "line": 1}]},
            {"text": "bad quote", "evidence": [{"quote": "we agreed on fifty thousand", "line": 1}]},
            {"text": "bad line", "evidence": [{"quote": "fifteen thousand", "line": 9}]},
        ]
    }
    cleaned, dropped = validate_extraction(extraction, lines)
    assert len(cleaned["summary"]) == 2
    assert {d["where"] for d in dropped} == {"summary[2]", "summary[3]"}
