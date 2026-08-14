"""Retry/budget harness for a dynamic step list: capped retries, a budget cap,
and a clear terminal status, over whatever steps the caller passes in — one
agent's routed skills, or the pipeline's own guaranteed baseline. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §4.

Criticality is the caller's to declare. Agent runs pass no critical set,
because their skills are user-configured: such a run is `failed` only when
every one of its steps failed to ship anything. The baseline passes
run_state.CRITICAL_STAGES, so a failed `summarize` fails the whole run.
"""

from dataclasses import dataclass, field

from .run_state import BudgetExceeded, StageFailed, MAX_ATTEMPTS, max_cost_per_run

__all__ = ["AgentStepState", "AgentRunState", "new_agent_run_state"]


@dataclass
class AgentStepState:
    name: str
    status: str = "pending"  # pending | ok | failed | skipped
    attempts: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    dropped_claims: int = 0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "attempts": self.attempts,
            "cost_usd": round(self.cost_usd, 4),
            "error": self.error,
        }


@dataclass
class AgentRunState:
    steps: list[AgentStepState] = field(default_factory=list)
    budget: float = field(default_factory=max_cost_per_run)
    critical: frozenset[str] = frozenset()

    @property
    def spent(self) -> float:
        return sum(s.cost_usd for s in self.steps)

    def charge(self, step_name: str, cost: float) -> None:
        step = self._get(step_name)
        step.cost_usd += cost
        if self.spent > self.budget:
            raise BudgetExceeded(
                f"agent run budget ${self.budget:.2f} exceeded at step {step_name!r} "
                f"(spent ${self.spent:.4f})"
            )

    def execute(self, step_name: str, fn) -> object:
        step = self._get(step_name)
        last_err: Exception | None = None
        while step.attempts < MAX_ATTEMPTS:
            step.attempts += 1
            try:
                result = fn()
                step.status = "ok"
                return result
            except BudgetExceeded:
                step.status = "failed"
                step.error = "run budget exceeded"
                raise
            except Exception as e:  # noqa: BLE001 — reason is recorded, not swallowed
                last_err = e
        step.status = "failed"
        step.error = f"{last_err} (after {step.attempts} attempts)"
        raise StageFailed(step_name, str(last_err))

    def skip_remaining(self, from_step: str) -> None:
        """Mark every step after `from_step` as skipped — called when a
        budget breach or other stop condition cuts execution short, so a
        finished AgentRun never leaves a step stuck at 'pending' (that
        would look like a stuck run, not a deliberate stop)."""
        seen = False
        for s in self.steps:
            if s.name == from_step:
                seen = True
                continue
            if seen and s.status == "pending":
                s.status = "skipped"

    def final_status(self) -> str:
        """shipped | partial | failed.

        A critical step that did not complete — failed, or skipped because an
        earlier stop cut execution short — means nothing shipped. Agent runs
        pass no critical set: their skills are user-configured, so they are
        failed only when nothing shipped from them at all.
        """
        if any(s.name in self.critical and s.status != "ok" for s in self.steps):
            return "failed"
        if not self.steps:
            # An agent whose router selected no skills legitimately did nothing.
            # The status stays "shipped"; AgentRun.routing_reasoning (Task 5)
            # carries the why, and the UI (Task 10) says so instead of
            # rendering an empty card.
            return "shipped"
        if not any(s.status == "ok" for s in self.steps):
            return "failed"
        if any(s.status == "failed" for s in self.steps) or any(s.dropped_claims for s in self.steps):
            return "partial"
        return "shipped"

    def as_dicts(self) -> list[dict]:
        return [s.as_dict() for s in self.steps]

    def _get(self, name: str) -> AgentStepState:
        for s in self.steps:
            if s.name == name:
                return s
        raise KeyError(f"unknown step {name!r}")


def new_agent_run_state(step_names: list[str], critical: set[str] | None = None) -> AgentRunState:
    return AgentRunState(
        steps=[AgentStepState(n) for n in step_names],
        critical=frozenset(critical or ()),
    )
