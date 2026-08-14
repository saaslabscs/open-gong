"""Shared retry/budget primitives, reused by agent_runtime.AgentRunState.
The fixed-stage RunState this module used to define is retired — see
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §3."""

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
