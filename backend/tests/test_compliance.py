"""M10: in-house compliance fallback — evidence-gated findings, PASS/WARN verdict."""

import app.llm as llm_mod
from app.compliance import run_compliance_check

LINES = [
    {"line": 1, "speaker": "Jake", "text": "Hi, this is Jake calling about your dental practice."},
    {"line": 2, "speaker": "Linda", "text": "Okay, what's this about?"},
    {"line": 3, "speaker": "Jake", "text": "This is guaranteed to double your bookings in 30 days."},
    {"line": 4, "speaker": "Jake", "text": "This pricing is only good if you sign today."},
]


def test_clean_call_verdict_pass(monkeypatch):
    monkeypatch.setattr(llm_mod, "complete_json", lambda *a, **k: ({"findings": []}, 0.002))
    result, cost = run_compliance_check(LINES)
    assert result["verdict"] == "PASS"
    assert result["findings"] == []
    assert result["source"] == "internal"
    assert result["audit_hash"].startswith("internal_")
    assert cost == 0.002


def test_findings_with_valid_evidence_kept_verdict_warn(monkeypatch):
    raw = {
        "findings": [
            {"rule": "unsubstantiated_claims", "severity": "high", "detail": "guarantee with no basis",
             "evidence": [{"quote": "guaranteed to double your bookings", "line": 3}]},
            {"rule": "high_pressure_tactics", "severity": "medium", "detail": "artificial urgency",
             "evidence": [{"quote": "only good if you sign today", "line": 4}]},
        ]
    }
    monkeypatch.setattr(llm_mod, "complete_json", lambda *a, **k: (raw, 0.004))
    result, _ = run_compliance_check(LINES)
    assert result["verdict"] == "WARN"
    assert len(result["findings"]) == 2


def test_missing_disclosure_passes_with_no_evidence(monkeypatch):
    # an absence has no line to cite — this rule is allowed through empty,
    # unlike every other quotable finding
    raw = {"findings": [
        {"rule": "missing_recording_disclosure", "severity": "high",
         "detail": "No one disclosed the call was being recorded.", "evidence": []},
    ]}
    monkeypatch.setattr(llm_mod, "complete_json", lambda *a, **k: (raw, 0.002))
    result, _ = run_compliance_check(LINES)
    assert result["verdict"] == "WARN"
    assert result["findings"][0]["rule"] == "missing_recording_disclosure"


def test_fabricated_finding_dropped(monkeypatch):
    raw = {
        "findings": [
            {"rule": "pii_exposure", "severity": "high", "detail": "SSN read aloud",
             "evidence": [{"quote": "my social is 123-45-6789", "line": 2}]},  # not in transcript
        ]
    }
    monkeypatch.setattr(llm_mod, "complete_json", lambda *a, **k: (raw, 0.003))
    result, _ = run_compliance_check(LINES)
    assert result["findings"] == []
    assert result["verdict"] == "PASS"  # unproven finding dropped → nothing to warn about


def test_never_raises_on_llm_error_is_caller_responsibility(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider down")
    monkeypatch.setattr(llm_mod, "complete_json", boom)
    try:
        run_compliance_check(LINES)
        raised = False
    except RuntimeError:
        raised = True
    assert raised  # module itself propagates; pipeline.py catches via StageFailed/retry
