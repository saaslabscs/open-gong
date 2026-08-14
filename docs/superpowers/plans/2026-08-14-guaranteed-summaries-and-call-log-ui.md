# Guaranteed Call Summaries + Call Log UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every call gets a summary, objections, next steps and a follow-up email again — produced by fixed pipeline stages that no configuration can switch off — and the UI becomes a call log plus a tabbed call detail.

**Architecture:** Two fixed stages (`summarize`, `compose_email`) run inside the existing retry/budget harness after `transcribe` and before agent dispatch, writing to the already-present-but-unused `Run.insights` column. Agents stay purely additive. The call detail keeps its current two-column shell and gains Summary/Agent-runs tabs over the left column; the transcript rail stays put because citation chips scroll it.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy / pytest (backend); Next.js 16.3 / React 19 / Tailwind v4 (web). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-14-guaranteed-summaries-and-call-log-ui-design.md`

## Global Constraints

- No new dependencies, backend or frontend.
- `evidence.validate_extraction()` is reused **unchanged**. Do not edit `app/evidence.py`.
- Keep the literal prompt substrings `"Extract the following"` and `"follow-up email"` — `tests/fakes.py::fake_llm` keys on them, so existing fakes work untouched.
- `Run.insights` / `Run.edited_insights` already exist. **No migration for them.**
- `AgentRun.routing_reasoning` is the only new column; this project has no migration tooling, so it needs a manual `ALTER TABLE` (see Task 5).
- Scorecard, `InsightPack`, and compliance stay retired. Do not revive `select_pack`, the `intent` stage, or `Run.pack_id`.
- Stored insight shape is fixed by the 9 pre-cutover runs and must not change:
  `summary: [{text, evidence}]`, `objections: [{label, detail, status, evidence}]`,
  `next_steps: [{text, owner, evidence}]`, `follow_up_email: {subject, body}`,
  evidence items `{quote, line}`.
- Run `cd backend && uv run pytest -q` after every backend task. Baseline is **134 passing**.

---

## File Structure

**Backend**

| File | Responsibility | Change |
|---|---|---|
| `app/agent_runtime.py` | retry/budget harness over a step list | add optional `critical` set |
| `app/run_state.py` | shared primitives | add `STAGES`, `CRITICAL_STAGES` |
| `app/insights.py` | one narrow LLM task per function | add `summarize`, `compose_email` |
| `app/api/ingest.py:51` | `_fresh_stages()` — **the only place `Run.stages` is created** | list all three stages |
| `app/pipeline.py` | job handlers, stage driving, dispatch | run baseline before dispatch; persist `routing_reasoning` |
| `app/models.py` | ORM | `AgentRun.routing_reasoning` |
| `app/main.py` | list + detail serialization | expose insights, agent count, routing_reasoning |
| `app/render.py` | exports + share snapshot | insights back into `.md`/`.json` |

**Frontend**

| File | Responsibility | Change |
|---|---|---|
| `web/lib/api.ts` | types + fetch layer | `Insights` type, new fields |
| `web/lib/status.ts` | status/stage labels | stage labels for the two new stages |
| `web/components/Insights.tsx` | **new** — renders the guaranteed block | create |
| `web/components/CallView.tsx` | call detail | tabs; no-skills-applied state |
| `web/app/page.tsx` | call log | table, filters, Add call |

---

## Task 1: Give the harness a notion of critical steps

The baseline needs "no summary ⇒ the run failed". `AgentRunState` has no such
concept (agent runs are failed only when nothing shipped at all). Add it as an
optional set so one harness serves both, rather than restoring the ~90-line
near-duplicate `RunState` that `66da4e2` deleted.

**Files:**
- Modify: `backend/app/agent_runtime.py`
- Test: `backend/tests/test_agent_runtime.py`

**Interfaces:**
- Consumes: `run_state.MAX_ATTEMPTS`, `BudgetExceeded`, `StageFailed` (unchanged)
- Produces: `new_agent_run_state(step_names: list[str], critical: set[str] | None = None) -> AgentRunState`; `AgentRunState.critical: frozenset[str]`; `AgentRunState.final_status() -> str`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_agent_runtime.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_agent_runtime.py -q`
Expected: FAIL — `new_agent_run_state() got an unexpected keyword argument 'critical'`

- [ ] **Step 3: Implement**

In `backend/app/agent_runtime.py`, add the field to `AgentRunState` (after `budget`):

```python
    critical: frozenset[str] = frozenset()
```

Replace `final_status`:

```python
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
```

Replace the factory:

```python
def new_agent_run_state(step_names: list[str], critical: set[str] | None = None) -> AgentRunState:
    return AgentRunState(
        steps=[AgentStepState(n) for n in step_names],
        critical=frozenset(critical or ()),
    )
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/test_agent_runtime.py -q`
Expected: PASS

- [ ] **Step 5: Full suite, then commit**

Run: `cd backend && uv run pytest -q` — expected 138 passed.

```bash
git add backend/app/agent_runtime.py backend/tests/test_agent_runtime.py
git commit -m "feat: let the run-state harness mark steps critical"
```

---

## Task 2: `insights.summarize()` — built-in schema through the evidence gate

**Files:**
- Modify: `backend/app/insights.py`
- Test: `backend/tests/test_insights_baseline.py` (create)

