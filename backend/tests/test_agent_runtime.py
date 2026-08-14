"""Per-AgentRun retry/budget harness (Task 4 of the agent-skill architecture
plan) — same guarantees as run_state.RunState, generalized to a dynamic step
list. See docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §4.
"""

import pytest

from app.agent_runtime import new_agent_run_state
from app.run_state import BudgetExceeded, StageFailed


def test_all_steps_ok_is_shipped():
    rs = new_agent_run_state(["sales-scorecard", "compliance-check"])
    rs.execute("sales-scorecard", lambda: "ok1")
    rs.execute("compliance-check", lambda: "ok2")
    assert rs.final_status() == "shipped"


def test_one_step_failed_after_retries_is_partial_if_another_shipped():
    rs = new_agent_run_state(["sales-scorecard", "compliance-check"])
    rs.execute("sales-scorecard", lambda: "ok1")

    def always_fails():
        raise ValueError("boom")

    with pytest.raises(StageFailed):
        rs.execute("compliance-check", always_fails)
    assert rs.final_status() == "partial"


def test_all_steps_failed_is_failed():
    rs = new_agent_run_state(["only-skill"])

    def always_fails():
        raise ValueError("boom")

    with pytest.raises(StageFailed):
        rs.execute("only-skill", always_fails)
    assert rs.final_status() == "failed"


def test_zero_steps_is_shipped():
    rs = new_agent_run_state([])
    assert rs.final_status() == "shipped"


def test_dropped_claims_mark_partial_even_if_step_ok():
    rs = new_agent_run_state(["sales-scorecard"])
    rs.execute("sales-scorecard", lambda: "ok")
    rs._get("sales-scorecard").dropped_claims = 2
    assert rs.final_status() == "partial"


def test_retries_are_capped_at_max_attempts():
    rs = new_agent_run_state(["flaky-skill"])
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise ValueError(f"attempt {calls['n']}")

    with pytest.raises(StageFailed):
        rs.execute("flaky-skill", flaky)
    assert calls["n"] == 3  # MAX_ATTEMPTS
    assert rs._get("flaky-skill").attempts == 3
    assert "attempt 3" in rs._get("flaky-skill").error


def test_budget_exceeded_raises_and_stops():
    rs = new_agent_run_state(["skill-a", "skill-b"])
    rs.budget = 0.01
    with pytest.raises(BudgetExceeded):
        rs.charge("skill-a", 0.02)


def test_skip_remaining_marks_only_pending_steps_after_the_named_one():
    rs = new_agent_run_state(["skill-a", "skill-b", "skill-c"])
    rs.execute("skill-a", lambda: "ok")
    rs.skip_remaining("skill-a")
    assert rs._get("skill-b").status == "skipped"
    assert rs._get("skill-c").status == "skipped"
    assert rs._get("skill-a").status == "ok"  # unaffected — it already finished


def test_skip_remaining_does_not_overwrite_a_non_pending_step():
    rs = new_agent_run_state(["skill-a", "skill-b", "skill-c"])
    rs.execute("skill-a", lambda: "ok")

    def fails():
        raise ValueError("boom")

    with pytest.raises(StageFailed):
        rs.execute("skill-b", fails)
    rs.skip_remaining("skill-a")
    assert rs._get("skill-b").status == "failed"  # already terminal, not clobbered to skipped
    assert rs._get("skill-c").status == "skipped"


def test_as_dicts_matches_step_shape():
    rs = new_agent_run_state(["skill-a"])
    rs.execute("skill-a", lambda: "ok")
    dicts = rs.as_dicts()
    assert dicts == [{"name": "skill-a", "status": "ok", "attempts": 1, "cost_usd": 0.0, "error": None}]


def test_failed_critical_step_is_failed_even_when_another_shipped():
    rs = new_agent_run_state(["summarize", "compose_email"], critical={"summarize"})
    rs.execute("compose_email", lambda: "ok")

    def boom():
        raise ValueError("no summary")

    with pytest.raises(StageFailed):
        rs.execute("summarize", boom)
    assert rs.final_status() == "failed"


def test_failed_noncritical_step_is_partial():
    rs = new_agent_run_state(["summarize", "compose_email"], critical={"summarize"})
    rs.execute("summarize", lambda: "ok")

    def boom():
        raise ValueError("no email")

    with pytest.raises(StageFailed):
        rs.execute("compose_email", boom)
    assert rs.final_status() == "partial"


def test_skipped_critical_step_is_failed():
    """A critical step skipped by an earlier stop is as fatal as one that failed."""
    rs = new_agent_run_state(["compose_email", "summarize"], critical={"summarize"})
    rs.execute("compose_email", lambda: "ok")
    rs.skip_remaining("compose_email")
    assert rs.final_status() == "failed"


def test_critical_defaults_to_empty_so_agent_runs_are_unaffected():
    rs = new_agent_run_state(["sales-scorecard"])
    rs.execute("sales-scorecard", lambda: "ok")
    assert rs.critical == frozenset()
    assert rs.final_status() == "shipped"
