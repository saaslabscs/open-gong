"""The fixed pipeline stages, and the retry/budget primitives shared with
agent_runtime.AgentRunState.

The old RunState class is gone — agent_runtime's harness replaced it — but the
stage list itself is back: `summarize` and `compose_email` run on every call
before any agent dispatches, so a summary cannot be configured away. See
docs/superpowers/specs/2026-08-14-guaranteed-summaries-and-call-log-ui-design.md
§1."""

import os

MAX_ATTEMPTS = 3

# The baseline every call runs, in order. Agents dispatch after these.
STAGES = ["transcribe", "summarize", "compose_email"]
# Without these there is nothing to ship — their failure fails the whole run.
CRITICAL_STAGES = {"transcribe", "summarize"}


def max_cost_per_run() -> float:
    return float(os.environ.get("MAX_COST_PER_RUN", "1.00"))


class BudgetExceeded(Exception):
    pass


class StageFailed(Exception):
    def __init__(self, stage: str, reason: str):
        self.stage = stage
        self.reason = reason
        super().__init__(f"stage {stage!r} failed: {reason}")