**Interfaces:**
- Consumes: `llm.complete_json`, `insights._transcript_text`, `insights.SYSTEM` (add it — the module lost it in the cutover)
- Produces: `insights.SUMMARY_SCHEMA: dict`; `insights.summarize(lines: list[dict]) -> tuple[dict, float]` returning `({"summary": [...], "objections": [...], "next_steps": [...]}, cost)` — **ungated**; the caller applies `evidence.validate_extraction`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_insights_baseline.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_insights_baseline.py -q`
Expected: FAIL — `AttributeError: module 'app.insights' has no attribute 'summarize'`

- [ ] **Step 3: Implement**

In `backend/app/insights.py`, add after the imports:

```python
SYSTEM = (
    "You analyze business call transcripts. You only state what the transcript "
    "supports. Every claim must cite verbatim quotes with their line numbers. "
    "If something was not said on the call, it does not appear in your output."
)

_EVIDENCE = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"quote": {"type": "string"}, "line": {"type": "integer"}},
        "required": ["quote", "line"],
    },
}

# The baseline every call gets. Built in on purpose: not pack-driven, not
# configurable, so it cannot be switched off or mis-wired.
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "evidence": _EVIDENCE},
                "required": ["text", "evidence"],
            },
        },
        "objections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "detail": {"type": "string"},
                    "status": {"type": "string"},
                    "evidence": _EVIDENCE,
                },
                "required": ["label", "detail", "evidence"],
            },
        },
        "next_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "owner": {"type": "string"},
                    "evidence": _EVIDENCE,
                },
                "required": ["text", "evidence"],
            },
        },
    },
    "required": ["summary", "objections", "next_steps"],
}
```

Add at the end of the file:

```python
def summarize(lines: list[dict]) -> tuple[dict, float]:
    """Summary, objections and next steps — every claim carrying its quote.

    Returns the raw model output; the caller runs it through
    evidence.validate_extraction so dropped claims can be counted on the stage.
    """
    user = (
        "Extract the following from this call transcript. Rules:\n"
        "- every claim needs evidence: verbatim quote + line number\n"
        "- summary: the few things a colleague must know, one claim each\n"
        "- objections: concerns the other side raised, with how they were left\n"
        "- next_steps: what was actually agreed, with an owner named on the call\n"
        "- never invent, never embellish; omit a field rather than pad it\n\n"
        f"{_transcript_text(lines)}"
    )
    return llm.complete_json(SYSTEM, user, SUMMARY_SCHEMA, max_tokens=4000)
```

> The phrase `"Extract the following"` must stay — `fakes.fake_llm` keys on it.

- [ ] **Step 4: Run the test**

Run: `cd backend && uv run pytest tests/test_insights_baseline.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/insights.py backend/tests/test_insights_baseline.py
git commit -m "feat: add the built-in call summary extraction"
```

---

## Task 3: `insights.compose_email()`

**Files:**
- Modify: `backend/app/insights.py`
- Test: `backend/tests/test_insights_baseline.py`

**Interfaces:**
- Produces: `insights.compose_email(lines: list[dict], insights_so_far: dict) -> tuple[dict, float]` returning `({"subject": str, "body": str}, cost)`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_insights_baseline.py`:

```python
def test_compose_email_returns_subject_and_body(monkeypatch):
    monkeypatch.setattr(insights.llm, "complete_json", fake_llm({}))
    out, cost = insights.compose_email(LINES, {"summary": [], "next_steps": [], "objections": []})
    assert set(out) == {"subject", "body"}
    assert cost > 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_insights_baseline.py::test_compose_email_returns_subject_and_body -q`
Expected: FAIL — no attribute `compose_email`

- [ ] **Step 3: Implement**

Add `import json` to the top of `app/insights.py` if absent, then append:

```python
def compose_email(lines: list[dict], insights_so_far: dict) -> tuple[dict, float]:
    schema = {
        "type": "object",
        "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
        "required": ["subject", "body"],
    }
    grounding = {k: insights_so_far.get(k) for k in ("summary", "next_steps", "objections")}
    user = (
        "Draft a short, professional follow-up email from the company rep to the "
        "customer, grounded ONLY in what was agreed on this call (use the extracted "
        "next steps; do not promise anything not discussed). Plain text, no placeholders "
        "like [Name] — use the actual names from the transcript.\n\n"
        f"Extracted insights:\n{json.dumps(grounding, indent=1)[:3000]}\n\n"
        f"Transcript:\n{_transcript_text(lines)}"
    )
    return llm.complete_json(SYSTEM, user, schema, max_tokens=800)
```

> `"follow-up email"` must stay in the prompt — `fakes.fake_llm` keys on it.

- [ ] **Step 4: Run the test**

Run: `cd backend && uv run pytest tests/test_insights_baseline.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/insights.py backend/tests/test_insights_baseline.py
git commit -m "feat: restore the follow-up email draft"
```

---

## Task 4: Run the baseline on every call, before agents

**Files:**
- Modify: `backend/app/run_state.py` (add the stage list)
- Modify: `backend/app/api/ingest.py:51` (`_fresh_stages` — the only creator of `Run.stages`)
- Modify: `backend/app/pipeline.py` (`run_insights`)
- Test: `backend/tests/test_baseline_pipeline.py` (create)

**Interfaces:**
- Consumes: `new_agent_run_state(names, critical=...)` (Task 1), `insights.summarize` (Task 2), `insights.compose_email` (Task 3), `evidence.validate_extraction`
- Produces: `run_state.STAGES: list[str]`, `run_state.CRITICAL_STAGES: set[str]`; `Run.insights` populated as `{summary, objections, next_steps, follow_up_email, dropped_claims}`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_baseline_pipeline.py`:

```python
"""The guarantee: a summary on every call, with no agent configured at all.

66da4e2 made summaries conditional on an orchestrator dispatching an agent that
owned a relevant skill, so they silently stopped appearing. These tests are the
regression fence.
"""

import json

from fastapi.testclient import TestClient

from app import insights, pipeline
from app.jobs import run_due_jobs
from app.main import app
from fakes import fake_llm

