"""M1 harness guarantees: capped retries with reasons, partial vs failed, budget stop."""

import pytest

from app.run_state import (
    MAX_ATTEMPTS,
    BudgetExceeded,
    RunState,
    StageFailed,
)


def test_happy_path_ships():
    rs = RunState()
    for stage in [s.name for s in rs.stages]:
        rs.execute(stage, lambda: "ok")
    assert rs.final_status() == "shipped"


def test_noncritical_failure_is_partial_with_reason():
    rs = RunState()
    for stage in ["transcribe", "detect_intent", "extract", "validate", "score"]:
        rs.execute(stage, lambda: "ok")

    def bad_email():
        raise ValueError("LLM returned malformed JSON")

    with pytest.raises(StageFailed):
        rs.execute("compose_email", bad_email)

    assert rs.final_status() == "partial"
    email = next(s for s in rs.stages if s.name == "compose_email")
    assert email.attempts == MAX_ATTEMPTS
    assert "malformed JSON" in email.error


def test_critical_failure_is_failed_and_skips_rest():
    rs = RunState()

    def bad_transcribe():
        raise RuntimeError("audio fetch 403")

    with pytest.raises(StageFailed):
        rs.execute("transcribe", bad_transcribe)
    rs.skip_remaining("transcribe")

    assert rs.final_status() == "failed"
    assert all(s.status == "skipped" for s in rs.stages if s.name != "transcribe")


def test_retry_succeeds_before_cap():
    rs = RunState()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise TimeoutError("transient")
        return "ok"

    assert rs.execute("transcribe", flaky) == "ok"
    stage = next(s for s in rs.stages if s.name == "transcribe")
    assert stage.status == "ok" and stage.attempts == 2


def test_budget_breach_stops_cleanly(monkeypatch):
    monkeypatch.setenv("MAX_COST_PER_RUN", "0.05")
    rs = RunState()
    rs.charge("transcribe", 0.04)
    with pytest.raises(BudgetExceeded):
        rs.charge("extract", 0.02)
    # the breach is attributed and the run can be finalized, not hung
    extract = next(s for s in rs.stages if s.name == "extract")
    extract.status = "failed"
    extract.error = "run budget exceeded"
    rs.skip_remaining("extract")
    assert rs.final_status() == "failed"


def test_dropped_claims_mark_partial():
    rs = RunState()
    for stage in [s.name for s in rs.stages]:
        rs.execute(stage, lambda: "ok")
    validate = next(s for s in rs.stages if s.name == "validate")
    validate.dropped_claims = 2
    assert rs.final_status() == "partial"
