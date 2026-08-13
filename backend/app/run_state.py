"""Run/stage state machine: the harness that guarantees every run ends with a
clear status (shipped | partial | failed), retries are capped with reasons
attached, and cost stays under budget.

Stage order is fixed; each stage is attempted up to MAX_ATTEMPTS. A failed
non-critical stage marks the run `partial`; a failed critical stage marks it
`failed`. Budget breach stops the run cleanly.
"""

import os
from dataclasses import dataclass, field

STAGES = ["transcribe", "detect_intent", "extract", "validate", "score", "compliance", "compose_email"]
# Without these there is nothing to ship — their failure fails the whole run.
CRITICAL_STAGES = {"transcribe", "extract", "validate"}
MAX_ATTEMPTS = 3


def max_cost_per_run() -> float:
    return float(os.environ.get("MAX_COST_PER_RUN", "1.00"))


class BudgetExceeded(Exception):
    pass


@dataclass
class StageResult:
    name: str
    status: str = "pending"  # pending | ok | failed | skipped
    attempts: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    dropped_claims: int = 0

    def as_dict(self) -> dict:
        d = {
            "name": self.name,
            "status": self.status,
            "attempts": self.attempts,
            "cost_usd": round(self.cost_usd, 4),
            "error": self.error,
        }
        if self.name == "validate":
            d["dropped_claims"] = self.dropped_claims
        return d


@dataclass
class RunState:
    stages: list[StageResult] = field(default_factory=lambda: [StageResult(s) for s in STAGES])
    budget: float = field(default_factory=max_cost_per_run)

    @property
    def spent(self) -> float:
        return sum(s.cost_usd for s in self.stages)

    def charge(self, stage_name: str, cost: float) -> None:
        """Record spend; raise if the run budget is breached."""
        stage = self._get(stage_name)
        stage.cost_usd += cost
        if self.spent > self.budget:
            raise BudgetExceeded(
                f"run budget ${self.budget:.2f} exceeded at stage {stage_name!r} "
                f"(spent ${self.spent:.4f})"
            )

    def execute(self, stage_name: str, fn) -> object:
        """Run `fn()` for a stage with capped retries. Returns fn's result.

        On exhausted retries: records the reason, marks the stage failed, and
        raises StageFailed so the pipeline can decide partial-vs-failed.
        """
        stage = self._get(stage_name)
        last_err: Exception | None = None
        while stage.attempts < MAX_ATTEMPTS:
            stage.attempts += 1
            try:
                result = fn()
                stage.status = "ok"
                return result
            except BudgetExceeded:
                stage.status = "failed"
                stage.error = "run budget exceeded"
                raise
            except Exception as e:  # noqa: BLE001 — reason is recorded, not swallowed
                last_err = e
        stage.status = "failed"
        stage.error = f"{last_err} (after {stage.attempts} attempts)"
        raise StageFailed(stage_name, str(last_err))

    def skip_remaining(self, from_stage: str) -> None:
        seen = False
        for s in self.stages:
            if s.name == from_stage:
                seen = True
                continue
            if seen and s.status == "pending":
                s.status = "skipped"

    def final_status(self) -> str:
        """Every run ends with exactly one of: shipped | partial | failed.

        A critical stage that didn't complete — failed OR skipped because an
        earlier failure cut the pipeline short — means nothing shipped: failed.
        """
        if any(s.name in CRITICAL_STAGES and s.status != "ok" for s in self.stages):
            return "failed"
        failed = [s for s in self.stages if s.status == "failed"]
        if failed or any(s.dropped_claims for s in self.stages):
            return "partial"
        return "shipped"

    def as_dicts(self) -> list[dict]:
        return [s.as_dict() for s in self.stages]

    def _get(self, name: str) -> StageResult:
        for s in self.stages:
            if s.name == name:
                return s
        raise KeyError(f"unknown stage {name!r}")


class StageFailed(Exception):
    def __init__(self, stage: str, reason: str):
        self.stage = stage
        self.reason = reason
        super().__init__(f"stage {stage!r} failed: {reason}")