WAV = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00" + b"\x00" * 20


def _drain():
    for _ in range(10):
        if run_due_jobs() == 0:
            return


def _ingest(c) -> str:
    return c.post("/api/ingest/upload", files={"file": ("a.wav", WAV, "audio/wav")}).json()["call_id"]


def test_summary_and_email_ship_with_no_agents_configured(monkeypatch):
    monkeypatch.setattr(
        insights.llm,
        "complete_json",
        fake_llm({"extract": {"summary": [], "objections": [], "next_steps": []}}),
    )
    with TestClient(app) as c:
        call_id = _ingest(c)
        _drain()
        detail = c.get(f"/api/calls/{call_id}").json()

    assert detail["run"]["status"] == "shipped"
    names = [s["name"] for s in detail["run"]["stages"]]
    assert names == ["transcribe", "summarize", "compose_email"]
    assert all(s["status"] == "ok" for s in detail["run"]["stages"])


def test_summarize_failure_fails_the_run(monkeypatch):
    monkeypatch.setattr(
        insights.llm, "complete_json", fake_llm({"extract": RuntimeError("model down")})
    )
    with TestClient(app) as c:
        call_id = _ingest(c)
        _drain()
        detail = c.get(f"/api/calls/{call_id}").json()

    assert detail["run"]["status"] == "failed"
    stage = next(s for s in detail["run"]["stages"] if s["name"] == "summarize")
    assert stage["status"] == "failed"
    assert stage["attempts"] == 3


def test_email_failure_leaves_the_run_partial(monkeypatch):
    monkeypatch.setattr(
        insights.llm,
        "complete_json",
        fake_llm({
            "extract": {"summary": [], "objections": [], "next_steps": []},
            "email": RuntimeError("model down"),
        }),
    )
    with TestClient(app) as c:
        call_id = _ingest(c)
        _drain()
        detail = c.get(f"/api/calls/{call_id}").json()

    assert detail["run"]["status"] == "partial"
    assert next(s for s in detail["run"]["stages"] if s["name"] == "summarize")["status"] == "ok"


def test_dropped_claims_make_the_run_partial(monkeypatch):
    """An unproven claim is removed and the run says it needs review."""
    monkeypatch.setattr(
        insights.llm,
        "complete_json",
        fake_llm({"extract": {
            "summary": [{"text": "invented", "evidence": [{"quote": "never said", "line": 1}]}],
            "objections": [],
            "next_steps": [],
        }}),
    )
    with TestClient(app) as c:
        call_id = _ingest(c)
        _drain()
        detail = c.get(f"/api/calls/{call_id}").json()

    assert detail["run"]["status"] == "partial"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && uv run pytest tests/test_baseline_pipeline.py -q`
Expected: FAIL — stage names are `["transcribe"]`, not the three-stage chain

- [ ] **Step 3: Add the stage list**

In `backend/app/run_state.py`, after `MAX_ATTEMPTS`:

```python
# The baseline every call runs, in order. Agents dispatch after these.
STAGES = ["transcribe", "summarize", "compose_email"]
# Without these there is nothing to ship — their failure fails the whole run.
CRITICAL_STAGES = {"transcribe", "summarize"}
```

- [ ] **Step 4: Create runs with all three stages**

In `backend/app/api/ingest.py`, add `from ..run_state import STAGES` to the imports and replace `_fresh_stages` (line 51):

```python
def _fresh_stages() -> list[dict]:
    return [
        {"name": s, "status": "pending", "attempts": 0, "cost_usd": 0.0, "error": None}
        for s in STAGES
    ]
```

- [ ] **Step 5: Drive the baseline in `run_insights`**

In `backend/app/pipeline.py`, add to the imports:

```python
from .evidence import validate_extraction
from .run_state import CRITICAL_STAGES, STAGES
```

Inside `@handler("run_insights")`, after `run_id = run.id` is resolved and the
prior AgentRuns are cleared, and **before** `_dispatch_and_run(...)`, insert:

```python
    # The guaranteed baseline. Runs before dispatch so agents never gate it.
    rs = new_agent_run_state(["summarize", "compose_email"], critical=CRITICAL_STAGES)
    baseline: dict = {}
    dropped: list[dict] = []

    def do_summarize():
        raw, cost = insights_mod.summarize(lines)
        rs.charge("summarize", cost)
        cleaned, drops = validate_extraction(raw, lines)
        dropped.extend(drops)
        rs._get("summarize").dropped_claims = len(drops)
        return cleaned

    try:
        baseline = rs.execute("summarize", do_summarize)
    except (StageFailed, BudgetExceeded):
        rs.skip_remaining("summarize")
        _persist_baseline(run_id, rs, baseline, dropped)
        return

    def do_email():
        email, cost = insights_mod.compose_email(lines, baseline)
        rs.charge("compose_email", cost)
        return email

    try:
        baseline["follow_up_email"] = rs.execute("compose_email", do_email)
    except (StageFailed, BudgetExceeded):
        pass  # non-critical: the summary still ships

    baseline["dropped_claims"] = len(dropped)
    _persist_baseline(run_id, rs, baseline, dropped)
```

`insights_mod` must be imported the same indirect way `orchestrator_mod` is, so
tests can monkeypatch it — replace `from .insights import prettify_transcript`
with:

```python
from . import insights as insights_mod
from .insights import prettify_transcript
```

Add the persister next to `_aggregate_status`:

```python
def _persist_baseline(run_id: str, rs, baseline: dict, dropped: list[dict]) -> None:
    """Write the baseline stages and insights onto the Run.

    Unions into Run.stages by name: updates entries that exist (so `transcribe`,
    already ok, survives) and appends the ones that don't. Appending matters —
    a Run built before this change, or by a test, carries only `transcribe`, and
    an update-only merge would silently drop the baseline stages.
    """
    with get_session() as session:
        run = session.get(Run, run_id)
        by_name = {s["name"]: s for s in rs.as_dicts()}
        merged = [dict(s, **by_name.pop(s["name"], {})) for s in run.stages]
        merged.extend(by_name[n] for n in STAGES if n in by_name)
        run.stages = merged
        run.insights = baseline or None
        run.cost_usd = round((run.cost_usd or 0.0) + rs.spent, 4)
        session.commit()
```

The aggregate status must account for the baseline: where `run.status` is set
from `_aggregate_status(...)` at the end of `run_insights`, combine it with the
baseline's own verdict so a failed `summarize` cannot be masked by a shipped
agent:

```python
        run.status = _aggregate_status([rs.final_status()] + agent_statuses)
```

- [ ] **Step 6: Run the tests**

Run: `cd backend && uv run pytest tests/test_baseline_pipeline.py -q`
Expected: PASS (4 tests)

- [ ] **Step 7: Full suite, then commit**

Run: `cd backend && uv run pytest -q`

Expect fallout in these places, all already located:

- `tests/test_agent_executor.py:47`, `tests/test_api_agents_cutover.py:33`,
  `tests/test_share.py:48,184` hand-build `Run(stages=[{"name": "transcribe", …}])`.
  These bypass `_fresh_stages`, so they keep working — the union merge in
  `_persist_baseline` appends the baseline stages rather than dropping them.
  That is exactly what those call sites are testing; leave them alone.
- `tests/test_ingest.py:213` looks up the `transcribe` stage by name — unaffected.
- Runs driven through `run_insights` now also make `summarize` and
  `compose_email` LLM calls. The autouse `no_network_llm` fixture
  (`conftest.py:19`) already answers both, because `fakes.fake_llm` keys on
  `"Extract the following"` and `"follow-up email"`. No fixture changes needed —
  this is why those prompt substrings are a global constraint.

If a test genuinely asserts the old single-stage chain, update the assertion to
the three-stage chain. Do not weaken the new tests to match an old assertion.

```bash
git add backend/app/run_state.py backend/app/api/ingest.py backend/app/pipeline.py backend/tests/
git commit -m "feat: guarantee a summary and follow-up email on every call"
```

---

## Task 5: Persist why an agent's router chose nothing

`pipeline.py:141` computes `router_reasoning` and throws it away, so an agent run
with zero steps is stored `shipped` with `output: NULL` and nothing anywhere says
why. That is the empty card in the UI.

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/pipeline.py` (`_run_one_agent`)
- Test: `backend/tests/test_agent_executor.py`

**Interfaces:**
- Produces: `AgentRun.routing_reasoning: str | None`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_agent_executor.py`:

Uses this file's existing `_seed(agent_names, skill_names)` helper (line 21) and
the established `orchestrator_mod` / `skill_router_mod` monkeypatch pattern:

```python
def test_router_reasoning_is_persisted_when_no_skills_apply(monkeypatch):
    """An agent that legitimately did nothing must still say why.

    Zero steps plus a NULL output is exactly the empty card the UI used to show:
    a green "shipped" badge over no content and no explanation anywhere.
    """
    call_id, agent_ids, _ = _seed(["QA agent"], {"QA agent": ["deck-creator"]})

    monkeypatch.setattr(
        orchestrator_mod, "dispatch",
        lambda lines, agents, prompt: ([agent_ids["QA agent"]], "QA should look at this.", 0.002),
    )
    monkeypatch.setattr(
        skill_router_mod, "route_skills",
        lambda lines, prompt, skills: ([], "No follow-up date was booked.", 0.002),
    )

    pipeline.run_insights({"call_id": call_id})

    with get_session() as session:
        ar = session.scalars(select(AgentRun).where(AgentRun.call_id == call_id)).one()
        assert ar.steps == []
        assert ar.output is None
        assert ar.routing_reasoning == "No follow-up date was booked."
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_agent_executor.py -k routing_reasoning -q`
Expected: FAIL — `AgentRun` has no attribute `routing_reasoning`

- [ ] **Step 3: Add the column**

In `backend/app/models.py`, inside `class AgentRun`, next to `error`:

```python
    # Why the skill router selected what it did. Without this, an agent run with
    # zero steps is indistinguishable from one that silently did nothing.
    routing_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 4: Persist it**

In `_run_one_agent`, in the success `with get_session()` block, add alongside
`agent_run.steps = rs.as_dicts()`:

```python
            agent_run.routing_reasoning = router_reasoning
```

- [ ] **Step 5: Run the test**

Run: `cd backend && uv run pytest tests/test_agent_executor.py -q`
Expected: PASS

- [ ] **Step 6: Migrate the dev database**

`Base.metadata.create_all` does not add columns to existing tables, and this
project has no migration tooling. Apply it by hand, the way
`agents.is_orchestrator` was:

```bash
cd backend && python3 -c "
import sqlite3
con = sqlite3.connect('data/opengong.sqlite3')
cols = [c[1] for c in con.execute('PRAGMA table_info(agent_runs)')]
if 'routing_reasoning' not in cols:
    con.execute('ALTER TABLE agent_runs ADD COLUMN routing_reasoning TEXT')
    con.commit()
    print('column added')
else:
    print('already present')
"
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/app/pipeline.py backend/tests/test_agent_executor.py
git commit -m "feat: record why an agent's skill router selected nothing"
```

---

## Task 6: Serve insights, agent count, and routing reasoning

**Files:**
- Modify: `backend/app/main.py` (`list_calls`, `get_call`)
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Produces: `GET /api/calls[]` gains `agent_count: int`; `GET /api/calls/{id}` gains `insights: dict | None` (edited wins) and `agent_runs[].routing_reasoning: str | None`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_api.py`:

```python
def test_call_detail_serves_insights_and_list_serves_agent_count(monkeypatch):
    from app import insights as insights_mod
    from fakes import fake_llm

    monkeypatch.setattr(
        insights_mod.llm,
        "complete_json",
        fake_llm({"extract": {"summary": [], "objections": [], "next_steps": []}}),
    )
    wav = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00" + b"\x00" * 20
    with TestClient(app) as c:
        call_id = c.post("/api/ingest/upload", files={"file": ("a.wav", wav, "audio/wav")}).json()["call_id"]
        for _ in range(10):
            if run_due_jobs() == 0:
                break

        detail = c.get(f"/api/calls/{call_id}").json()
        assert detail["insights"] is not None
        assert "summary" in detail["insights"]
        assert "follow_up_email" in detail["insights"]

        row = next(r for r in c.get("/api/calls").json() if r["id"] == call_id)
        assert row["agent_count"] == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_api.py -k insights_and_list -q`
Expected: FAIL — `KeyError: 'insights'`

- [ ] **Step 3: Implement**

In `list_calls`, inside the loop, before appending:

```python
            agent_count = 0
            if run is not None:
                agent_count = len(
                    session.scalars(select(AgentRun).where(AgentRun.run_id == run.id)).all()
                )
```

and add `"agent_count": agent_count,` to the appended dict.

In `get_call`, add `"routing_reasoning": ar.routing_reasoning,` to each
`agent_runs_out` entry, and add a top-level key to the returned dict, beside
`"transcript"`:

```python
            # Human edits win over the AI original, the same precedent
            # render.effective_agent_outputs applies per AgentRun.
            "insights": (run.edited_insights or run.insights) if run else None,
```

- [ ] **Step 4: Run the test**

Run: `cd backend && uv run pytest tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: Verify a pre-cutover call still serializes**

Run against the real dev database (backward compatibility for the 9 stored runs):

```bash
cd backend && curl -s localhost:8000/api/calls/sample-01 | python3 -c "
import sys, json
d = json.load(sys.stdin)
ins = d['insights']
print('summary claims:', len(ins['summary']))
print('has email:', 'follow_up_email' in ins)
print('first evidence:', ins['summary'][0]['evidence'][0])
"
```

Expected: non-zero claims, `has email: True`, an evidence object with `quote` and `line`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/test_api.py
git commit -m "feat: serve call insights and per-call agent count"
```

---

## Task 7: Put insights back into exports and the share snapshot

**Files:**
- Modify: `backend/app/render.py`
- Test: `backend/tests/test_share.py`

**Interfaces:**
- Consumes: `render._cite(evidence)` (exists), `Run.insights` / `Run.edited_insights`
- Produces: `render.render_insights_markdown(insights: dict) -> str`; markdown export and share snapshot both include the baseline

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_share.py`:

```python
def test_markdown_export_includes_the_summary_with_citations():
    from app.render import render_insights_markdown

    md = render_insights_markdown({
        "summary": [{"text": "Eleven AMs log half their calls.",
                     "evidence": [{"quote": "eleven account managers", "line": 4}]}],
        "objections": [],
        "next_steps": [{"text": "Maya sends docs.", "owner": "Maya",
                        "evidence": [{"quote": "I'll send you our security overview", "line": 31}]}],
        "follow_up_email": {"subject": "Security docs", "body": "Hi Daniel,"},
    })
    assert "Eleven AMs log half their calls." in md
    assert "[L4]" in md
    assert "Maya sends docs." in md
    assert "Security docs" in md
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_share.py -k markdown_export -q`
Expected: FAIL — cannot import `render_insights_markdown`

- [ ] **Step 3: Implement**

Add to `backend/app/render.py`:

```python
def render_insights_markdown(insights: dict | None) -> str:
    """The guaranteed baseline as Markdown, citations inline."""
    if not insights:
        return ""
    parts: list[str] = []

    if insights.get("summary"):
        parts.append("## Summary\n")
        for claim in insights["summary"]:
            parts.append(f"- {claim['text']}{_cite(claim.get('evidence', []))}")
        parts.append("")

    if insights.get("objections"):
        parts.append("## Objections\n")
        for o in insights["objections"]:
            status = f" _({o['status']})_" if o.get("status") else ""
            parts.append(f"- **{o['label']}**{status} — {o.get('detail', '')}{_cite(o.get('evidence', []))}")
        parts.append("")

    if insights.get("next_steps"):
        parts.append("## Next steps\n")
        for s in insights["next_steps"]:
            owner = f"**{s['owner']}** — " if s.get("owner") else ""
            parts.append(f"- {owner}{s['text']}{_cite(s.get('evidence', []))}")
        parts.append("")

    email = insights.get("follow_up_email")
    if email:
        parts.append("## Follow-up email\n")
        parts.append(f"**Subject:** {email.get('subject', '')}\n")
        parts.append(email.get("body", ""))
        parts.append("")

    return "\n".join(parts)
```

Then wire it into the three existing renderers, all of which already receive
`run`:

- `to_markdown` (`render.py:62`) — insert `render_insights_markdown(run.edited_insights or run.insights)` into the parts list immediately after the call header and before the agent-output sections.
- `export_json` (`render.py:84`) — add `"insights": run.edited_insights or run.insights,` to the returned dict.
- `share_snapshot` (`render.py:98`) — add the same key, so a shared call carries its summary.

Human edits win over the AI original in all three, matching the
`edited_output or output` precedent in `effective_agent_outputs`.

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/test_share.py -q`
Expected: PASS

- [ ] **Step 5: Full suite, then commit**

Run: `cd backend && uv run pytest -q`

```bash
git add backend/app/render.py backend/tests/test_share.py
git commit -m "feat: include the call summary in exports and shared snapshots"
```

---

> **Phase boundary.** Everything above is independently valuable: summaries are
> guaranteed, served, and exported. The frontend tasks below can be executed in a
> separate session.

---

## Task 8: Frontend types

**Files:**
- Modify: `web/lib/api.ts`
- Modify: `web/lib/status.ts`

**Interfaces:**
- Produces: `Evidence` (exists), `Claim`, `Objection`, `NextStep`, `Insights`; `CallSummary.agent_count`; `CallDetail.insights`; `AgentRunSummary.routing_reasoning`

- [ ] **Step 1: Add the types**

In `web/lib/api.ts`, after the existing `Evidence` type:

```ts
export type Claim = { text: string; evidence: Evidence[] };
export type Objection = { label: string; detail: string; status?: string; evidence: Evidence[] };
export type NextStep = { text: string; owner?: string; evidence: Evidence[] };

// The baseline every call gets. Produced by fixed pipeline stages, not agents.
export type Insights = {
  summary?: Claim[];
  objections?: Objection[];
  next_steps?: NextStep[];
  follow_up_email?: { subject: string; body: string };
  dropped_claims?: number;
};
```

Add `agent_count: number;` to `CallSummary`, `routing_reasoning: string | null;`
to `AgentRunSummary`, and `insights: Insights | null;` to `CallDetail`.

- [ ] **Step 2: Label the new stages**

In `web/lib/status.ts`, add to `stageLabels`:

```ts
  summarize: "Summary & next steps",
  compose_email: "Follow-up email",
```

- [ ] **Step 3: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: clean

- [ ] **Step 4: Commit**

```bash
git add web/lib/api.ts web/lib/status.ts
git commit -m "feat: type the guaranteed call insights"
```

---

## Task 9: The Summary panel

**Files:**
- Create: `web/components/Insights.tsx`

**Interfaces:**
- Consumes: `Insights` (Task 8); a `jumpTo(line: number) => void` callback supplied by `CallView`
- Produces: `export default function InsightsPanel({ insights, onJump }: { insights: Insights | null; onJump: (line: number) => void })`

- [ ] **Step 1: Create the component**

```tsx
"use client";

import type { Insights } from "@/lib/api";

function Cite({ evidence, onJump }: { evidence: { quote: string; line: number }[]; onJump: (line: number) => void }) {
  if (!evidence?.length) return null;
  return (
    <button className="cite" title={evidence[0].quote} onClick={() => onJump(evidence[0].line)}>
      ❝ proof
    </button>
  );
}

export default function InsightsPanel({
  insights,
  onJump,
}: {
  insights: Insights | null;
  onJump: (line: number) => void;
}) {
  if (!insights) {
    return (
      <div className="card text-sm text-neutral-500">
        No summary for this call yet.
      </div>
    );
  }

  const { summary, objections, next_steps, follow_up_email } = insights;

  return (
    <div className="space-y-6">
      {!!summary?.length && (
        <section className="card">
          <h2 className="eyebrow">Summary</h2>
          <ul className="mt-3 space-y-2 text-sm">
            {summary.map((c, i) => (
              <li key={i}>
                {c.text}
                <Cite evidence={c.evidence} onJump={onJump} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {!!next_steps?.length && (
        <section className="card">
          <h2 className="eyebrow">Next steps</h2>
          <ul className="mt-3 space-y-2 text-sm">
            {next_steps.map((s, i) => (
              <li key={i}>
                {s.owner && <span className="font-medium">{s.owner} — </span>}
                {s.text}
                <Cite evidence={s.evidence} onJump={onJump} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {!!objections?.length && (
        <section className="card">
          <h2 className="eyebrow">Objections</h2>
          <ul className="mt-3 space-y-3 text-sm">
            {objections.map((o, i) => (
              <li key={i}>
                <span className="font-medium">{o.label}</span>
                {o.status && <span className="ml-1.5 text-xs text-neutral-500">({o.status})</span>}
                <p className="mt-0.5 text-neutral-700">
                  {o.detail}
                  <Cite evidence={o.evidence} onJump={onJump} />
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {follow_up_email && <FollowUpEmail email={follow_up_email} />}
    </div>
  );
}

function FollowUpEmail({ email }: { email: { subject: string; body: string } }) {
  return (
    <section className="card">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="eyebrow">Follow-up email</h2>
        <button
          className="btn"
          onClick={() => navigator.clipboard.writeText(`Subject: ${email.subject}\n\n${email.body}`)}
        >
          Copy
        </button>
      </div>
      <p className="mt-3 text-sm font-medium">{email.subject}</p>
      <p className="mt-2 whitespace-pre-wrap text-sm text-neutral-700">{email.body}</p>
    </section>
  );
}
```

- [ ] **Step 2: Typecheck and lint**

Run: `cd web && npx tsc --noEmit && npx eslint components/Insights.tsx`
Expected: clean

- [ ] **Step 3: Commit**

```bash
git add web/components/Insights.tsx
git commit -m "feat: add the call summary panel"
```

---

## Task 10: Tabs on the call detail, and the no-skills-applied state

The page already has the layout: a `lg:col-span-7` main column beside a
`lg:col-span-5` sticky transcript rail. Add tabs over the left column only — the
rail must stay visible on both tabs, because `jumpTo` scrolls it.

**Files:**
- Modify: `web/components/CallView.tsx`

**Interfaces:**
- Consumes: `InsightsPanel` (Task 9), `data.insights`, `agentRun.routing_reasoning`
- Produces: no new exports

- [ ] **Step 1: Add tab state and render the tabs**

Import the panel and add state beside the existing `useState` calls:

```tsx
import InsightsPanel from "./Insights";

const [tab, setTab] = useState<"summary" | "agents">("summary");
```

Replace the contents of the `lg:col-span-7` column with:

```tsx
        <div className="space-y-6 lg:col-span-7">
          <div className="flex gap-1 border-b border-neutral-200">
            {([["summary", "Summary"], ["agents", `Agent runs`]] as const).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`-mb-px border-b-2 px-3 py-2 text-sm ${
                  tab === key
                    ? "border-neutral-900 font-medium text-neutral-900"
                    : "border-transparent text-neutral-500 hover:text-neutral-800"
                }`}
              >
                {label}
                {key === "agents" && agent_runs.length > 0 && (
                  <span className="ml-1.5 rounded-full bg-neutral-100 px-1.5 py-0.5 text-xs text-neutral-600">
                    {agent_runs.length}
                  </span>
                )}
              </button>
            ))}
          </div>

          {tab === "summary" ? (
            <InsightsPanel insights={data.insights} onJump={jumpTo} />
          ) : (
            <>
              {agent_runs.map((ar) => (
                <AgentRunCard key={ar.id} callId={id} agentRun={ar} onChanged={load} />
              ))}
              {agent_runs.length === 0 && (
                <div className="card text-sm text-neutral-500">
                  No agents ran on this call.
                  {run.orchestrator_reasoning && ` ${run.orchestrator_reasoning}`}
                </div>
              )}
            </>
          )}

          <ProcessingDetails run={run} />
        </div>
```

`jumpTo` is already defined at module scope in this file (line ~23) — pass it
straight through. If `data` is destructured such that `insights` is not in scope,
add it to the existing destructure: `const { call, run, transcript, agent_runs, insights } = data;`
and pass `insights={insights}`.

- [ ] **Step 2: Explain an agent run that did nothing**

In `AgentRunCard`, replace the output render (currently
`Object.entries(agentRun.output ?? {}).map(...)`, around line 129) with:

```tsx
        agentRun.output && Object.keys(agentRun.output).length > 0 ? (
          Object.entries(agentRun.output).map(([skillName, fields]) => (
            <SkillOutput key={skillName} skillName={skillName} fields={fields} />
          ))
        ) : (
          <p className="text-sm text-neutral-500">
            No skills applied.
            {agentRun.routing_reasoning && ` ${agentRun.routing_reasoning}`}
          </p>
        )
```

- [ ] **Step 3: Typecheck and lint**

Run: `cd web && npx tsc --noEmit && npx eslint components/CallView.tsx`
Expected: clean

- [ ] **Step 4: Verify in the running app**

Open a pre-cutover call (Brightline discovery). Confirm: Summary is the default
tab and populated; clicking `❝ proof` scrolls **and flashes** the transcript line
in the rail without leaving the tab; the Agent runs tab shows a count badge; an
agent run with no output reads "No skills applied" with its reason.

- [ ] **Step 5: Commit**

```bash
git add web/components/CallView.tsx
git commit -m "feat: tab the call detail into Summary and Agent runs"
```

---

## Task 11: The call log

**Files:**
- Modify: `web/app/page.tsx`

**Interfaces:**
- Consumes: `CallSummary` with `agent_count` (Task 8), existing `uploadCall` / `ingestUrl` / `listCalls` / `getStatus`
- Produces: no new exports

- [ ] **Step 1: Rebuild the page as a filterable table**

Keep the existing ingest logic — `ingest()`, `duplicateOf`, `error`, the polling
`useEffect` — untouched. Change only the layout: move the drop zone behind an
**Add call** toggle, and render the list as a table.

```tsx
const [showAdd, setShowAdd] = useState(false);
const [q, setQ] = useState("");
const [sourceFilter, setSourceFilter] = useState("all");
const [statusFilter, setStatusFilter] = useState("all");
const [days, setDays] = useState(30);

const visible = calls.filter((c) => {
  if (q && !c.title.toLowerCase().includes(q.toLowerCase())) return false;
  if (sourceFilter !== "all" && c.source !== sourceFilter) return false;
  if (statusFilter !== "all" && c.run_status !== statusFilter) return false;
  if (days > 0) {
    const age = (Date.now() - new Date(c.recorded_at).getTime()) / 86400000;
    if (age > days) return false;
  }
  return true;
});
```

Toolbar, above the table:

```tsx
<div className="flex flex-wrap items-center gap-2">
  <input
    value={q}
    onChange={(e) => setQ(e.target.value)}
    placeholder="Search calls"
    className="min-w-48 flex-1 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm"
  />
  <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="rounded-lg border border-neutral-300 px-2 py-1.5 text-sm">
    <option value={7}>Last 7 days</option>
    <option value={30}>Last 30 days</option>
    <option value={0}>All time</option>
  </select>
  <select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)} className="rounded-lg border border-neutral-300 px-2 py-1.5 text-sm">
    <option value="all">Any source</option>
    <option value="upload">Upload</option>
    <option value="url">Link</option>
    <option value="sample">Sample</option>
  </select>
  <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="rounded-lg border border-neutral-300 px-2 py-1.5 text-sm">
    <option value="all">Any status</option>
    <option value="shipped">Ready</option>
    <option value="partial">Needs review</option>
    <option value="failed">Couldn’t finish</option>
    <option value="running">Analyzing</option>
  </select>
  <button onClick={() => setShowAdd((v) => !v)} className="btn btn-primary">
    {showAdd ? "Close" : "Add call"}
  </button>
</div>
```

Render the existing drop zone (the whole `<div onDragOver=…>` block, unchanged,
including the error and `duplicateOf` lines) only `{showAdd && ( … )}`.

Then the table:

```tsx
<table className="mt-6 w-full text-sm">
  <thead>
    <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-400">
      <th className="py-2 font-semibold">Date</th>
      <th className="py-2 font-semibold">Call</th>
      <th className="py-2 font-semibold">Source</th>
      <th className="py-2 font-semibold">Length</th>
      <th className="py-2 font-semibold">Status</th>
      <th className="py-2 text-right font-semibold">Agents</th>
    </tr>
  </thead>
  <tbody>
    {visible.map((c) => {
      const st = humanizeStatus(c.run_status);
      return (
        <tr key={c.id} className="border-b border-neutral-100 hover:bg-neutral-50">
          <td className="py-3 whitespace-nowrap text-neutral-500">
            {new Date(c.recorded_at).toLocaleDateString()}
          </td>
          <td className="py-3">
            <Link href={`/calls/${c.id}`} className="font-medium hover:underline">
              {c.title}
            </Link>
          </td>
          <td className="py-3 text-neutral-500">{c.source}</td>
          <td className="py-3 whitespace-nowrap text-neutral-500">{formatDuration(c.duration_s)}</td>
          <td className="py-3">
            <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${tonePill[st.tone]}`}>
              {st.busy && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
              {st.label}
            </span>
          </td>
          <td className="py-3 text-right text-neutral-500">{c.agent_count || "—"}</td>
        </tr>
      );
    })}
  </tbody>
</table>
{visible.length === 0 && (
  <p className="mt-6 rounded-xl border border-dashed border-neutral-200 px-5 py-10 text-center text-sm text-neutral-400">
    {calls.length === 0 ? "No calls yet — choose Add call to get started." : "No calls match these filters."}
  </p>
)}
```

Widen the page shell from `max-w-3xl` to `max-w-5xl` so six columns fit.

- [ ] **Step 2: Typecheck and lint**

Run: `cd web && npx tsc --noEmit && npx eslint app/page.tsx`
Expected: clean

- [ ] **Step 3: Verify in the running app**

Filter by source and by status; set All time and confirm the samples appear;
open **Add call**, re-paste an already-ingested link and confirm the duplicate
notice still shows; confirm the Agents column reads `—` for calls with none.

- [ ] **Step 4: Commit**

```bash
git add web/app/page.tsx
git commit -m "feat: turn the home page into a filterable call log"
```

---

## Task 12 (OPTIONAL — strike if unwanted)

Layout A has no summary snippet, so a call titled from a bare recording ID
(`REbe3fec20e15a80aa8d5a97bd81adcdd8` — 4 of the current 12) is unreadable in the
log. Derive a short title once `summarize` has run.

**Files:**
- Modify: `backend/app/pipeline.py`
- Test: `backend/tests/test_baseline_pipeline.py`

- [ ] **Step 1: Write the failing test**

```python
def test_identifier_titles_are_replaced_after_summarizing(monkeypatch):
    monkeypatch.setattr(
        insights.llm,
        "complete_json",
        fake_llm({"extract": {
            "summary": [{"text": "Caller asked to be transferred to billing.",
                         "evidence": [{"quote": "Thanks for making the time.", "line": 1}]}],
            "objections": [], "next_steps": [],
        }}),
    )
    with TestClient(app) as c:
        call_id = c.post("/api/ingest/url", json={"url": "https://x.test/stream/CA1/RE0123456789abcdef0123456789abcdef"}).json()["call_id"]
        _drain()
        assert c.get(f"/api/calls/{call_id}").json()["call"]["title"] != "RE0123456789abcdef0123456789abcdef"
```

> This test performs a URL ingest, so it needs the `url_ingest` transport fixture
> from `tests/test_ingest.py`. Move that fixture into `tests/conftest.py` first
> so both modules can use it, rather than duplicating it.

- [ ] **Step 2: Run it to verify it fails**, then implement. Add `import re` to
`app/pipeline.py`, then add beside `_persist_baseline`:

```python
_ID_TITLE = re.compile(r"^(RE|CA)?[0-9a-f]{16,}$", re.I)


def _derive_title(current: str, baseline: dict) -> str | None:
    """A bare recording id tells a reader nothing. Use the first summary claim."""
    if not _ID_TITLE.match(current or ""):
        return None
    claims = baseline.get("summary") or []
    if not claims:
        return None
    text = claims[0]["text"].strip()
    return text[:70].rstrip(" .,;:") if text else None
```

Call it in `_persist_baseline` where the `Run` is loaded, updating
`run.call.title` only when it returns a value.

- [ ] **Step 3: Run the tests, then commit**

```bash
git add backend/app/pipeline.py backend/tests/
git commit -m "feat: derive a readable title for recording-id calls"
```

---

## Final verification

1. `cd backend && uv run pytest -q` — all green.
2. `cd web && npx tsc --noEmit && npx eslint app components lib`
   (`app/agents/page.tsx:36` carries a pre-existing
   `react-hooks/set-state-in-effect` error unrelated to this work.)
3. Ingest a fresh call with **no agent enabled**. Confirm summary, next steps and
   follow-up email all appear — that is the guarantee.
4. Open a pre-cutover sample call: Summary populated from stored data, `❝ proof`
   scrolls and flashes the rail.
5. Export `.md` and `.json`; confirm the summary and `[L…]` citations are present.
6. Create a share link and confirm the shared page carries the summary.
7. Call log: every filter, the Agents column, and Add call with a duplicate link.
