# Agent & Skill Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Open Gong's hardcoded 8-stage insight pipeline with three user-configurable objects — Orchestrator, Agent, Skill — while preserving every existing trust guarantee (evidence gate, capped retries, budget cap, shipped/partial/failed), and prove parity by reproducing today's summarizer behavior as a seeded "Call Summarizer" agent.

**Architecture:** New, purely additive modules (skill `.md` parser, generalized evidence gate, a per-`AgentRun` retry/budget harness, a generic skill executor, two LLM routing calls) are built and unit-tested in isolation first. One integration task then rewires `pipeline.py`'s `run_insights` to use them, migrates the API surface off `Run.insights`/`Run.compliance` onto `AgentRun`, and seeds the built-in skills + Call Summarizer agent. CRUD APIs and a functional (non-visual-redesign) frontend land last.

**Tech Stack:** Python/FastAPI backend (existing), SQLAlchemy models, PyYAML for skill frontmatter parsing (new dependency — see Task 2), Next.js/React frontend (existing), Tailwind (existing, no new design pass).

**Spec:** `docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md` — read it before starting; this plan implements it section-by-section and cross-references it below.

## Global Constraints

- Agents run only skills attached to them, steered by an editable system prompt — no open-ended tool-calling loop (spec Context).
- A skill is one `.md` file: YAML frontmatter (`name`, `description`, `when_to_use`, optional `fields:` with `checks`/`scores`/`claims` sub-keys) + a prose body (spec §2).
- Anything declared under a skill's `fields:` goes through the evidence gate unchanged — a claim without a verifiable transcript quote is dropped, never fabricated (spec §2, §4).
- Two LLM routing calls, each steerable by an editable prompt: orchestrator dispatch (call → agents) and agent skill-router (agent + call → skills) (spec §1).
- The orchestrator dispatches lazily; an `EntryRule` match bypasses the orchestrator call entirely and pins a single agent — this plan implements the model and the bypass check, not the `EntryRule` UI (spec §4, Non-goals).
- One `AgentRun` per agent per call, each with its own steps/budget/status/retry (spec §4).
- `AgentRun.parent_agent_run_id` exists as a column but nothing sets or reads it in this phase — it is a P3 seam only (spec §4, Non-goals).
- Per-`AgentRun` budget cap AND the existing per-call aggregate cap both apply; exceeding either finalizes cleanly with no zombie runs (spec §4, §5).
- Two edge cases are decided, not incidental: zero agents selected → `Run` completes `shipped` with reasoning stored; a skill's fielded output failing validation drops that claim/step, never the whole call (spec §4).
- No CRM Automation agent, no side-effecting actions, no multi-CRM work — the HubSpot branch stays untouched and unmerged; it lands in P2 (spec Non-goals).
- No `EntryRule` configuration UI in this phase (spec Non-goals).
- No visual redesign — CRUD/left-nav screens in this plan are functional Tailwind UI at today's visual bar, not an Impeccable-designed pass (spec Non-goals).
- The five sample calls are re-seeded by running them through the new agent path for real (API keys required); a fresh keyless clone will not see populated samples until this is revisited — known and accepted (spec Known risks #1).
- `packs.py`, `pack_compiler.py`, `frameworks.py`, the `InsightPack` model, and `api/packs.py` are **not deleted** in this plan — they become unrouted (nothing in the new agent path calls them) but are left in place, since the spec treats the prose-compiler as an optional future authoring assist, not a required P1 integration. Do not wire them up; do not delete them. This is a deliberate scope boundary, not an oversight.
- This is a restructure forked from `main` (commit `0a69c98`), not from the unmerged `worktree-hubspot-crm-sync` branch — that branch stays isolated exactly as previously decided, and its CRM adapter/content-builder/`CrmSync` table are untouched here (they merge in P2).

---

## File Structure

New backend modules (all additive until Task 9 wires them in):

- `backend/app/skills/__init__.py`, `backend/app/skills/loader.py` — parse a skill `.md` string into a structured dict (Task 2).
- `backend/app/skills/executor.py` — run one skill against a transcript: build a JSON schema from its `fields:`, call the LLM gateway, gate the output (Task 5).
- `backend/app/agent_runtime.py` — the per-`AgentRun` retry/budget harness, generalizing `run_state.py`'s guarantees to a dynamic step list (Task 4).
- `backend/app/orchestrator.py` — orchestrator dispatch: call → which agents (Task 6).
- `backend/app/skill_router.py` — agent skill-router: agent + call → which of its skills (Task 7).
- `backend/app/entry_rules.py` — resolve a pinned agent from `EntryRule`, bypassing the orchestrator (Task 8).
- `backend/app/api/agents.py`, `backend/app/api/skills.py` — CRUD routers (Task 10).

Modified: `backend/app/models.py` (Task 1, Task 9), `backend/app/evidence.py` (Task 3), `backend/app/pipeline.py`, `backend/app/main.py`, `backend/app/api/review.py`, `backend/app/api/share.py`, `backend/app/render.py`, `backend/app/api/ingest.py`, `backend/scripts/seed.py`, `backend/pyproject.toml` (Task 9).

Frontend: `web/components/Nav.tsx` (new, Task 11), `web/app/layout.tsx`, `web/app/agents/page.tsx` (new), `web/app/skills/page.tsx` (new), `web/lib/api.ts` (Task 11).

---

### Task 1: Data model — Agent, Skill, AgentSkill, Orchestrator, AgentRun, EntryRule

**Files:**
- Modify: `backend/app/models.py`
- Test: `backend/tests/test_models_agents.py` (create)

**Interfaces:**
- Consumes: `Base`, `_uuid`, `_now` (already in `app/models.py`).
- Produces:
  - `Agent(id, name, description, system_prompt, enabled, created_at)`
  - `Skill(id, name, description, when_to_use, body_md, fields: dict | None, source, version, created_at)`
  - `AgentSkill(id, agent_id, skill_id)`
  - `Orchestrator(id, system_prompt, enabled)`
  - `AgentRun(id, call_id, run_id, agent_id, parent_agent_run_id: str | None, status, steps: list, output: dict | None, edited_output: dict | None, cost_usd, error: str | None, created_at, finished_at: datetime | None)`
  - `EntryRule(id, match_kind, match_value, agent_id)`

This is purely additive — no existing table or column changes. Every later task depends on these six tables existing.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_models_agents.py`:

```python
"""New Agent/Skill/Orchestrator/AgentRun/EntryRule tables (Task 1 of the
agent-skill architecture plan). Purely additive — see
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §4.
"""

from app.db import get_session
from app.models import Agent, AgentRun, AgentSkill, EntryRule, Orchestrator, Skill


def test_agent_round_trips():
    with get_session() as session:
        agent = Agent(
            name="Call Summarizer",
            description="Summarizes calls and scores them against sales/support rubrics",
            system_prompt="Use the sales skill for sales calls, support skill for support calls.",
        )
        session.add(agent)
        session.commit()
        agent_id = agent.id

    with get_session() as session:
        row = session.get(Agent, agent_id)
        assert row.name == "Call Summarizer"
        assert row.enabled is True
        assert row.created_at is not None


def test_skill_round_trips_with_fields():
    with get_session() as session:
        skill = Skill(
            name="sales-scorecard",
            description="Scores discovery quality and MEDDIC coverage on sales calls",
            when_to_use="The call is a sales conversation",
            body_md="Score this call against MEDDIC...",
            fields={
                "checks": ["budget_discussed"],
                "scores": [{"name": "discovery_quality", "max": 5}],
                "claims": ["summary", "next_steps"],
            },
            source="ui",
        )
        session.add(skill)
        session.commit()
        skill_id = skill.id

    with get_session() as session:
        row = session.get(Skill, skill_id)
        assert row.fields["checks"] == ["budget_discussed"]
        assert row.fields["scores"][0]["max"] == 5
        assert row.version == 1
        assert row.source == "ui"


def test_skill_fields_defaults_to_none_for_narrative_only_skill():
    with get_session() as session:
        skill = Skill(name="plain-summary", description="d", when_to_use="w", body_md="Summarize.", source="upload")
        session.add(skill)
        session.commit()
        skill_id = skill.id

    with get_session() as session:
        assert session.get(Skill, skill_id).fields is None


def test_agent_skill_join_links_agent_to_skill():
    with get_session() as session:
        agent = Agent(name="a", description="d", system_prompt="p")
        skill = Skill(name="s", description="d", when_to_use="w", body_md="b", source="ui")
        session.add_all([agent, skill])
        session.flush()
        session.add(AgentSkill(agent_id=agent.id, skill_id=skill.id))
        session.commit()
        agent_id, skill_id = agent.id, skill.id

    with get_session() as session:
        from sqlalchemy import select

        link = session.scalars(
            select(AgentSkill).where(AgentSkill.agent_id == agent_id, AgentSkill.skill_id == skill_id)
        ).first()
        assert link is not None


def test_orchestrator_singleton_round_trips():
    with get_session() as session:
        orch = Orchestrator(system_prompt="Decide which agents this call needs.")
        session.add(orch)
        session.commit()
        orch_id = orch.id

    with get_session() as session:
        row = session.get(Orchestrator, orch_id)
        assert row.enabled is True
        assert "which agents" in row.system_prompt


def test_agent_run_round_trips_with_parent_seam_unset():
    with get_session() as session:
        agent = Agent(name="a", description="d", system_prompt="p")
        session.add(agent)
        session.flush()
        agent_run = AgentRun(
            call_id="call-1",
            run_id="run-1",
            agent_id=agent.id,
            status="shipped",
            steps=[{"name": "sales-scorecard", "status": "ok", "attempts": 1, "cost_usd": 0.01, "error": None}],
            output={"summary": []},
            cost_usd=0.01,
        )
        session.add(agent_run)
        session.commit()
        agent_run_id = agent_run.id

    with get_session() as session:
        row = session.get(AgentRun, agent_run_id)
        assert row.status == "shipped"
        assert row.parent_agent_run_id is None
        assert row.edited_output is None
        assert row.finished_at is None
        assert row.steps[0]["name"] == "sales-scorecard"


def test_entry_rule_round_trips():
    with get_session() as session:
        agent = Agent(name="Support Triage", description="d", system_prompt="p")
        session.add(agent)
        session.flush()
        rule = EntryRule(match_kind="phone_line", match_value="+15550001111", agent_id=agent.id)
        session.add(rule)
        session.commit()
        rule_id = rule.id

    with get_session() as session:
        row = session.get(EntryRule, rule_id)
        assert row.match_kind == "phone_line"
        assert row.match_value == "+15550001111"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_models_agents.py -v`
Expected: FAIL with `ImportError: cannot import name 'Agent' from 'app.models'`.

- [ ] **Step 3: Add the six new tables**

In `backend/app/models.py`, add after the `ShareLink` class (end of file — this branch, forked from `main`, has no `CrmSync` class; do not look for one):

```python
class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)  # what the orchestrator sees
    system_prompt: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    when_to_use: Mapped[str] = mapped_column(Text)
    body_md: Mapped[str] = mapped_column(Text)
    # {"checks": [...], "scores": [{"name","max"}], "claims": [...]} or None (narrative-only)
    fields: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String)  # ui | upload
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentSkill(Base):
    __tablename__ = "agent_skills"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"))


class Orchestrator(Base):
    __tablename__ = "orchestrators"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    system_prompt: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    # P3 seam — unset and unread until agent-to-agent invocation (P3) lands
    parent_agent_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending | shipped | partial | failed
    # [{"name","status","attempts","cost_usd","error","dropped_claims"}] — one per skill run
    steps: Mapped[list] = mapped_column(JSON, default=list)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # immutable AI output, keyed by skill name
    edited_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # human review edits
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EntryRule(Base):
    __tablename__ = "entry_rules"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    match_kind: Mapped[str] = mapped_column(String)  # phone_line | source
    match_value: Mapped[str] = mapped_column(String)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_models_agents.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/tests/test_models_agents.py
git commit -m "feat: add Agent/Skill/Orchestrator/AgentRun/EntryRule tables"
```

---

### Task 2: Skill file parser

**Files:**
- Create: `backend/app/skills/__init__.py` (empty)
- Create: `backend/app/skills/loader.py`
- Test: `backend/tests/test_skill_loader.py` (create)
- Modify: `backend/pyproject.toml` (add `pyyaml` dependency)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `parse_skill_md(text: str) -> dict` — returns `{"name": str, "description": str, "when_to_use": str, "fields": dict | None, "body": str}`.
  - `SkillParseError(Exception)` — raised on missing frontmatter or missing required frontmatter keys.

The `fields:` sub-keys (`checks`, `scores`, `claims`) are the exact shape every later task (evidence gate, executor, seeding) assumes — this task is the single source of truth for that shape.

- [ ] **Step 1: Add the YAML dependency**

In `backend/pyproject.toml`, add `pyyaml` to `dependencies` (alongside the existing `httpx`, `jsonschema` entries — match the existing list's formatting exactly).

Run: `cd backend && uv sync`
Expected: `pyyaml` installed, lockfile updated.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_skill_loader.py`:

```python
"""Skill .md parser (Task 2 of the agent-skill architecture plan).
See docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §2.
"""

import pytest

from app.skills.loader import SkillParseError, parse_skill_md

FULL_SKILL = """---
name: sales-scorecard
description: Scores discovery quality and MEDDIC coverage on sales calls
when_to_use: The call is a sales conversation - discovery, demo, pricing, or negotiation
fields:
  checks:
    - budget_discussed
    - economic_buyer_identified
  scores:
    - name: discovery_quality
      max: 5
  claims:
    - summary
    - next_steps
---

Score this call against MEDDIC. For each check, cite the exact line.
"""

NARRATIVE_ONLY_SKILL = """---
name: plain-summary
description: Summarizes the call in plain prose
when_to_use: Always, as a fallback when no other skill applies
---

Write a short plain-English summary of this call.
"""


def test_parses_full_frontmatter_and_fields():
    result = parse_skill_md(FULL_SKILL)
    assert result["name"] == "sales-scorecard"
    assert result["description"] == "Scores discovery quality and MEDDIC coverage on sales calls"
    assert result["when_to_use"].startswith("The call is a sales conversation")
    assert result["fields"]["checks"] == ["budget_discussed", "economic_buyer_identified"]
    assert result["fields"]["scores"] == [{"name": "discovery_quality", "max": 5}]
    assert result["fields"]["claims"] == ["summary", "next_steps"]
    assert result["body"].strip().startswith("Score this call against MEDDIC")


def test_parses_narrative_only_skill_with_no_fields():
    result = parse_skill_md(NARRATIVE_ONLY_SKILL)
    assert result["name"] == "plain-summary"
    assert result["fields"] is None
    assert "plain-English summary" in result["body"]


def test_raises_when_frontmatter_missing():
    with pytest.raises(SkillParseError, match="no YAML frontmatter"):
        parse_skill_md("Just a body, no frontmatter at all.")


def test_raises_when_required_key_missing():
    bad = "---\nname: incomplete\n---\n\nBody text.\n"
    with pytest.raises(SkillParseError, match="missing required frontmatter key"):
        parse_skill_md(bad)


def test_raises_on_malformed_yaml():
    bad = "---\nname: [unterminated\n---\n\nBody.\n"
    with pytest.raises(SkillParseError, match="invalid YAML frontmatter"):
        parse_skill_md(bad)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_skill_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.skills'`.

- [ ] **Step 4: Implement the parser**

Create `backend/app/skills/__init__.py` (empty file).

Create `backend/app/skills/loader.py`:

```python
"""Parse a skill .md file into a structured dict.

Format: YAML frontmatter (name, description, when_to_use, optional fields:
with checks/scores/claims sub-keys) + a prose body. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §2.
"""

import yaml

REQUIRED_KEYS = ("name", "description", "when_to_use")


class SkillParseError(Exception):
    pass


def parse_skill_md(text: str) -> dict:
    """Returns {"name", "description", "when_to_use", "fields", "body"}.

    "fields" is the raw {"checks": [...], "scores": [...], "claims": [...]}
    dict (any subset, any absent) or None if the skill declares no fields at
    all (narrative-only output)."""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        raise SkillParseError("no YAML frontmatter found (must start with '---')")

    rest = stripped[3:]
    end = rest.find("\n---")
    if end == -1:
        raise SkillParseError("no YAML frontmatter found (missing closing '---')")

    raw_frontmatter = rest[:end]
    body = rest[end + 4 :].lstrip("\n")

    try:
        frontmatter = yaml.safe_load(raw_frontmatter)
    except yaml.YAMLError as e:
        raise SkillParseError(f"invalid YAML frontmatter: {e}") from e

    if not isinstance(frontmatter, dict):
        raise SkillParseError("frontmatter must be a YAML mapping")

    for key in REQUIRED_KEYS:
        if key not in frontmatter:
            raise SkillParseError(f"missing required frontmatter key: {key!r}")

    return {
        "name": frontmatter["name"],
        "description": frontmatter["description"],
        "when_to_use": frontmatter["when_to_use"],
        "fields": frontmatter.get("fields"),
        "body": body,
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_skill_loader.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/skills/__init__.py backend/app/skills/loader.py backend/tests/test_skill_loader.py
git commit -m "feat: add skill .md frontmatter parser"
```

---

### Task 3: Generalize the evidence gate over arbitrary skill fields

**Files:**
- Modify: `backend/app/evidence.py`
- Test: `backend/tests/test_evidence_fields.py` (create)

**Interfaces:**
- Consumes: `_check_evidence(evidence, lines) -> (bool, str | None)` (existing, `app/evidence.py`); a skill's `fields` dict shape from Task 2 (`{"checks": [names], "scores": [{"name","max"}], "claims": [names]}`).
- Produces: `validate_fields(fields_spec: dict, raw_output: dict, transcript_lines: list[dict]) -> tuple[dict, list[dict]]` — returns `(cleaned, dropped)` where `dropped = [{"where", "reason"}]`, same contract as the existing `validate_extraction`.

This is the same "no proof, no claim" logic `validate_extraction` already enforces, generalized to walk a skill's declared `checks`/`scores`/`claims` names instead of one hardcoded pack schema. `_check_evidence` is reused verbatim — nothing about evidence verification itself changes.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_evidence_fields.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_evidence_fields.py -v`
Expected: FAIL — `ImportError: cannot import name 'validate_fields' from 'app.evidence'`.

- [ ] **Step 3: Implement `validate_fields`**

In `backend/app/evidence.py`, add at the end of the file:

```python
def validate_fields(fields_spec: dict, raw_output: dict, transcript_lines: list[dict]) -> tuple[dict, list[dict]]:
    """Generalized gate over a skill's declared fields: {checks, scores, claims}.

    Same "no proof, no claim" contract as validate_extraction:
    - checks (flags): value=true REQUIRES verifying evidence (unproven "yes"
      becomes null); value=false/null may have empty evidence.
    - scores: always require verifying evidence; a bad score is dropped
      entirely (not nulled — there's no meaningful "unscored" placeholder
      the caller should render).
    - claims (lists): items with bad evidence are removed from the list.

    A field absent from raw_output is treated as absent, not an error — the
    skill's own LLM call is responsible for completeness; this gate only
    verifies what evidence backs whatever WAS returned.
    """
    lines = {l["line"]: l["text"] for l in transcript_lines}
    cleaned: dict = {}
    dropped: list[dict] = []

    for name in fields_spec.get("checks", []):
        if name not in raw_output:
            continue
        value = raw_output[name]
        evidence = value.get("evidence", []) if isinstance(value, dict) else []
        if isinstance(value, dict) and value.get("value") is True:
            ok, reason = _check_evidence(evidence, lines)
            if ok and evidence:
                cleaned[name] = value
            else:
                dropped.append({"where": name, "reason": reason or "true claim without evidence"})
                cleaned[name] = {"value": None, "evidence": []}
        elif isinstance(value, dict):
            ok, reason = _check_evidence(evidence, lines)
            cleaned[name] = value if ok else {"value": value.get("value"), "evidence": []}
            if not ok:
                dropped.append({"where": f"{name}.evidence", "reason": reason})

    for spec in fields_spec.get("scores", []):
        name = spec["name"]
        if name not in raw_output:
            continue
        value = raw_output[name]
        evidence = value.get("evidence", []) if isinstance(value, dict) else []
        ok, reason = _check_evidence(evidence, lines)
        if ok and evidence:
            cleaned[name] = value
        else:
            dropped.append({"where": name, "reason": reason or "score without evidence"})

    for name in fields_spec.get("claims", []):
        if name not in raw_output:
            cleaned[name] = []
            continue
        items = raw_output[name] if isinstance(raw_output[name], list) else []
        kept = []
        for i, claim in enumerate(items):
            evidence = claim.get("evidence", []) if isinstance(claim, dict) else []
            ok, reason = _check_evidence(evidence, lines)
            if ok and evidence:
                kept.append(claim)
            else:
                dropped.append({"where": f"{name}[{i}]", "reason": reason or "no evidence provided"})
        cleaned[name] = kept

    if not fields_spec:
        return dict(raw_output), []

    return cleaned, dropped
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_evidence_fields.py -v`
Expected: 7 passed.

- [ ] **Step 5: Run the full existing evidence suite to confirm no regression**

Run: `cd backend && uv run pytest tests/test_insights.py -k evidence -v` (or the closest existing evidence-focused tests)
Expected: unchanged, all passing — `validate_extraction` itself was not touched.

- [ ] **Step 6: Commit**

```bash
git add backend/app/evidence.py backend/tests/test_evidence_fields.py
git commit -m "feat: generalize the evidence gate over arbitrary skill fields"
```

---

### Task 4: Per-AgentRun retry/budget harness

**Files:**
- Create: `backend/app/agent_runtime.py`
- Test: `backend/tests/test_agent_runtime.py` (create)

**Interfaces:**
- Consumes: `BudgetExceeded`, `StageFailed`, `MAX_ATTEMPTS`, `max_cost_per_run` (all existing, unmodified, from `app/run_state.py` — these are already step-name-agnostic primitives, not tied to the fixed `STAGES` list).
- Produces:
  - `AgentStepState` dataclass: `name, status, attempts, cost_usd, error, dropped_claims`.
  - `AgentRunState` dataclass: `steps: list[AgentStepState]`, `budget: float`, with methods `charge(step_name, cost)`, `execute(step_name, fn)`, `final_status() -> str`, `as_dicts() -> list[dict]`, `_get(name)`.
  - `def new_agent_run_state(step_names: list[str]) -> AgentRunState` — the constructor entry point (a plain `@dataclass` can't take a runtime list as a required positional default the way `RunState` does with its module-global `STAGES`, so this is a small factory instead).

This mirrors `run_state.RunState` exactly, except the step list is passed in per call instead of read from a module-global `STAGES` constant, and `final_status()` has no `CRITICAL_STAGES` concept (skills are user-configured — there is no fixed "critical" skill). Nothing in `run_state.py` is modified by this task; Task 9 is where the old `STAGES`-based harness is retired.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_agent_runtime.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_agent_runtime.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent_runtime'`.

- [ ] **Step 3: Implement the harness**

Create `backend/app/agent_runtime.py`:

```python
"""Per-AgentRun retry/budget harness: the same guarantees as
run_state.RunState (capped retries, budget cap, a clear terminal status),
generalized to a dynamic step list built from one agent's routed skills
instead of the fixed global STAGES list. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §4.

Skills are user-configured, so there is no fixed "critical" skill the way
transcribe/extract/validate are critical stages today: an AgentRun is
`failed` only when every one of its steps failed to ship anything.
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
        """shipped | partial | failed — no per-step "critical" concept:
        skills are user-configured, so an AgentRun is failed only when
        nothing shipped from it at all."""
        if not self.steps:
            return "shipped"
        shipped_any = any(s.status == "ok" for s in self.steps)
        if not shipped_any:
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


def new_agent_run_state(step_names: list[str]) -> AgentRunState:
    return AgentRunState(steps=[AgentStepState(n) for n in step_names])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_agent_runtime.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_runtime.py backend/tests/test_agent_runtime.py
git commit -m "feat: add per-AgentRun retry/budget harness"
```

---

### Task 5: Skill executor — run one skill against a transcript

**Files:**
- Create: `backend/app/skills/executor.py`
- Test: `backend/tests/test_skill_executor.py` (create)
- Modify: `backend/tests/fakes.py` (extend `fake_llm` with a skill-exec marker)

**Interfaces:**
- Consumes: `parse_skill_md`'s `fields` shape (Task 2); `llm.complete_json(system, user, schema, max_tokens) -> (dict, float)` (existing, unmodified); `evidence.validate_fields(fields_spec, raw_output, transcript_lines) -> (cleaned, dropped)` (Task 3).
- Produces: `run_skill(skill: dict, transcript_lines: list[dict]) -> tuple[dict, list[dict], float]` — `skill` is a dict with `name, description, when_to_use, body_md, fields` (the `Skill` ORM row's fields, or the parsed-`.md` dict shape — same keys). Returns `(cleaned_output, dropped, cost)`.

This builds a JSON schema from the skill's `fields:` declaration (generalizing `packs.py`'s `_flag`/`_COMMON_PROPERTIES`/judgment-schema construction to arbitrary field names) and drives one `llm.complete_json` call using the skill's `body_md` as the user-facing instructions, then gates the result through Task 3.

- [ ] **Step 1: Add the fake-LLM marker for skill execution**

In `backend/tests/fakes.py`, add a new response key and a matching branch. This file is shared across the whole test suite, so extend rather than replace:

```python
GOOD_SKILL_OUTPUT: dict = {}


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
        if "Run this skill:" in user:
            r = responses.get("skill", GOOD_SKILL_OUTPUT)
            if isinstance(r, Exception):
                raise r
            return r, 0.01
        raise AssertionError(f"unexpected prompt: {user[:80]}")

    return fake
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_skill_executor.py`:

```python
"""Skill executor: build a schema from fields:, run one LLM call, gate the
output (Task 5 of the agent-skill architecture plan). See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §2.
"""

import app.llm as llm_mod
from app.skills.executor import run_skill
from fakes import fake_llm

LINES = [
    {"line": 1, "speaker": "Ana", "text": "This call is recorded, just so you know."},
    {"line": 2, "speaker": "Bob", "text": "Our budget for this is around fifty thousand a year."},
]

SKILL = {
    "name": "sales-scorecard",
    "description": "Scores discovery quality on sales calls",
    "when_to_use": "The call is a sales conversation",
    "body_md": "Score this call against MEDDIC. Cite evidence for every field.",
    "fields": {
        "checks": ["budget_discussed"],
        "scores": [{"name": "discovery_quality", "max": 5}],
        "claims": ["summary"],
    },
}


def test_run_skill_builds_schema_and_gates_output(monkeypatch):
    good_output = {
        "budget_discussed": {"value": True, "evidence": [{"quote": "fifty thousand a year", "line": 2}]},
        "discovery_quality": {"score": 4, "justification": "Solid discovery.", "evidence": [{"quote": "budget for this", "line": 2}]},
        "summary": [{"text": "Budget of $50k/year discussed.", "evidence": [{"quote": "fifty thousand a year", "line": 2}]}],
    }
    monkeypatch.setattr(llm_mod, "complete_json", fake_llm({"skill": good_output}))

    cleaned, dropped, cost = run_skill(SKILL, LINES)

    assert cleaned["budget_discussed"]["value"] is True
    assert cleaned["discovery_quality"]["score"] == 4
    assert cleaned["summary"][0]["text"] == "Budget of $50k/year discussed."
    assert dropped == []
    assert cost == 0.01


def test_run_skill_drops_fabricated_check(monkeypatch):
    bad_output = {
        "budget_discussed": {"value": True, "evidence": [{"quote": "we need a million dollars", "line": 2}]},
        "discovery_quality": {"score": 3, "justification": "j", "evidence": [{"quote": "budget for this", "line": 2}]},
        "summary": [],
    }
    monkeypatch.setattr(llm_mod, "complete_json", fake_llm({"skill": bad_output}))

    cleaned, dropped, cost = run_skill(SKILL, LINES)

    assert cleaned["budget_discussed"] == {"value": None, "evidence": []}
    assert any(d["where"] == "budget_discussed" for d in dropped)


def test_run_skill_with_no_fields_returns_raw_output_ungated(monkeypatch):
    narrative_skill = {
        "name": "plain-summary",
        "description": "d",
        "when_to_use": "w",
        "body_md": "Summarize this call in plain prose.",
        "fields": None,
    }
    monkeypatch.setattr(llm_mod, "complete_json", fake_llm({"skill": {"summary_text": "A short prose summary."}}))

    cleaned, dropped, cost = run_skill(narrative_skill, LINES)

    assert cleaned == {"summary_text": "A short prose summary."}
    assert dropped == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_skill_executor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.skills.executor'`.

- [ ] **Step 4: Implement the executor**

Create `backend/app/skills/executor.py`:

```python
"""Run one skill against a transcript: build a JSON schema from its fields:
declaration, drive one LLM call through the provider-agnostic gateway, gate
the result through the generalized evidence gate. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §2.
"""

from .. import llm
from ..evidence import validate_fields

SYSTEM = (
    "You analyze business call transcripts. You only state what the transcript "
    "supports. Every claim must cite verbatim quotes with their line numbers. "
    "If something was not said on the call, it does not appear in your output."
)

_EVIDENCE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"quote": {"type": "string"}, "line": {"type": "integer"}},
        "required": ["quote", "line"],
    },
}


def _transcript_text(lines: list[dict]) -> str:
    return "\n".join(f"[{l['line']}] {l['speaker']}: {l['text']}" for l in lines)


def _build_schema(fields_spec: dict) -> dict:
    properties: dict = {}
    required: list[str] = []

    for name in fields_spec.get("checks", []):
        properties[name] = {
            "type": "object",
            "properties": {"value": {"type": ["boolean", "null"]}, "evidence": _EVIDENCE_SCHEMA},
            "required": ["value", "evidence"],
        }
        required.append(name)

    for spec in fields_spec.get("scores", []):
        properties[spec["name"]] = {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "minimum": 1, "maximum": spec["max"]},
                "justification": {"type": "string"},
                "evidence": _EVIDENCE_SCHEMA,
            },
            "required": ["score", "justification", "evidence"],
        }
        required.append(spec["name"])

    for name in fields_spec.get("claims", []):
        properties[name] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "evidence": _EVIDENCE_SCHEMA},
                "required": ["text", "evidence"],
            },
        }
        required.append(name)

    return {"type": "object", "properties": properties, "required": required}


def run_skill(skill: dict, transcript_lines: list[dict]) -> tuple[dict, list[dict], float]:
    """Returns (cleaned_output, dropped, cost). `skill` has keys name,
    description, when_to_use, body_md, fields (fields may be None for a
    narrative-only skill, in which case output passes through ungated)."""
    fields_spec = skill.get("fields") or {}
    schema = _build_schema(fields_spec) if fields_spec else {"type": "object", "properties": {}}

    user = f"Run this skill: {skill['name']}\n\n{skill['body_md']}\n\n{_transcript_text(transcript_lines)}"
    raw, cost = llm.complete_json(SYSTEM, user, schema, max_tokens=3000)

    if not fields_spec:
        return raw, [], cost

    cleaned, dropped = validate_fields(fields_spec, raw, transcript_lines)
    return cleaned, dropped, cost
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_skill_executor.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/skills/executor.py backend/tests/test_skill_executor.py backend/tests/fakes.py
git commit -m "feat: add skill executor — schema-from-fields, LLM call, evidence gate"
```

---

### Task 6: Orchestrator dispatch

**Files:**
- Create: `backend/app/orchestrator.py`
- Test: `backend/tests/test_orchestrator.py` (create)
- Modify: `backend/tests/fakes.py` (extend `fake_llm` with an orchestrator marker)

**Interfaces:**
- Consumes: `llm.complete_json` (existing).
- Produces: `dispatch(transcript_lines: list[dict], agents: list[dict], system_prompt: str) -> tuple[list[str], str, float]` — `agents` is `[{"id", "name", "description"}, ...]`; returns `(selected_agent_ids, reasoning, cost)`. An empty `selected_agent_ids` list is a valid, non-error result (spec's zero-agents edge case).

- [ ] **Step 1: Add the fake-LLM marker for orchestrator dispatch**

In `backend/tests/fakes.py`, add:

```python
GOOD_DISPATCH: dict = {"agent_ids": [], "reasoning": "No agents matched this call."}
```

And add a branch to `fake_llm`'s `fake()` function, alongside the `"Run this skill:"` branch:

```python
        if "Decide which of these agents" in user:
            r = responses.get("dispatch", GOOD_DISPATCH)
            if isinstance(r, Exception):
                raise r
            return r, 0.002
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_orchestrator.py`:

```python
"""Orchestrator dispatch: call -> which agents (Task 6 of the agent-skill
architecture plan). See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §1, §4.
"""

import app.llm as llm_mod
from app.orchestrator import dispatch
from fakes import fake_llm

LINES = [
    {"line": 1, "speaker": "Ana", "text": "Thanks for calling in about your renewal price."},
    {"line": 2, "speaker": "Bob", "text": "Yeah, it went up forty percent and I'm not happy."},
]

AGENTS = [
    {"id": "agent-1", "name": "Call Summarizer", "description": "Summarizes and scores every call"},
    {"id": "agent-2", "name": "QA Coach", "description": "Assesses rep troubleshooting skill on support calls"},
]

SYSTEM_PROMPT = "Decide which agents this call needs. Always include the Call Summarizer."


def test_dispatch_selects_agents_and_returns_reasoning(monkeypatch):
    monkeypatch.setattr(
        llm_mod,
        "complete_json",
        fake_llm({"dispatch": {"agent_ids": ["agent-1"], "reasoning": "This is a support call; only the summarizer is needed."}}),
    )
    selected, reasoning, cost = dispatch(LINES, AGENTS, SYSTEM_PROMPT)
    assert selected == ["agent-1"]
    assert "support call" in reasoning
    assert cost == 0.002


def test_dispatch_can_select_zero_agents(monkeypatch):
    monkeypatch.setattr(
        llm_mod,
        "complete_json",
        fake_llm({"dispatch": {"agent_ids": [], "reasoning": "Transcript too short to classify meaningfully."}}),
    )
    selected, reasoning, cost = dispatch(LINES, AGENTS, SYSTEM_PROMPT)
    assert selected == []
    assert "too short" in reasoning


def test_dispatch_can_select_multiple_agents(monkeypatch):
    monkeypatch.setattr(
        llm_mod,
        "complete_json",
        fake_llm({"dispatch": {"agent_ids": ["agent-1", "agent-2"], "reasoning": "Both summarization and QA coaching apply."}}),
    )
    selected, reasoning, cost = dispatch(LINES, AGENTS, SYSTEM_PROMPT)
    assert selected == ["agent-1", "agent-2"]


def test_dispatch_with_no_agents_configured_returns_empty(monkeypatch):
    monkeypatch.setattr(
        llm_mod,
        "complete_json",
        fake_llm({"dispatch": {"agent_ids": [], "reasoning": "No agents are configured."}}),
    )
    selected, reasoning, cost = dispatch(LINES, [], SYSTEM_PROMPT)
    assert selected == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_orchestrator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.orchestrator'`.

- [ ] **Step 4: Implement dispatch**

Create `backend/app/orchestrator.py`:

```python
"""Orchestrator dispatch: given a transcript and the roster of enabled
agents, decide which agents this call needs. One LLM call, steered by the
orchestrator's own editable system prompt. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §1, §4.

An empty result is valid, not an error — the "orchestrator selects no
agents" edge case is a deliberate, storable outcome, not a failure.
"""

from . import llm

_SCHEMA_TEMPLATE = {
    "type": "object",
    "properties": {
        "agent_ids": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": ["agent_ids", "reasoning"],
}


def _transcript_text(lines: list[dict]) -> str:
    return "\n".join(f"[{l['line']}] {l['speaker']}: {l['text']}" for l in lines)


def dispatch(
    transcript_lines: list[dict], agents: list[dict], system_prompt: str
) -> tuple[list[str], str, float]:
    """Returns (selected_agent_ids, reasoning, cost). `agents` is
    [{"id","name","description"}, ...] — only enabled agents should be
    passed in by the caller."""
    if not agents:
        return [], "No agents are configured.", 0.0

    roster = "\n".join(f"- {a['id']}: {a['name']} — {a['description']}" for a in agents)
    valid_ids = {a["id"] for a in agents}
    schema = dict(_SCHEMA_TEMPLATE)
    schema["properties"] = dict(_SCHEMA_TEMPLATE["properties"])
    schema["properties"]["agent_ids"] = {"type": "array", "items": {"type": "string", "enum": list(valid_ids)}}

    user = (
        f"{system_prompt}\n\n"
        f"Decide which of these agents should run on this call. Return only "
        f"the ids of agents that should run — an empty list is valid if none "
        f"apply. Briefly explain your reasoning.\n\nAgents:\n{roster}\n\n"
        f"Transcript:\n{_transcript_text(transcript_lines)}"
    )
    result, cost = llm.complete_json("You route calls to the right agents.", user, schema, max_tokens=500)
    selected = [a_id for a_id in result.get("agent_ids", []) if a_id in valid_ids]
    return selected, result.get("reasoning", ""), cost
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_orchestrator.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/orchestrator.py backend/tests/test_orchestrator.py backend/tests/fakes.py
git commit -m "feat: add orchestrator dispatch"
```

---

### Task 7: Agent skill-router

**Files:**
- Create: `backend/app/skill_router.py`
- Test: `backend/tests/test_skill_router.py` (create)
- Modify: `backend/tests/fakes.py` (extend `fake_llm` with a skill-router marker)

**Interfaces:**
- Consumes: `llm.complete_json` (existing).
- Produces: `route_skills(transcript_lines: list[dict], agent_system_prompt: str, skills: list[dict]) -> tuple[list[str], str, float]` — `skills` is `[{"id", "name", "description", "when_to_use"}, ...]`; returns `(selected_skill_ids, reasoning, cost)`.

Structurally identical to Task 6's `dispatch` (same "list of candidates in, subset of ids + reasoning out" shape) but a distinct function and file: the spec treats these as two separately steerable routing levels (a different system prompt author — the agent owner, not the orchestrator owner — and a different candidate set), and keeping them as separate small modules keeps each task's diff independently reviewable.

- [ ] **Step 1: Add the fake-LLM marker for skill routing**

In `backend/tests/fakes.py`, add:

```python
GOOD_SKILL_ROUTE: dict = {"skill_ids": [], "reasoning": "No skills matched."}
```

And add a branch to `fake_llm`'s `fake()` function:

```python
        if "Decide which of these skills" in user:
            r = responses.get("skill_route", GOOD_SKILL_ROUTE)
            if isinstance(r, Exception):
                raise r
            return r, 0.002
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_skill_router.py`:

```python
"""Agent skill-router: agent + call -> which skills to run (Task 7 of the
agent-skill architecture plan). See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §1.
"""

import app.llm as llm_mod
from app.skill_router import route_skills
from fakes import fake_llm

LINES = [
    {"line": 1, "speaker": "Ana", "text": "So tell me about your budget for this project."},
    {"line": 2, "speaker": "Bob", "text": "We're looking at fifty thousand a year, roughly."},
]

SKILLS = [
    {"id": "skill-1", "name": "sales-scorecard", "description": "Scores sales discovery", "when_to_use": "The call is a sales conversation"},
    {"id": "skill-2", "name": "support-scorecard", "description": "Scores support resolution", "when_to_use": "The call is a support conversation"},
    {"id": "skill-3", "name": "compliance-check", "description": "Flags compliance risk", "when_to_use": "Always run this"},
]

AGENT_PROMPT = "Use the sales skill for sales calls, support skill for support calls. Always run compliance."


def test_route_selects_matching_skills(monkeypatch):
    monkeypatch.setattr(
        llm_mod,
        "complete_json",
        fake_llm({"skill_route": {"skill_ids": ["skill-1", "skill-3"], "reasoning": "Sales call — sales scorecard plus mandatory compliance."}}),
    )
    selected, reasoning, cost = route_skills(LINES, AGENT_PROMPT, SKILLS)
    assert selected == ["skill-1", "skill-3"]
    assert "Sales call" in reasoning


def test_route_can_select_zero_skills(monkeypatch):
    monkeypatch.setattr(
        llm_mod,
        "complete_json",
        fake_llm({"skill_route": {"skill_ids": [], "reasoning": "No skill applies to this fragment."}}),
    )
    selected, reasoning, cost = route_skills(LINES, AGENT_PROMPT, SKILLS)
    assert selected == []


def test_route_with_no_skills_attached_returns_empty(monkeypatch):
    selected, reasoning, cost = route_skills(LINES, AGENT_PROMPT, [])
    assert selected == []
    assert cost == 0.0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_skill_router.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.skill_router'`.

- [ ] **Step 4: Implement the router**

Create `backend/app/skill_router.py`:

```python
"""Agent skill-router: given a transcript and one agent's attached skills,
decide which skills should run for this call. One LLM call per dispatched
agent, steered by that agent's own editable system prompt. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §1.
"""

from . import llm

_SCHEMA_TEMPLATE = {
    "type": "object",
    "properties": {
        "skill_ids": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": ["skill_ids", "reasoning"],
}


def _transcript_text(lines: list[dict]) -> str:
    return "\n".join(f"[{l['line']}] {l['speaker']}: {l['text']}" for l in lines)


def route_skills(
    transcript_lines: list[dict], agent_system_prompt: str, skills: list[dict]
) -> tuple[list[str], str, float]:
    """Returns (selected_skill_ids, reasoning, cost). `skills` is
    [{"id","name","description","when_to_use"}, ...] — the agent's attached
    skills only."""
    if not skills:
        return [], "No skills are attached to this agent.", 0.0

    catalog = "\n".join(f"- {s['id']}: {s['name']} — {s['description']} (use when: {s['when_to_use']})" for s in skills)
    valid_ids = {s["id"] for s in skills}
    schema = dict(_SCHEMA_TEMPLATE)
    schema["properties"] = dict(_SCHEMA_TEMPLATE["properties"])
    schema["properties"]["skill_ids"] = {"type": "array", "items": {"type": "string", "enum": list(valid_ids)}}

    user = (
        f"{agent_system_prompt}\n\n"
        f"Decide which of these skills should run on this call. Return only "
        f"the ids of skills that should run — an empty list is valid if none "
        f"apply. Briefly explain your reasoning.\n\nSkills:\n{catalog}\n\n"
        f"Transcript:\n{_transcript_text(transcript_lines)}"
    )
    result, cost = llm.complete_json("You choose which skills apply to a call.", user, schema, max_tokens=500)
    selected = [s_id for s_id in result.get("skill_ids", []) if s_id in valid_ids]
    return selected, result.get("reasoning", ""), cost
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_skill_router.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/skill_router.py backend/tests/test_skill_router.py backend/tests/fakes.py
git commit -m "feat: add agent skill-router"
```

---

### Task 8: Entry rules

**Files:**
- Create: `backend/app/entry_rules.py`
- Test: `backend/tests/test_entry_rules.py` (create)

**Interfaces:**
- Consumes: `EntryRule`, `Call` (Task 1, existing) via a SQLAlchemy `Session`.
- Produces: `resolve_entry_rule(session, call) -> str | None` — returns the pinned `agent_id`, or `None` if no rule matches (the common case, since this phase ships no UI to create rules).

Deliberately no LLM call — a pure DB lookup. **Scoped to `match_kind == "source"` only in this phase.** `EntryRule.match_kind` (Task 1) already allows an arbitrary string including `"phone_line"`, and the spec's data model names it as a future case — but this branch's `Call` model has no `caller_phone` column at all (phone-number ingestion is HubSpot-branch/P2 work, not merged here per Global Constraints). Matching against a column that doesn't exist would be untestable and would crash on `Call(caller_phone=...)`. Implement only the `source` branch; a later phase adds `phone_line` once `Call` actually carries a phone number.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_entry_rules.py`:

```python
"""Entry rule resolution: bypass the orchestrator when a call's source is
pinned to a specific agent (Task 8 of the agent-skill architecture plan).
phone_line matching is deferred — see the note in the task text for why.
See docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md
§4, Non-goals.
"""

from app.db import get_session
from app.entry_rules import resolve_entry_rule
from app.models import Agent, Call, EntryRule


def _seed_agent() -> str:
    with get_session() as session:
        agent = Agent(name="Support Triage", description="d", system_prompt="p")
        session.add(agent)
        session.commit()
        return agent.id


def test_matches_call_by_source():
    agent_id = _seed_agent()
    with get_session() as session:
        session.add(EntryRule(match_kind="source", match_value="sample", agent_id=agent_id))
        call = Call(title="t", source="sample", external_id="e1")
        session.add(call)
        session.commit()
        call_id = call.id

    with get_session() as session:
        call = session.get(Call, call_id)
        assert resolve_entry_rule(session, call) == agent_id


def test_no_match_returns_none():
    agent_id = _seed_agent()
    with get_session() as session:
        session.add(EntryRule(match_kind="source", match_value="sample", agent_id=agent_id))
        call = Call(title="t", source="upload", external_id="e2")
        session.add(call)
        session.commit()
        call_id = call.id

    with get_session() as session:
        call = session.get(Call, call_id)
        assert resolve_entry_rule(session, call) is None


def test_no_rules_configured_returns_none():
    with get_session() as session:
        call = Call(title="t", source="upload", external_id="e3")
        session.add(call)
        session.commit()
        call_id = call.id

    with get_session() as session:
        call = session.get(Call, call_id)
        assert resolve_entry_rule(session, call) is None


def test_ignores_a_phone_line_rule_it_cannot_evaluate_yet():
    """A phone_line rule may exist in the table (the column allows it), but
    this phase's Call has no caller_phone to match against — it must be
    silently ignored, not crash or false-match."""
    agent_id = _seed_agent()
    with get_session() as session:
        session.add(EntryRule(match_kind="phone_line", match_value="+15550001111", agent_id=agent_id))
        call = Call(title="t", source="upload", external_id="e4")
        session.add(call)
        session.commit()
        call_id = call.id

    with get_session() as session:
        call = session.get(Call, call_id)
        assert resolve_entry_rule(session, call) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_entry_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.entry_rules'`.

- [ ] **Step 3: Implement resolution**

Create `backend/app/entry_rules.py`:

```python
"""Resolve a pinned agent from EntryRule, bypassing the orchestrator call
entirely when a call's source is already known to always need one specific
agent. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §4, Non-goals.

Only match_kind == "source" is implemented in this phase — Call has no
caller_phone column yet (that lands with the HubSpot branch in P2), so a
phone_line rule cannot be evaluated and is silently ignored, not attempted.
No UI to create EntryRule rows exists either — this module only implements
the lookup so the executor's bypass path is ready when that UI lands later.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Call, EntryRule


def resolve_entry_rule(session: Session, call: Call) -> str | None:
    """Returns the pinned agent_id, or None if no EntryRule matches this call."""
    rule = session.scalars(
        select(EntryRule).where(EntryRule.match_kind == "source", EntryRule.match_value == call.source)
    ).first()
    return rule.agent_id if rule else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_entry_rules.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/entry_rules.py backend/tests/test_entry_rules.py
git commit -m "feat: add entry rule resolution"
```

---

### Task 9: Integration — agent executor wiring, API cutover, built-in seeding

**This is the highest-risk task in this plan** — it is the only task that touches existing, load-bearing files (`pipeline.py`, `models.py`'s `Run`, `main.py`, `api/review.py`, `api/share.py`, `render.py`, `api/ingest.py`, `scripts/seed.py`) and deletes now-superseded code. If the assigned implementer finds this too large to hold in context at once, it is acceptable to split it into two dispatches (executor + seeding first, then API/model cutover) — but both halves must land together before the suite is green again, since the schema change and the code that depends on it can't ship separately.

**Note on scope vs. earlier drafts of this task:** this plan was written by reading files in the `worktree-hubspot-crm-sync` branch (which has HubSpot CRM sync merged — `Call.caller_phone`/`caller_email`, `CrmSync`, `crm_sync.py`, `adapters/crm/*`, an 8-stage `STAGES` list including `crm_sync`). This implementation actually forks from `main`, which has **none of that** — 7-stage `STAGES`, no CRM anything at all, per the Global Constraints' explicit decision to leave the HubSpot branch isolated until P2. Every step below has been corrected to match `main`'s actual current state: there is no `sync_call_to_crm`, no `CrmSync`, no `crm_sync.py`, no CRM adapter, and no CRM-related test files (`test_crm_sync_pipeline.py`/`test_crm_integration.py`/`test_crm_adapter.py`/`test_crm_content.py`/`test_models_crm.py` do not exist on this branch — do not look for them). Do not add any of that code in this task; it lands in P2 when the HubSpot branch merges.

**Files:**
- Modify: `backend/app/models.py` (remove `Run.pack_id`, `Run.insights`, `Run.compliance`, `Run.edited_insights`; add `Run.orchestrator_reasoning`)
- Modify: `backend/app/pipeline.py` (rewrite `run_insights`; delete `select_pack`; keep `process_call`/`poll_transcription`/`_fail_transcribe` unchanged)
- Modify: `backend/app/run_state.py` (delete `STAGES`, `CRITICAL_STAGES`, `RunState`, `StageResult` — superseded by `agent_runtime.py`; keep `BudgetExceeded`, `StageFailed`, `MAX_ATTEMPTS`, `max_cost_per_run`)
- Modify: `backend/app/insights.py` (delete `detect_intent`, `extract`, `score`, `compose_email` — superseded by seeded skills executed generically; keep `prettify_transcript`, `_looks_clean`, `_transcript_text`)
- Modify: `backend/app/api/ingest.py` (`_fresh_stages()` no longer iterates `STAGES`)
- Modify: `backend/app/main.py` (`GET /api/calls`, `GET /api/calls/{id}` read from `AgentRun`)
- Modify: `backend/app/api/review.py` (edit/reset/retry operate on an `AgentRun`, not `Run`)
- Modify: `backend/app/render.py` (rewrite `to_markdown`/`export_json`/`share_snapshot` to render a list of per-agent, per-skill outputs generically — the old functions assumed fixed field names like `summary`/`scorecard`/`intent` that no longer exist)
- Modify: `backend/app/api/share.py` (`_call_and_run` checks for `AgentRun`s instead of `run.insights`; call sites pass the new agent-output list)
- Modify: `backend/scripts/seed.py` (seed built-in skills + Call Summarizer agent + Orchestrator; re-run the five sample calls through the real agent path)
- Test: `backend/tests/test_agent_executor.py` (create)
- Test: `backend/tests/test_api_agents_cutover.py` (create)
- Modify (delete now-invalid assertions, keep the rest): `backend/tests/test_insights.py`, `backend/tests/test_run_state.py`, `backend/tests/test_share.py`

**Interfaces:**
- Consumes: `new_agent_run_state` (Task 4), `run_skill` (Task 5), `dispatch` (Task 6), `route_skills` (Task 7), `resolve_entry_rule` (Task 8), all six new tables (Task 1).
- Produces: `run_insights(payload: dict) -> None` (same job-handler signature, entirely new body); `get_call(call_id) -> dict` now returns `"agent_runs": [{"id", "agent_id", "agent_name", "status", "output", "edited", "steps", "cost_usd"}, ...]` instead of `"insights"`/`"compliance"` — `id` is the `AgentRun`'s own primary key, required by Task 11's edit/reset UI to build `PATCH /api/calls/{id}/agent-runs/{agent_run_id}`; `agent_id` is a separate field, not interchangeable with it.

- [ ] **Step 1: Write the failing executor test first**

Create `backend/tests/test_agent_executor.py` — drives the whole new path with a fake orchestrator/router/skill-exec (no real LLM), proving isolation (one agent fails, sibling ships) and the zero-agents edge case:

```python
"""Agent executor integration (Task 9 of the agent-skill architecture
plan): orchestrator dispatch -> per-agent AgentRun -> skill router -> skill
steps, with isolation and the zero-agents edge case. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §4, §5.
"""

import app.orchestrator as orchestrator_mod
import app.skill_router as skill_router_mod
import app.skills.executor as executor_mod
from app.db import get_session
from app.jobs import run_due_jobs
from app.models import Agent, AgentRun, Call, Orchestrator, Run, Skill, Transcript
from sqlalchemy import select

LINES = [
    {"line": 1, "speaker": "Ana", "text": "Thanks for calling about your renewal."},
    {"line": 2, "speaker": "Bob", "text": "Our budget is fifty thousand a year."},
]


def _seed(agent_names: list[str], skill_names: dict[str, list[str]]) -> tuple[str, dict[str, str], dict[str, str]]:
    """agent_names: list of agent names to create. skill_names: {agent_name: [skill_name, ...]}."""
    with get_session() as session:
        session.add(Orchestrator(system_prompt="Decide which agents this call needs."))
        agent_ids = {}
        skill_ids = {}
        for name in agent_names:
            agent = Agent(name=name, description=f"{name} agent", system_prompt=f"{name} prompt")
            session.add(agent)
            session.flush()
            agent_ids[name] = agent.id
            for skill_name in skill_names.get(name, []):
                skill = Skill(
                    name=skill_name, description="d", when_to_use="w",
                    body_md="Do the thing.", fields=None, source="ui",
                )
                session.add(skill)
                session.flush()
                skill_ids[skill_name] = skill.id
                from app.models import AgentSkill

                session.add(AgentSkill(agent_id=agent.id, skill_id=skill.id))

        call = Call(title="t", source="upload", external_id="exec-test-1", audio_path="/tmp/x.wav")
        session.add(call)
        session.flush()
        session.add(Transcript(call_id=call.id, lines=LINES))
        run = Run(call_id=call.id, status="running", stages=[{"name": "transcribe", "status": "ok", "attempts": 1, "cost_usd": 0.0, "error": None}])
        session.add(run)
        session.commit()
        return call.id, agent_ids, skill_ids


def test_dispatched_agent_ships_and_run_finalizes(monkeypatch):
    call_id, agent_ids, skill_ids = _seed(["Call Summarizer"], {"Call Summarizer": ["plain-summary"]})

    monkeypatch.setattr(
        orchestrator_mod, "dispatch",
        lambda lines, agents, prompt: ([agent_ids["Call Summarizer"]], "Only summarizer needed.", 0.002),
    )
    monkeypatch.setattr(
        skill_router_mod, "route_skills",
        lambda lines, prompt, skills: ([skill_ids["plain-summary"]], "Always run.", 0.002),
    )
    monkeypatch.setattr(
        executor_mod, "run_skill",
        lambda skill, lines: ({"summary_text": "A clean call."}, [], 0.01),
    )

    from app.pipeline import run_insights

    run_insights({"call_id": call_id})

    with get_session() as session:
        run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
        assert run.status == "shipped"
        assert "Only summarizer needed" in run.orchestrator_reasoning

        agent_run = session.scalars(select(AgentRun).where(AgentRun.call_id == call_id)).first()
        assert agent_run.status == "shipped"
        assert agent_run.output["plain-summary"]["summary_text"] == "A clean call."


def test_one_agent_failing_does_not_affect_sibling(monkeypatch):
    call_id, agent_ids, skill_ids = _seed(
        ["Call Summarizer", "QA Coach"],
        {"Call Summarizer": ["plain-summary"], "QA Coach": ["qa-rubric"]},
    )

    monkeypatch.setattr(
        orchestrator_mod, "dispatch",
        lambda lines, agents, prompt: ([agent_ids["Call Summarizer"], agent_ids["QA Coach"]], "Both needed.", 0.002),
    )

    def fake_route(lines, prompt, skills):
        names = [s["name"] for s in skills]
        return [s["id"] for s in skills], "route all", 0.001

    monkeypatch.setattr(skill_router_mod, "route_skills", fake_route)

    def fake_run_skill(skill, lines):
        if skill["name"] == "qa-rubric":
            raise ValueError("simulated LLM failure")
        return {"summary_text": "A clean call."}, [], 0.01

    monkeypatch.setattr(executor_mod, "run_skill", fake_run_skill)

    from app.pipeline import run_insights

    run_insights({"call_id": call_id})

    with get_session() as session:
        agent_runs = {ar.agent_id: ar for ar in session.scalars(select(AgentRun).where(AgentRun.call_id == call_id)).all()}
        summarizer_run = agent_runs[agent_ids["Call Summarizer"]]
        qa_run = agent_runs[agent_ids["QA Coach"]]
        assert summarizer_run.status == "shipped"
        assert qa_run.status == "failed"

        run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
        assert run.status == "partial"  # one agent shipped, one failed — never silently "shipped"


def test_zero_agents_selected_ships_with_reasoning_stored(monkeypatch):
    call_id, agent_ids, skill_ids = _seed(["Call Summarizer"], {"Call Summarizer": ["plain-summary"]})

    monkeypatch.setattr(
        orchestrator_mod, "dispatch",
        lambda lines, agents, prompt: ([], "Transcript too short to classify.", 0.001),
    )

    from app.pipeline import run_insights

    run_insights({"call_id": call_id})

    with get_session() as session:
        run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
        assert run.status == "shipped"
        assert "too short" in run.orchestrator_reasoning
        assert session.scalars(select(AgentRun).where(AgentRun.call_id == call_id)).first() is None


def test_aggregate_budget_exceeded_skips_remaining_agents_cleanly(monkeypatch):
    """Global Constraints: per-AgentRun cap AND the per-call aggregate cap
    both apply. Two agents each spend WITHIN their own per-agent budget
    (proving this isn't just the per-agent cap firing), but their combined
    spend crosses MAX_COST_PER_RUN before a third agent starts — that third
    agent's AgentRun must never be created. No zombie runs, clean finalize."""
    call_id, agent_ids, skill_ids = _seed(
        ["Call Summarizer", "QA Coach", "Knowledgebase"],
        {"Call Summarizer": ["plain-summary"], "QA Coach": ["qa-rubric"], "Knowledgebase": ["kb-lookup"]},
    )
    monkeypatch.setenv("MAX_COST_PER_RUN", "0.05")

    monkeypatch.setattr(
        orchestrator_mod, "dispatch",
        lambda lines, agents, prompt: (
            [agent_ids["Call Summarizer"], agent_ids["QA Coach"], agent_ids["Knowledgebase"]],
            "All three needed.", 0.0,
        ),
    )
    monkeypatch.setattr(
        skill_router_mod, "route_skills",
        lambda lines, prompt, skills: ([s["id"] for s in skills], "route all", 0.0),
    )
    monkeypatch.setattr(
        executor_mod, "run_skill",
        lambda skill, lines: ({"summary_text": "A call."}, [], 0.03),  # under the 0.05 per-agent cap alone
    )

    from app.pipeline import run_insights

    run_insights({"call_id": call_id})

    with get_session() as session:
        run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
        agent_runs = session.scalars(select(AgentRun).where(AgentRun.call_id == call_id)).all()
        assert len(agent_runs) == 2  # Call Summarizer + QA Coach ran and shipped; Knowledgebase never started
        assert all(ar.status == "shipped" for ar in agent_runs)  # each individually under its own cap
        assert run.status == "shipped"  # finalized cleanly, not stuck "running"
        assert "budget" in run.orchestrator_reasoning.lower()
        assert "Knowledgebase" in run.orchestrator_reasoning
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_agent_executor.py -v`
Expected: FAIL — `AttributeError: 'Run' object has no attribute 'orchestrator_reasoning'` (or similar, since `run_insights` still runs the old pipeline).

- [ ] **Step 3: Update `Run` — remove old insight columns, add orchestrator reasoning**

In `backend/app/models.py`, replace the `Run` class body:

```python
class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"))
    # pending | running | shipped | partial | failed — aggregate across this call's AgentRuns
    status: Mapped[str] = mapped_column(String, default="pending")
    # [{"name","status","attempts","cost_usd","error"}] — transcribe-stage bookkeeping only;
    # per-skill steps live on AgentRun.steps now
    stages: Mapped[list] = mapped_column(JSON, default=list)
    orchestrator_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    call: Mapped[Call] = relationship(back_populates="runs")
```

(This removes `pack_id`, `insights`, `edited_insights`, `compliance` — `pack_id` is dropped too since `InsightPack` selection is no longer part of the new path, per Global Constraints.)

- [ ] **Step 4: Write the agent executor and rewrite `pipeline.py::run_insights`**

In `backend/app/pipeline.py`, replace the imports at the top:

```python
from datetime import datetime, timezone

from sqlalchemy import select

from .adapters.pyai.base import get_adapter
from .agent_runtime import new_agent_run_state
from .db import get_session
from .entry_rules import resolve_entry_rule
from .insights import prettify_transcript
from .jobs import enqueue, handler
from .models import Agent, AgentRun, AgentSkill, Call, Orchestrator, Run, Skill
from .orchestrator import dispatch
from .run_state import BudgetExceeded, StageFailed, max_cost_per_run
from .skill_router import route_skills
from .skills.executor import run_skill
from .transcription import deliver_transcript, update_transcript_lines

POLL_INTERVAL_S = 5
MAX_POLLS = 120
```

Delete `select_pack` entirely (no longer used — packs are retired from the live path per Global Constraints).

Keep `_update_stage`, `process_call`, `poll_transcription`, and `_fail_transcribe` **exactly as they are today** — they operate above the insight chain and are untouched by this task. There is no `sync_call_to_crm` on this branch (see the note at the top of this task) — do not add one.

Replace `run_insights` and `_persist` with:

```python
def _agent_dict(agent: Agent) -> dict:
    return {"id": agent.id, "name": agent.name, "description": agent.description}


def _skill_dict(skill: Skill) -> dict:
    return {
        "id": skill.id, "name": skill.name, "description": skill.description,
        "when_to_use": skill.when_to_use, "body_md": skill.body_md, "fields": skill.fields,
    }


def _run_one_agent(agent: Agent, skills: list[Skill], call_id: str, run_id: str, lines: list[dict]) -> None:
    with get_session() as session:
        agent_run = AgentRun(call_id=call_id, run_id=run_id, agent_id=agent.id, status="running", steps=[])
        session.add(agent_run)
        session.commit()
        agent_run_id = agent_run.id

    skill_ids, router_reasoning, router_cost = route_skills(lines, agent.system_prompt, [_skill_dict(s) for s in skills])
    routed_skills = [s for s in skills if s.id in skill_ids]

    rs = new_agent_run_state([s.name for s in routed_skills])
    output: dict = {}
    total_cost = router_cost

    for skill in routed_skills:
        def run_one(sk=skill):
            cleaned, dropped, cost = run_skill(_skill_dict(sk), lines)
            rs.charge(sk.name, cost)
            rs._get(sk.name).dropped_claims = len(dropped)
            return cleaned

        try:
            output[skill.name] = rs.execute(skill.name, run_one)
        except BudgetExceeded:
            rs.skip_remaining(skill.name)
            break
        except StageFailed:
            continue

    total_cost += sum(s.cost_usd for s in rs.steps)

    with get_session() as session:
        agent_run = session.get(AgentRun, agent_run_id)
        agent_run.steps = rs.as_dicts()
        agent_run.status = rs.final_status()
        agent_run.output = output or None
        agent_run.cost_usd = round(total_cost, 4)
        agent_run.finished_at = datetime.now(timezone.utc)
        session.commit()


def _aggregate_status(statuses: list[str]) -> str:
    if not statuses:
        return "shipped"
    if all(s == "shipped" for s in statuses):
        return "shipped"
    if all(s == "failed" for s in statuses):
        return "failed"
    return "partial"


@handler("run_insights")
def run_insights(payload: dict) -> None:
    call_id = payload["call_id"]
    with get_session() as session:
        call = session.get(Call, call_id)
        if call is None or call.transcript is None:
            raise ValueError(f"call {call_id} has no transcript")
        run = session.scalars(select(Run).where(Run.call_id == call_id).order_by(Run.created_at.desc())).first()
        lines = call.transcript.lines
        run_id = run.id

        try:
            pretty, fmt_cost = prettify_transcript(lines)
            if pretty is not lines:
                update_transcript_lines(call_id, pretty)
                lines = pretty
        except Exception:
            fmt_cost = 0.0

        entry_agent_id = resolve_entry_rule(session, call)
        enabled_agents = session.scalars(select(Agent).where(Agent.enabled.is_(True))).all()

        if entry_agent_id:
            selected_ids, reasoning, dispatch_cost = [entry_agent_id], "Entry rule pinned this agent.", 0.0
        else:
            orch = session.scalars(select(Orchestrator).where(Orchestrator.enabled.is_(True))).first()
            orch_prompt = orch.system_prompt if orch else "Decide which agents this call needs."
            selected_ids, reasoning, dispatch_cost = dispatch(lines, [_agent_dict(a) for a in enabled_agents], orch_prompt)

        selected_agents = [a for a in enabled_agents if a.id in selected_ids]
        agent_skills: dict[str, list[Skill]] = {}
        for agent in selected_agents:
            links = session.scalars(select(AgentSkill).where(AgentSkill.agent_id == agent.id)).all()
            skill_ids = [l.skill_id for l in links]
            agent_skills[agent.id] = (
                session.scalars(select(Skill).where(Skill.id.in_(skill_ids))).all() if skill_ids else []
            )

        run.orchestrator_reasoning = reasoning
        run.cost_usd = round(fmt_cost + dispatch_cost, 4)
        session.commit()

        agents_to_run = [(a, agent_skills[a.id]) for a in selected_agents]
        spent_so_far = round(fmt_cost + dispatch_cost, 4)
        budget = max_cost_per_run()

    budget_note = None
    for agent, skills in agents_to_run:
        if spent_so_far > budget:
            budget_note = (
                f"run budget ${budget:.2f} exceeded before agent {agent.name!r} could run "
                f"(spent ${spent_so_far:.4f}) — remaining agents skipped"
            )
            break
        _run_one_agent(agent, skills, call_id, run_id, lines)
        with get_session() as session:
            spent_so_far = round(
                spent_so_far
                + (session.scalars(select(AgentRun).where(AgentRun.run_id == run_id)).all()[-1].cost_usd),
                4,
            )

    with get_session() as session:
        run = session.get(Run, run_id)
        agent_runs = session.scalars(select(AgentRun).where(AgentRun.run_id == run_id)).all()
        run.status = _aggregate_status([ar.status for ar in agent_runs])
        run.cost_usd = round(run.cost_usd + sum(ar.cost_usd for ar in agent_runs), 4)
        if budget_note:
            run.orchestrator_reasoning = f"{run.orchestrator_reasoning}\n\n{budget_note}"
        run.finished_at = datetime.now(timezone.utc)
        session.commit()
```

This is the end of `run_insights` — there is no CRM sync call. P1 has no side-effecting actions at all (spec Non-goals); CRM Automation is P2's job, once the HubSpot branch merges against this new `AgentRun` shape.

- [ ] **Step 5: Delete the superseded old-pipeline code**

In `backend/app/run_state.py`, delete `STAGES`, `CRITICAL_STAGES`, `StageResult`, and `RunState` (the whole dataclass and its methods). Keep `max_cost_per_run`, `BudgetExceeded`, `execute`'s sibling logic is gone with `RunState` — keep only `StageFailed` and `MAX_ATTEMPTS` as free-standing. The file should end up containing just:

```python
"""Shared retry/budget primitives, reused by agent_runtime.AgentRunState.
The fixed-stage RunState this module used to define is retired — see
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §3."""

import os

MAX_ATTEMPTS = 3


def max_cost_per_run() -> float:
    return float(os.environ.get("MAX_COST_PER_RUN", "1.00"))


class BudgetExceeded(Exception):
    pass


class StageFailed(Exception):
    def __init__(self, stage: str, reason: str):
        self.stage = stage
        self.reason = reason
        super().__init__(f"stage {stage!r} failed: {reason}")
```

In `backend/app/insights.py`, delete `detect_intent`, `extract`, `score`, `compose_email` and the now-unused `import json` and `from .packs import BUILTIN_PACKS` line. Keep `SYSTEM`, `_looks_clean`, `prettify_transcript`, `_transcript_text` — `prettify_transcript` is still called directly from `pipeline.py`.

- [ ] **Step 6: Fix `api/ingest.py`'s stage bookkeeping**

In `backend/app/api/ingest.py`, replace:

```python
from ..run_state import STAGES
```

with nothing (delete the import), and replace `_fresh_stages()`:

```python
def _fresh_stages() -> list[dict]:
    return [{"name": "transcribe", "status": "pending", "attempts": 0, "cost_usd": 0.0, "error": None}]
```

- [ ] **Step 7: Update the API surface to read from `AgentRun`**

In `backend/app/main.py`, replace `list_calls`'s intent lookup (delete the `intent` field entirely — it was sourced from the retired `detect_intent` stage and has no replacement in this phase; the frontend's Task 11 work stops rendering it):

```python
@app.get("/api/calls")
def list_calls() -> list[dict]:
    with get_session() as session:
        calls = session.scalars(select(Call).order_by(Call.created_at)).all()
        out = []
        for c in calls:
            run = _latest_run(session, c.id)
            out.append(
                {
                    "id": c.id,
                    "title": c.title,
                    "source": c.source,
                    "duration_s": c.duration_s,
                    "recorded_at": c.recorded_at.isoformat(),
                    "run_status": run.status if run else "pending",
                }
            )
        return out
```

Replace `get_call`:

```python
@app.get("/api/calls/{call_id}")
def get_call(call_id: str) -> dict:
    with get_session() as session:
        call = session.get(Call, call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="call not found")
        run = _latest_run(session, call_id)
        agent_runs_out = []
        if run:
            agent_runs = session.scalars(select(AgentRun).where(AgentRun.run_id == run.id)).all()
            for ar in agent_runs:
                agent = session.get(Agent, ar.agent_id)
                agent_runs_out.append(
                    {
                        "id": ar.id,
                        "agent_id": ar.agent_id,
                        "agent_name": agent.name if agent else "Unknown agent",
                        "status": ar.status,
                        "steps": ar.steps,
                        "output": ar.edited_output or ar.output,
                        "edited": bool(ar.edited_output),
                        "cost_usd": ar.cost_usd,
                    }
                )

        return {
            "call": {
                "id": call.id,
                "title": call.title,
                "source": call.source,
                "duration_s": call.duration_s,
                "recorded_at": call.recorded_at.isoformat(),
                "participants": _participants(call),
            },
            "run": {
                "status": run.status if run else "pending",
                "stages": run.stages if run else [],
                "orchestrator_reasoning": run.orchestrator_reasoning if run else None,
            },
            "transcript": (
                {"language": call.transcript.language, "lines": call.transcript.lines}
                if call.transcript
                else None
            ),
            "agent_runs": agent_runs_out,
        }
```

Replace the top-of-file `from .models import Call, Run` line with `from .models import Agent, AgentRun, Call, Run` (no `CrmSync` — this branch has no such model; do not add one).

In `backend/app/api/review.py`, replace the edit/reset endpoints to operate on a specific `AgentRun` (via `agent_run_id`) instead of the whole `Run`:

```python
class AgentRunEdit(BaseModel):
    output: dict


@router.patch("/{call_id}/agent-runs/{agent_run_id}")
def edit_agent_run(call_id: str, agent_run_id: str, body: AgentRunEdit) -> dict:
    with get_session() as session:
        agent_run = session.get(AgentRun, agent_run_id)
        if agent_run is None or agent_run.call_id != call_id:
            raise HTTPException(404, "no agent run for this call")
        if agent_run.output is None:
            raise HTTPException(409, "agent run has no output to edit yet")
        agent_run.edited_output = body.output
        session.commit()
        return {"ok": True, "edited": True}


@router.post("/{call_id}/agent-runs/{agent_run_id}/reset")
def reset_agent_run(call_id: str, agent_run_id: str) -> dict:
    with get_session() as session:
        agent_run = session.get(AgentRun, agent_run_id)
        if agent_run is None or agent_run.call_id != call_id:
            raise HTTPException(404, "no agent run for this call")
        agent_run.edited_output = None
        session.commit()
        return {"ok": True, "edited": False}
```

Add `from ..models import AgentRun` to the top of `review.py`. Delete the old `InsightEdit`/`edit_insights`/`reset_insights` — superseded by the two endpoints above. Keep `retry` as-is except for `Run.stages` bookkeeping, which now only ever contains the single `transcribe` entry, so the existing `if s["status"] in ("failed", "skipped")` loop still behaves correctly (it just has one element to consider instead of eight); no code change needed there.

**Rewrite `backend/app/render.py` and `backend/app/api/share.py` in full.** This is not a one-line swap: the old `to_markdown`/`export_json`/`share_snapshot` assumed a fixed insights shape (`summary`, `objections`, `next_steps`, `scorecard`, `follow_up_email`, `intent` as flat top-level keys). That shape no longer exists — a seeded skill's output is a dict of `{field_name: value}` nested under that skill's name (e.g. `AgentRun.output["sales-scorecard"]["discovery_quality"]`), and a call can have multiple `AgentRun`s (multiple agents). The renderers need a generic per-field formatter instead of hardcoded field names.

The old design also had a real, load-bearing guarantee: compliance findings never leave the building — they were structurally excluded from `export_json`/`share_snapshot`/`to_markdown` because `run.compliance` was a separate column those functions never read. Under the new design, `compliance-check` output lives inside `AgentRun.output` like every other skill, so that exclusion must now be enforced by name. Preserve it — this is not optional cleanup, it's carrying forward an intentional trust boundary from the spec's Phase-2 CRM design note ("compliance is internal, doesn't leave the building").

Replace `backend/app/render.py` in full:

```python
"""Rendering: effective agent outputs, Markdown export, and the shareable
snapshot.

Shared by the export endpoints and the share-link snapshot so a call renders
identically whether downloaded or shared. compliance-check's output is
deliberately excluded everywhere here — the old design enforced "compliance
never leaves the building" structurally (a separate Run.compliance column
these functions never read); now that compliance-check is an ordinary seeded
skill living inside AgentRun.output, the same exclusion is enforced by name.
"""

from typing import Any

from sqlalchemy import select

from .models import Agent, AgentRun

EXCLUDED_SKILLS = {"compliance-check"}


def effective_agent_outputs(session, run) -> list[dict]:
    """[{"agent_name", "output", "edited"}, ...] for every AgentRun on this
    run. Human edits win over the original AI output per AgentRun — same
    precedent as the old edited_insights-over-insights rule. compliance-check
    is stripped from `output` unconditionally."""
    out = []
    for ar in session.scalars(select(AgentRun).where(AgentRun.run_id == run.id)).all():
        agent = session.get(Agent, ar.agent_id)
        raw = ar.edited_output or ar.output or {}
        filtered = {k: v for k, v in raw.items() if k not in EXCLUDED_SKILLS}
        out.append({
            "agent_name": agent.name if agent else "Unknown agent",
            "output": filtered,
            "edited": bool(ar.edited_output),
        })
    return out


def _cite(evidence: list) -> str:
    if not evidence:
        return ""
    return " " + " ".join(f"[L{e['line']}]" for e in evidence)


def _render_field(name: str, value) -> str:
    """Formats one skill field generically by shape, not by name — a
    score ({"score","justification","evidence"}), a check
    ({"value","evidence"}), or a claims list ([{"text","evidence"}, ...])."""
    label = name.replace("_", " ")
    if isinstance(value, dict) and "score" in value:
        return f"- {label}: **{value.get('score')}** — {value.get('justification', '')}{_cite(value.get('evidence', []))}"
    if isinstance(value, dict) and "value" in value:
        val = "yes" if value.get("value") else "no" if value.get("value") is False else "—"
        return f"- {label}: **{val}**{_cite(value.get('evidence', []))}"
    if isinstance(value, list):
        if not value:
            return f"- {label}: none"
        return "\n".join(f"- {item.get('text', '')}{_cite(item.get('evidence', []))}" for item in value)
    return f"- {label}: {value}"


def to_markdown(call, run, agent_outputs: list[dict], *, include_transcript: bool = False, transcript=None) -> str:
    lines: list[str] = [f"# {call.title}", "", f"_Status: {run.status}_", ""]

    for ao in agent_outputs:
        if not ao["output"]:
            continue
        lines.append(f"## {ao['agent_name']}" + (" · _edited_" if ao["edited"] else ""))
        for skill_name, fields in ao["output"].items():
            lines.append(f"### {skill_name.replace('-', ' ').title()}")
            for field_name, value in (fields or {}).items():
                lines.append(_render_field(field_name, value))
            lines.append("")

    if include_transcript and transcript:
        lines.append("## Transcript")
        for l in transcript.lines:
            lines.append(f"{l['line']}. **{l['speaker']}:** {l['text']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def export_json(call, run, agent_outputs: list[dict]) -> dict[str, Any]:
    """Full structured export — includes evidence, excludes compliance-check."""
    return {
        "call": {
            "id": call.id,
            "title": call.title,
            "duration_s": call.duration_s,
            "recorded_at": call.recorded_at.isoformat(),
        },
        "run": {"status": run.status, "edited": any(ao["edited"] for ao in agent_outputs)},
        "agent_runs": agent_outputs,
    }


def share_snapshot(call, run, agent_outputs: list[dict]) -> dict[str, Any]:
    """Frozen at share time. No raw transcript, no compliance-check."""
    return {
        "title": call.title,
        "recorded_at": call.recorded_at.isoformat(),
        "duration_s": call.duration_s,
        "agent_runs": agent_outputs,
    }
```

Replace `backend/app/api/share.py` in full — `get_share`/`revoke_share` are unchanged from today; `_call_and_run`, `export_markdown`, `export_call_json`, `create_share` change:

```python
"""Exports (Markdown / JSON) and share links.

Exports render the effective agent outputs (human edits if present, else AI
output). Share links freeze a snapshot at creation time — later edits don't
change an already-shared page — and are revocable. Shared snapshots exclude
the raw transcript and compliance-check output.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from ..db import get_session
from ..models import AgentRun, Call, Run, ShareLink
from ..render import effective_agent_outputs, export_json, share_snapshot, to_markdown

router = APIRouter(tags=["share"])


def _call_and_run(session, call_id: str) -> tuple[Call, Run]:
    call = session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, "call not found")
    run = session.scalars(
        select(Run).where(Run.call_id == call_id).order_by(Run.created_at.desc())
    ).first()
    has_output = run is not None and session.scalars(
        select(AgentRun).where(AgentRun.run_id == run.id)
    ).first() is not None
    if not has_output:
        raise HTTPException(409, "call has no agent output to export yet")
    return call, run


@router.get("/api/calls/{call_id}/export.md", response_class=PlainTextResponse)
def export_markdown(call_id: str, transcript: bool = False) -> str:
    with get_session() as session:
        call, run = _call_and_run(session, call_id)
        outputs = effective_agent_outputs(session, run)
        return to_markdown(call, run, outputs, include_transcript=transcript, transcript=call.transcript)


@router.get("/api/calls/{call_id}/export.json")
def export_call_json(call_id: str) -> dict:
    with get_session() as session:
        call, run = _call_and_run(session, call_id)
        return export_json(call, run, effective_agent_outputs(session, run))


@router.post("/api/calls/{call_id}/share")
def create_share(call_id: str) -> dict:
    with get_session() as session:
        call, run = _call_and_run(session, call_id)
        snap = share_snapshot(call, run, effective_agent_outputs(session, run))
        link = ShareLink(run_id=run.id, content_snapshot=snap)
        session.add(link)
        session.commit()
        return {"token": link.token, "url": f"/share/{link.token}"}


@router.get("/api/share/{token}")
def get_share(token: str) -> dict:
    with get_session() as session:
        link = session.get(ShareLink, token)
        if link is None or link.revoked:
            raise HTTPException(404, "share link not found or revoked")
        return {"snapshot": link.content_snapshot, "created_at": link.created_at.isoformat()}


@router.post("/api/share/{token}/revoke")
def revoke_share(token: str) -> dict:
    with get_session() as session:
        link = session.get(ShareLink, token)
        if link is None:
            raise HTTPException(404, "share link not found")
        link.revoked = True
        session.commit()
        return {"ok": True, "revoked": True}
```

- [ ] **Step 8: Seed built-in skills, the Call Summarizer agent, and the orchestrator**

Rewrite `backend/scripts/seed.py`. The five built-in skills' `body_md` content below is adapted directly from the retired `insights.py`/`compliance.py` prompts so behavior stays equivalent — this is the parity proof the spec's Goals section requires:

```python
"""Seed the database: built-in skills, the Call Summarizer agent, the
orchestrator, and the five sample calls (re-processed through the real
agent path — see
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md
Known risks #1 for why this requires live API keys).

Idempotent: re-running updates in place (keyed on Call.external_id / Skill
name / Agent name).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db import Base, engine, get_session
from app.jobs import enqueue, run_due_jobs
from app.models import Agent, AgentSkill, Call, Orchestrator, Run, Skill, Transcript

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "samples"

BUILTIN_SKILLS = [
    {
        "name": "summary-and-next-steps",
        "description": "Summarizes the call and extracts committed next steps",
        "when_to_use": "Always — every call needs a summary",
        "fields": {"claims": ["summary", "next_steps"]},
        "body_md": (
            "Extract a factual summary (3-5 bullets covering what happened on the "
            "call) and the concrete next steps committed to, with an owner for "
            "each. Every claim must cite a verbatim quote with its line number. "
            "Never invent or embellish."
        ),
    },
    {
        "name": "sales-scorecard",
        "description": "Scores discovery quality and MEDDIC coverage on sales calls",
        "when_to_use": "The call is a sales conversation — discovery, demo, pricing, or negotiation",
        "fields": {
            "checks": [
                "recording_disclosure", "budget_discussed", "decision_process_identified",
                "timeline_identified", "next_step_secured",
            ],
            "scores": [
                {"name": "discovery_quality", "max": 5},
                {"name": "objection_handling", "max": 5},
            ],
        },
        "body_md": (
            "Score this call against a sales rubric. Checks (true only with "
            "supporting evidence, false/null otherwise): was the call recording "
            "disclosed; was budget discussed; were other decision makers "
            "identified; was a timeline established; was a concrete next step "
            "secured. Scores (1-5, with justification and evidence): "
            "discovery_quality — how well did the rep uncover pain, urgency, "
            "buying process, and budget through open questions rather than "
            "pitching; objection_handling — how directly and credibly were "
            "objections answered."
        ),
    },
    {
        "name": "support-scorecard",
        "description": "Scores issue resolution and empathy on support calls",
        "when_to_use": "The call is a support conversation — existing customer issues, billing, cancellations",
        "fields": {
            "checks": [
                "recording_disclosure", "issue_identified", "resolution_provided",
                "timeline_communicated", "churn_risk_flagged",
            ],
            "scores": [
                {"name": "empathy_and_tone", "max": 5},
                {"name": "resolution_quality", "max": 5},
            ],
        },
        "body_md": (
            "Score this call against a support rubric. Checks (true only with "
            "supporting evidence, false/null otherwise): was the call recording "
            "disclosed; was the customer's issue clearly identified; was a "
            "resolution provided or concretely promised; was a resolution "
            "timeline communicated; did the customer signal cancellation or "
            "churn risk. Scores (1-5, with justification and evidence): "
            "empathy_and_tone — did the agent acknowledge frustration and stay "
            "helpful under pressure; resolution_quality — was the root cause "
            "found and fully addressed with clear next-step communication."
        ),
    },
    {
        "name": "compliance-check",
        "description": "Flags compliance risk: recording disclosure, unsubstantiated claims, pressure tactics, PII exposure",
        "when_to_use": "Always — every call should be checked for compliance risk",
        "fields": {
            "checks": ["recording_disclosure_given"],
            "claims": ["compliance_findings"],
        },
        "body_md": (
            "Check this call for compliance risk. recording_disclosure_given: "
            "true only if someone explicitly disclosed the call is recorded, "
            "with the disclosing quote as evidence; false/null if not (you "
            "cannot cite evidence for an absence, so leave evidence empty in "
            "that case). compliance_findings: list any of the following, each "
            "with a verbatim quote and line number as evidence — an "
            "unsubstantiated guaranteed-outcome claim; high-pressure or "
            "artificial-urgency tactics; sensitive personal data (SSN, full "
            "card number, health details) spoken aloud unnecessarily. Never "
            "invent a finding; if nothing applies, return an empty list."
        ),
    },
    {
        "name": "follow-up-email",
        "description": "Drafts a follow-up email grounded in what was agreed on the call",
        "when_to_use": "Always — every call benefits from a drafted follow-up",
        "fields": {"claims": ["email_draft"]},
        "body_md": (
            "Draft a short, professional follow-up email from the company rep "
            "to the customer, grounded ONLY in what was agreed on this call. "
            "Do not promise anything not discussed. Plain text, no placeholders "
            "like [Name] — use the actual names from the transcript. Return it "
            "as a single email_draft claim whose text is 'Subject: ...\\n\\n"
            "<body>' and whose evidence cites the next-step commitments it "
            "draws from."
        ),
    },
]


def seed_agents_and_skills() -> None:
    with get_session() as session:
        if session.scalars(select(Orchestrator)).first() is None:
            session.add(Orchestrator(system_prompt=(
                "Decide which agents this call needs. There is currently one "
                "agent, Call Summarizer — dispatch it for every call that has "
                "a transcript, unless the transcript is too short to say "
                "anything meaningful about."
            )))

        skill_rows = {}
        for spec in BUILTIN_SKILLS:
            row = session.scalars(select(Skill).where(Skill.name == spec["name"])).first()
            if row is None:
                row = Skill(name=spec["name"], source="ui")
                session.add(row)
            row.description = spec["description"]
            row.when_to_use = spec["when_to_use"]
            row.fields = spec["fields"]
            row.body_md = spec["body_md"]
            session.flush()
            skill_rows[spec["name"]] = row

        agent = session.scalars(select(Agent).where(Agent.name == "Call Summarizer")).first()
        if agent is None:
            agent = Agent(name="Call Summarizer", description="", system_prompt="")
            session.add(agent)
        agent.description = "Summarizes calls and scores them against sales/support/compliance rubrics"
        agent.system_prompt = (
            "Always run summary-and-next-steps and compliance-check. Use "
            "sales-scorecard for sales calls (discovery, demo, pricing, "
            "negotiation) and support-scorecard for support calls (existing "
            "customer issues, billing, cancellations) — not both. Always run "
            "follow-up-email last."
        )
        session.flush()

        existing_links = {
            l.skill_id for l in session.scalars(select(AgentSkill).where(AgentSkill.agent_id == agent.id)).all()
        }
        for skill_name, row in skill_rows.items():
            if row.id not in existing_links:
                session.add(AgentSkill(agent_id=agent.id, skill_id=row.id))

        session.commit()


def seed_sample_calls() -> int:
    count = 0
    with get_session() as session:
        for path in sorted(SAMPLES_DIR.glob("*.json")):
            data = json.loads(path.read_text())
            c = data["call"]
            external_id = f"sample:{c['id']}"

            call = session.scalars(select(Call).where(Call.external_id == external_id)).first()
            if call is None:
                call = Call(id=c["id"], external_id=external_id)
                session.add(call)
            call.title = c["title"]
            call.source = c["source"]
            call.duration_s = c["duration_s"]
            audio = SAMPLES_DIR / "audio" / f"{c['id']}.wav"
            call.audio_path = str(audio) if audio.exists() else None

            if call.transcript is None:
                call.transcript = Transcript(call_id=call.id, lines=[])
            call.transcript.language = data["transcript"]["language"]
            call.transcript.lines = data["transcript"]["lines"]

            run = session.scalars(select(Run).where(Run.call_id == call.id)).first()
            if run is None:
                run = Run(call_id=call.id)
                session.add(run)
            run.status = "running"
            run.stages = [{"name": "transcribe", "status": "ok", "attempts": 1, "cost_usd": 0.0, "error": None}]

            count += 1
        session.commit()

    for path in sorted(SAMPLES_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        enqueue("run_insights", {"call_id": data["call"]["id"]})
    run_due_jobs()
    return count


def seed() -> int:
    Base.metadata.create_all(engine)
    seed_agents_and_skills()
    return seed_sample_calls()


if __name__ == "__main__":
    n = seed()
    print(f"seeded {n} sample call(s)")
```

- [ ] **Step 9: Update the tests that assumed the old pipeline**

Delete `backend/tests/test_run_state.py`'s tests for the removed `RunState`/`STAGES` (the harness's behavior is now covered by `test_agent_runtime.py` from Task 4) — delete the whole file, since nothing in it survives the cutover.

In `backend/tests/test_insights.py`, delete every test that exercises `detect_intent`/`extract`/`score`/`compose_email`/`run_insights`'s old stage list (the file's own `_seed_call`/`STAGES` import). Keep only tests unrelated to the retired functions, if any remain — if none do, delete the file (its coverage moves to `test_agent_executor.py` and `test_skill_executor.py`).

Replace `backend/tests/test_share.py` in full — its fixture built a `Run` with `insights=INSIGHTS` and an 8-element `STAGES`-based stage list; both are gone. The new fixture creates one `Agent` + one `AgentRun` holding output nested by skill name, matching what `run_insights` actually produces now. This rewrite also adds a compliance-exclusion regression test that had no equivalent before (compliance's exclusion used to be structural — a column these functions never read — and is now enforced by name in `render.py`, so it needs its own test):

```python
"""M4/M5 (agent-skill architecture cutover): edit-before-share, retry,
exports, and share-link lifecycle — now driven by AgentRun instead of
Run.insights/compliance. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §4.
"""

import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_session
from app.main import app
from app.models import Agent, AgentRun, Call, Run, Transcript

LINES = [
    {"line": 1, "speaker": "Ana", "text": "This call is recorded. How can I help?"},
    {"line": 2, "speaker": "Bob", "text": "My invoice was charged twice this month."},
]

AGENT_OUTPUT = {
    "summary-and-next-steps": {
        "summary": [{"text": "Bob was double-charged.", "evidence": [{"quote": "charged twice this month", "line": 2}]}],
        "next_steps": [{"text": "Refund the duplicate.", "evidence": [{"quote": "charged twice", "line": 2}]}],
    },
    "support-scorecard": {
        "issue_identified": {"value": True, "evidence": [{"quote": "charged twice", "line": 2}]},
    },
    "follow-up-email": {
        "email_draft": [{"text": "Subject: Your refund\n\nHi Bob, refund on the way.", "evidence": [{"quote": "charged twice", "line": 2}]}],
    },
    "compliance-check": {
        "compliance_findings": [{"text": "Should never appear in exports.", "evidence": [{"quote": "charged twice", "line": 2}]}],
    },
}


def _seed(run_status="shipped", agent_run_status="shipped", transcript=True, output=AGENT_OUTPUT):
    with get_session() as session:
        call = Call(title="Billing call", source="upload", external_id=f"s-{uuid.uuid4().hex}", duration_s=120)
        session.add(call)
        session.flush()
        if transcript:
            session.add(Transcript(call_id=call.id, lines=LINES))
        run = Run(
            call_id=call.id, status=run_status,
            stages=[{"name": "transcribe", "status": "ok", "attempts": 1, "cost_usd": 0.0, "error": None}],
        )
        session.add(run)
        session.flush()
        agent_run_id = None
        if output is not None:
            agent = Agent(name="Call Summarizer", description="d", system_prompt="p")
            session.add(agent)
            session.flush()
            agent_run = AgentRun(
                call_id=call.id, run_id=run.id, agent_id=agent.id, status=agent_run_status,
                steps=[{"name": k, "status": "ok", "attempts": 1, "cost_usd": 0.0, "error": None} for k in output],
                output=output,
            )
            session.add(agent_run)
            session.flush()
            agent_run_id = agent_run.id
        session.commit()
        return call.id, agent_run_id


def test_edit_then_export_reflects_edits():
    with TestClient(app) as c:
        call_id, agent_run_id = _seed()
        edited = json.loads(json.dumps(AGENT_OUTPUT))
        edited["follow-up-email"]["email_draft"][0]["text"] = "Subject: Refund confirmed — sorry for the mix-up\n\nHi Bob."
        r = c.patch(f"/api/calls/{call_id}/agent-runs/{agent_run_id}", json={"output": edited})
        assert r.json()["edited"] is True

        detail = c.get(f"/api/calls/{call_id}").json()
        ar = detail["agent_runs"][0]
        assert ar["edited"] is True
        assert "Refund confirmed" in ar["output"]["follow-up-email"]["email_draft"][0]["text"]

        md = c.get(f"/api/calls/{call_id}/export.md").text
        assert "Refund confirmed" in md
        assert "_edited_" in md


def test_original_ai_output_preserved_after_edit():
    with TestClient(app) as c:
        call_id, agent_run_id = _seed()
        edited = json.loads(json.dumps(AGENT_OUTPUT))
        edited["summary-and-next-steps"]["summary"] = [{"text": "Human rewrote this.", "evidence": []}]
        c.patch(f"/api/calls/{call_id}/agent-runs/{agent_run_id}", json={"output": edited})
        c.post(f"/api/calls/{call_id}/agent-runs/{agent_run_id}/reset")
        detail = c.get(f"/api/calls/{call_id}").json()
        ar = detail["agent_runs"][0]
        assert ar["edited"] is False
        assert ar["output"]["summary-and-next-steps"]["summary"][0]["text"] == "Bob was double-charged."


def test_export_json_excludes_compliance_but_keeps_evidence():
    with TestClient(app) as c:
        call_id, _ = _seed()
        data = c.get(f"/api/calls/{call_id}/export.json").json()
        for ao in data["agent_runs"]:
            assert "compliance-check" not in ao["output"]
        summary = data["agent_runs"][0]["output"]["summary-and-next-steps"]["summary"]
        assert summary[0]["evidence"][0]["line"] == 2


def test_markdown_transcript_opt_in():
    with TestClient(app) as c:
        call_id, _ = _seed()
        assert "## Transcript" not in c.get(f"/api/calls/{call_id}/export.md").text
        assert "## Transcript" in c.get(f"/api/calls/{call_id}/export.md?transcript=true").text


def test_markdown_excludes_compliance():
    with TestClient(app) as c:
        call_id, _ = _seed()
        md = c.get(f"/api/calls/{call_id}/export.md").text
        assert "Should never appear in exports" not in md


def test_share_link_freezes_snapshot_and_excludes_transcript():
    with TestClient(app) as c:
        call_id, agent_run_id = _seed()
        token = c.post(f"/api/calls/{call_id}/share").json()["token"]

        # edit AFTER sharing — the shared page must not change
        edited = json.loads(json.dumps(AGENT_OUTPUT))
        edited["summary-and-next-steps"]["summary"] = [{"text": "changed later", "evidence": []}]
        c.patch(f"/api/calls/{call_id}/agent-runs/{agent_run_id}", json={"output": edited})

        snap = c.get(f"/api/share/{token}").json()["snapshot"]
        frozen_summary = snap["agent_runs"][0]["output"]["summary-and-next-steps"]["summary"]
        assert frozen_summary[0]["text"] == "Bob was double-charged."  # frozen
        assert "transcript" not in snap
        for ao in snap["agent_runs"]:
            assert "compliance-check" not in ao["output"]


def test_share_revoke_404s():
    with TestClient(app) as c:
        call_id, _ = _seed()
        token = c.post(f"/api/calls/{call_id}/share").json()["token"]
        assert c.get(f"/api/share/{token}").status_code == 200
        c.post(f"/api/share/{token}/revoke")
        assert c.get(f"/api/share/{token}").status_code == 404


def test_retry_only_on_failed_or_partial():
    with TestClient(app) as c:
        shipped, _ = _seed(run_status="shipped")
        assert c.post(f"/api/calls/{shipped}/retry").status_code == 409

        partial, _ = _seed(run_status="partial")
        with get_session() as session:
            run = session.scalars(select(Run).where(Run.call_id == partial)).first()
            stages = [dict(s) for s in run.stages]
            stages[-1]["status"] = "failed"
            stages[-1]["error"] = "bad json"
            run.stages = stages
            session.commit()

        r = c.post(f"/api/calls/{partial}/retry")
        assert r.json()["from_stage"] == "run_insights"  # transcript exists

        with get_session() as session:
            run = session.scalars(select(Run).where(Run.call_id == partial)).first()
            assert run.status == "running"
            assert run.stages[-1]["status"] == "pending"


def test_retry_reprocesses_when_no_transcript():
    with TestClient(app) as c:
        call_id, _ = _seed(run_status="failed", transcript=False, output=None)
        with get_session() as session:
            run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
            run.stages = [{"name": "transcribe", "status": "failed", "attempts": 3, "cost_usd": 0.0, "error": "403"}]
            session.commit()
        r = c.post(f"/api/calls/{call_id}/retry")
        assert r.json()["from_stage"] == "process_call"
```

- [ ] **Step 10: Run the full backend suite**

Run: `cd backend && uv run pytest -v`
Expected: All tests pass. This is the gate for this task — do not proceed to Task 10 with a red suite.

- [ ] **Step 11: Run the parity seed against the five sample calls**

Run: `cd backend && uv run python scripts/seed.py` (requires a real `ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY` in `backend/.env` — see spec Known risks #1)
Expected: `seeded 5 sample call(s)`, and each sample call's `Run.status` is `shipped` or `partial` (not `failed`) with a populated `AgentRun.output` covering `summary-and-next-steps`, the correct one of `sales-scorecard`/`support-scorecard`, `compliance-check`, and `follow-up-email`. Manually inspect at least one sales call and one support call's output via `sqlite3 backend/data/opengong.sqlite3 "select output from agent_runs order by created_at desc limit 1;"` (or an equivalent quick script) to confirm the routing picked the right scorecard.

- [ ] **Step 12: Commit**

```bash
git add backend/app/models.py backend/app/pipeline.py backend/app/run_state.py backend/app/insights.py \
        backend/app/api/ingest.py backend/app/main.py backend/app/api/review.py backend/app/api/share.py \
        backend/app/render.py backend/scripts/seed.py backend/tests/
git commit -m "feat: cut over to the agent executor — orchestrator dispatch, per-agent runs, seeded built-in skills"
```

---

### Task 10: CRUD APIs for Agent and Skill management

**Files:**
- Create: `backend/app/api/agents.py`
- Create: `backend/app/api/skills.py`
- Modify: `backend/app/main.py` (register the two new routers)
- Test: `backend/tests/test_api_agents.py` (create)
- Test: `backend/tests/test_api_skills.py` (create)

**Interfaces:**
- Consumes: `Agent`, `Skill`, `AgentSkill` (Task 1); `parse_skill_md` (Task 2).
- Produces: REST endpoints — `GET/POST /api/agents`, `GET/PATCH/DELETE /api/agents/{id}`, `POST /api/agents/{id}/skills` (attach), `DELETE /api/agents/{id}/skills/{skill_id}` (detach); `GET/POST /api/skills`, `GET/PATCH/DELETE /api/skills/{id}`, `POST /api/skills/upload` (multipart file upload, parsed via `parse_skill_md`).

- [ ] **Step 1: Write the failing tests for agents**

Create `backend/tests/test_api_agents.py`:

```python
"""Agent CRUD API (Task 10 of the agent-skill architecture plan)."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_create_and_list_agent():
    resp = client.post("/api/agents", json={
        "name": "QA Coach", "description": "Assesses rep skill", "system_prompt": "Be strict.",
    })
    assert resp.status_code == 200
    agent_id = resp.json()["id"]

    listed = client.get("/api/agents").json()
    assert any(a["id"] == agent_id and a["name"] == "QA Coach" for a in listed)


def test_get_single_agent():
    created = client.post("/api/agents", json={"name": "a", "description": "d", "system_prompt": "p"}).json()
    fetched = client.get(f"/api/agents/{created['id']}").json()
    assert fetched["name"] == "a"
    assert fetched["skills"] == []


def test_update_agent():
    created = client.post("/api/agents", json={"name": "a", "description": "d", "system_prompt": "p"}).json()
    resp = client.patch(f"/api/agents/{created['id']}", json={"system_prompt": "Be strict and cite examples."})
    assert resp.status_code == 200
    assert resp.json()["system_prompt"] == "Be strict and cite examples."


def test_delete_agent():
    created = client.post("/api/agents", json={"name": "a", "description": "d", "system_prompt": "p"}).json()
    resp = client.delete(f"/api/agents/{created['id']}")
    assert resp.status_code == 200
    assert client.get(f"/api/agents/{created['id']}").status_code == 404


def test_attach_and_detach_skill():
    agent = client.post("/api/agents", json={"name": "a", "description": "d", "system_prompt": "p"}).json()
    skill = client.post("/api/skills", json={
        "name": "s", "description": "d", "when_to_use": "w", "body_md": "b", "fields": None,
    }).json()

    attach = client.post(f"/api/agents/{agent['id']}/skills", json={"skill_id": skill["id"]})
    assert attach.status_code == 200
    fetched = client.get(f"/api/agents/{agent['id']}").json()
    assert fetched["skills"][0]["id"] == skill["id"]

    detach = client.delete(f"/api/agents/{agent['id']}/skills/{skill['id']}")
    assert detach.status_code == 200
    fetched = client.get(f"/api/agents/{agent['id']}").json()
    assert fetched["skills"] == []


def test_get_missing_agent_404s():
    assert client.get("/api/agents/does-not-exist").status_code == 404
```

Create `backend/tests/test_api_skills.py`:

```python
"""Skill CRUD + upload API (Task 10 of the agent-skill architecture plan)."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SKILL_MD = """---
name: uploaded-skill
description: A skill uploaded from disk
when_to_use: When testing uploads
fields:
  claims:
    - summary
---

Summarize the call.
"""


def test_create_and_list_skill():
    resp = client.post("/api/skills", json={
        "name": "s", "description": "d", "when_to_use": "w", "body_md": "b", "fields": None,
    })
    assert resp.status_code == 200
    skill_id = resp.json()["id"]
    listed = client.get("/api/skills").json()
    assert any(s["id"] == skill_id for s in listed)


def test_update_skill():
    created = client.post("/api/skills", json={
        "name": "s", "description": "d", "when_to_use": "w", "body_md": "b", "fields": None,
    }).json()
    resp = client.patch(f"/api/skills/{created['id']}", json={"body_md": "New instructions."})
    assert resp.status_code == 200
    assert resp.json()["body_md"] == "New instructions."


def test_delete_skill():
    created = client.post("/api/skills", json={
        "name": "s", "description": "d", "when_to_use": "w", "body_md": "b", "fields": None,
    }).json()
    assert client.delete(f"/api/skills/{created['id']}").status_code == 200
    assert client.get(f"/api/skills/{created['id']}").status_code == 404


def test_upload_skill_from_md_file():
    resp = client.post(
        "/api/skills/upload",
        files={"file": ("uploaded-skill.md", SKILL_MD, "text/markdown")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "uploaded-skill"
    assert body["source"] == "upload"
    assert body["fields"]["claims"] == ["summary"]


def test_upload_rejects_malformed_skill_md():
    resp = client.post(
        "/api/skills/upload",
        files={"file": ("bad.md", "no frontmatter here", "text/markdown")},
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_api_agents.py tests/test_api_skills.py -v`
Expected: FAIL — 404s (routers not registered) or `ModuleNotFoundError`.

- [ ] **Step 3: Implement the Agent CRUD router**

Create `backend/app/api/agents.py`:

```python
"""Agent CRUD + skill attach/detach. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §4.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..db import get_session
from ..models import Agent, AgentSkill, Skill

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _serialize(a: Agent, skills: list[Skill] | None = None) -> dict:
    out = {
        "id": a.id, "name": a.name, "description": a.description,
        "system_prompt": a.system_prompt, "enabled": a.enabled,
    }
    if skills is not None:
        out["skills"] = [{"id": s.id, "name": s.name} for s in skills]
    return out


class AgentCreate(BaseModel):
    name: str
    description: str
    system_prompt: str


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    enabled: bool | None = None


@router.post("")
def create_agent(body: AgentCreate) -> dict:
    with get_session() as session:
        agent = Agent(name=body.name, description=body.description, system_prompt=body.system_prompt)
        session.add(agent)
        session.commit()
        return _serialize(agent)


@router.get("")
def list_agents() -> list[dict]:
    with get_session() as session:
        agents = session.scalars(select(Agent).order_by(Agent.created_at)).all()
        return [_serialize(a) for a in agents]


def _skills_for(session, agent_id: str) -> list[Skill]:
    links = session.scalars(select(AgentSkill).where(AgentSkill.agent_id == agent_id)).all()
    if not links:
        return []
    skill_ids = [l.skill_id for l in links]
    return session.scalars(select(Skill).where(Skill.id.in_(skill_ids))).all()


@router.get("/{agent_id}")
def get_agent(agent_id: str) -> dict:
    with get_session() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(404, "agent not found")
        return _serialize(agent, _skills_for(session, agent_id))


@router.patch("/{agent_id}")
def update_agent(agent_id: str, body: AgentUpdate) -> dict:
    with get_session() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(404, "agent not found")
        if body.name is not None:
            agent.name = body.name
        if body.description is not None:
            agent.description = body.description
        if body.system_prompt is not None:
            agent.system_prompt = body.system_prompt
        if body.enabled is not None:
            agent.enabled = body.enabled
        session.commit()
        return _serialize(agent, _skills_for(session, agent_id))


@router.delete("/{agent_id}")
def delete_agent(agent_id: str) -> dict:
    with get_session() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(404, "agent not found")
        for link in session.scalars(select(AgentSkill).where(AgentSkill.agent_id == agent_id)).all():
            session.delete(link)
        session.delete(agent)
        session.commit()
        return {"ok": True}


class SkillAttach(BaseModel):
    skill_id: str


@router.post("/{agent_id}/skills")
def attach_skill(agent_id: str, body: SkillAttach) -> dict:
    with get_session() as session:
        agent = session.get(Agent, agent_id)
        skill = session.get(Skill, body.skill_id)
        if agent is None:
            raise HTTPException(404, "agent not found")
        if skill is None:
            raise HTTPException(404, "skill not found")
        existing = session.scalars(
            select(AgentSkill).where(AgentSkill.agent_id == agent_id, AgentSkill.skill_id == body.skill_id)
        ).first()
        if existing is None:
            session.add(AgentSkill(agent_id=agent_id, skill_id=body.skill_id))
            session.commit()
        return {"ok": True}


@router.delete("/{agent_id}/skills/{skill_id}")
def detach_skill(agent_id: str, skill_id: str) -> dict:
    with get_session() as session:
        link = session.scalars(
            select(AgentSkill).where(AgentSkill.agent_id == agent_id, AgentSkill.skill_id == skill_id)
        ).first()
        if link:
            session.delete(link)
            session.commit()
        return {"ok": True}
```

- [ ] **Step 4: Implement the Skill CRUD + upload router**

Create `backend/app/api/skills.py`:

```python
"""Skill CRUD + .md upload. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §2, §4.
"""

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from ..db import get_session
from ..models import Skill
from ..skills.loader import SkillParseError, parse_skill_md

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _serialize(s: Skill) -> dict:
    return {
        "id": s.id, "name": s.name, "description": s.description, "when_to_use": s.when_to_use,
        "body_md": s.body_md, "fields": s.fields, "source": s.source, "version": s.version,
    }


class SkillCreate(BaseModel):
    name: str
    description: str
    when_to_use: str
    body_md: str
    fields: dict | None = None


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    when_to_use: str | None = None
    body_md: str | None = None
    fields: dict | None = None


@router.post("")
def create_skill(body: SkillCreate) -> dict:
    with get_session() as session:
        skill = Skill(
            name=body.name, description=body.description, when_to_use=body.when_to_use,
            body_md=body.body_md, fields=body.fields, source="ui",
        )
        session.add(skill)
        session.commit()
        return _serialize(skill)


@router.get("")
def list_skills() -> list[dict]:
    with get_session() as session:
        skills = session.scalars(select(Skill).order_by(Skill.created_at)).all()
        return [_serialize(s) for s in skills]


@router.get("/{skill_id}")
def get_skill(skill_id: str) -> dict:
    with get_session() as session:
        skill = session.get(Skill, skill_id)
        if skill is None:
            raise HTTPException(404, "skill not found")
        return _serialize(skill)


@router.patch("/{skill_id}")
def update_skill(skill_id: str, body: SkillUpdate) -> dict:
    with get_session() as session:
        skill = session.get(Skill, skill_id)
        if skill is None:
            raise HTTPException(404, "skill not found")
        if body.name is not None:
            skill.name = body.name
        if body.description is not None:
            skill.description = body.description
        if body.when_to_use is not None:
            skill.when_to_use = body.when_to_use
        if body.body_md is not None:
            skill.body_md = body.body_md
        if body.fields is not None:
            skill.fields = body.fields
        skill.version += 1
        session.commit()
        return _serialize(skill)


@router.delete("/{skill_id}")
def delete_skill(skill_id: str) -> dict:
    with get_session() as session:
        skill = session.get(Skill, skill_id)
        if skill is None:
            raise HTTPException(404, "skill not found")
        session.delete(skill)
        session.commit()
        return {"ok": True}


@router.post("/upload")
async def upload_skill(file: UploadFile) -> dict:
    content = (await file.read()).decode("utf-8")
    try:
        parsed = parse_skill_md(content)
    except SkillParseError as e:
        raise HTTPException(422, str(e)) from e

    with get_session() as session:
        skill = Skill(
            name=parsed["name"], description=parsed["description"], when_to_use=parsed["when_to_use"],
            body_md=parsed["body"], fields=parsed["fields"], source="upload",
        )
        session.add(skill)
        session.commit()
        return _serialize(skill)
```

- [ ] **Step 5: Register the routers**

In `backend/app/main.py`, add to the imports:

```python
from .api.agents import router as agents_router
from .api.skills import router as skills_router
```

And add to the router registration block (alongside the existing `app.include_router(...)` calls):

```python
app.include_router(agents_router)
app.include_router(skills_router)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_api_agents.py tests/test_api_skills.py -v`
Expected: 11 passed.

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && uv run pytest -v`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/agents.py backend/app/api/skills.py backend/app/main.py \
        backend/tests/test_api_agents.py backend/tests/test_api_skills.py
git commit -m "feat: add Agent/Skill CRUD APIs"
```

---

### Task 11: Frontend — left-nav shell + functional Agent/Skill CRUD screens

**Files:**
- Create: `web/components/Nav.tsx`
- Modify: `web/app/layout.tsx`
- Modify: `web/components/Header.tsx` (retire — its nav duties move to `Nav.tsx`; keep it only if `app/layout.tsx` still needs a top bar for the title, otherwise delete and update `web/app/page.tsx`'s import)
- Create: `web/app/agents/page.tsx`
- Create: `web/app/skills/page.tsx`
- Modify: `web/lib/api.ts` (add Agent/Skill types + fetch functions; remove the retired `intent` field from `CallSummary` and the `insights`/`compliance` fields from `CallDetail`, replaced with `agent_runs`; add `editAgentRunOutput`/`resetAgentRunOutput`)
- Modify: `web/app/page.tsx` (drop the retired `intent` badge; read `run_status` unchanged)
- **Modify: `web/components/CallView.tsx`** (rewrite to render `agent_runs` generically instead of the retired flat `insights`/`compliance` fields — this is load-bearing, not optional polish: Task 9's backend cutover removes `insights`/`compliance` from the API response entirely, and this component is the only thing rendering a call's results today. Without this change the `/calls/[id]` page silently breaks and `npm run build` fails on this file's now-invalid property access.)
- Modify: `web/lib/status.ts` (`stageLabels` currently maps the old 8 fixed stage names; replace with the new skill names so `ProcessingDetails`/step badges show readable labels instead of raw skill-name fallbacks)

**Interfaces:**
- Consumes: `GET/POST /api/agents`, `GET/PATCH/DELETE /api/agents/{id}`, `POST/DELETE /api/agents/{id}/skills[/{skill_id}]`, `GET/POST /api/skills`, `GET/PATCH/DELETE /api/skills/{id}`, `POST /api/skills/upload` (Task 10).
- Produces: `/agents` and `/skills` pages reachable from a persistent left-nav; `web/lib/api.ts` exports `listAgents`, `createAgent`, `updateAgent`, `deleteAgent`, `attachSkill`, `detachSkill`, `listSkills`, `createSkill`, `updateSkill`, `deleteSkill`, `uploadSkill`.

This is explicitly **not** a visual redesign (Global Constraints) — Tailwind classes matching today's existing look (`rounded-xl border border-neutral-200`, `text-sm`, etc., copied from `CallView.tsx`/`page.tsx`'s established patterns), laid out as a left sidebar + main content area instead of the current single centered column.

- [ ] **Step 1: Add Agent/Skill types and API functions to `web/lib/api.ts`**

Read the current file in full first. Remove `intent: string | null;` from `CallSummary`, and replace the `insights`/`compliance` fields on `CallDetail` with `agent_runs`. Also remove the now-dead `type Insights`, `type ScorecardField`, `type ComplianceFinding` (Task 9 deleted their backing endpoints and shape) and the `saveInsights`/`resetInsights` functions — they call `PATCH /api/calls/{id}/insights` and `POST /api/calls/{id}/insights/reset`, both deleted in Task 9. Keep `type Evidence` — it's still the right shape for every skill field's evidence array. Add after the existing `CallDetail` type:

```typescript
export type AgentRunSummary = {
  id: string;
  agent_id: string;
  agent_name: string;
  status: "pending" | "shipped" | "partial" | "failed";
  steps: { name: string; status: string; attempts: number; cost_usd: number; error: string | null }[];
  output: Record<string, Record<string, unknown>> | null;
  edited: boolean;
  cost_usd: number;
};
```

`id` is the `AgentRun`'s own primary key — the edit/reset endpoints below key on it, not on `agent_id`. `output` is keyed by skill name, each value the skill's own `{field_name: value}` dict.

Update `CallDetail`:

```typescript
export type CallDetail = {
  call: {
    id: string;
    title: string;
    source: string;
    duration_s: number | null;
    recorded_at: string;
    participants: string[];
  };
  run: { status: RunStatus; stages: Stage[]; orchestrator_reasoning: string | null };
  transcript: { language: string; lines: { line: number; speaker: string; text: string }[] } | null;
  agent_runs: AgentRunSummary[];
};
```

(There is no `crm_sync` field on this branch's `CallDetail` today, and Task 9's `get_call` rewrite does not add one — CRM sync lands in P2. Do not reference a `CrmSyncInfo` type; it doesn't exist.)

Update `CallSummary` (remove `intent`):

```typescript
export type CallSummary = {
  id: string;
  title: string;
  source: string;
  duration_s: number | null;
  recorded_at: string;
  run_status: RunStatus;
};
```

Add Agent/Skill types and fetch functions at the end of the file:

```typescript
export type Skill = {
  id: string;
  name: string;
  description: string;
  when_to_use: string;
  body_md: string;
  fields: { checks?: string[]; scores?: { name: string; max: number }[]; claims?: string[] } | null;
  source: "ui" | "upload";
  version: number;
};

export type Agent = {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  enabled: boolean;
  skills?: { id: string; name: string }[];
};

export const listAgents = () => fetch(`${API_BASE}/api/agents`, { cache: "no-store" }).then(j<Agent[]>);
export const getAgent = (id: string) => fetch(`${API_BASE}/api/agents/${id}`, { cache: "no-store" }).then(j<Agent>);
export const createAgent = (body: { name: string; description: string; system_prompt: string }) =>
  fetch(`${API_BASE}/api/agents`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then(j<Agent>);
export const updateAgent = (id: string, body: Partial<{ name: string; description: string; system_prompt: string; enabled: boolean }>) =>
  fetch(`${API_BASE}/api/agents/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then(j<Agent>);
export const deleteAgent = (id: string) => fetch(`${API_BASE}/api/agents/${id}`, { method: "DELETE" }).then(j);
export const attachSkill = (agentId: string, skillId: string) =>
  fetch(`${API_BASE}/api/agents/${agentId}/skills`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ skill_id: skillId }),
  }).then(j);
export const detachSkill = (agentId: string, skillId: string) =>
  fetch(`${API_BASE}/api/agents/${agentId}/skills/${skillId}`, { method: "DELETE" }).then(j);

export const listSkills = () => fetch(`${API_BASE}/api/skills`, { cache: "no-store" }).then(j<Skill[]>);
export const createSkill = (body: { name: string; description: string; when_to_use: string; body_md: string; fields: Skill["fields"] }) =>
  fetch(`${API_BASE}/api/skills`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then(j<Skill>);
export const updateSkill = (id: string, body: Partial<{ name: string; description: string; when_to_use: string; body_md: string; fields: Skill["fields"] }>) =>
  fetch(`${API_BASE}/api/skills/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then(j<Skill>);
export const deleteSkill = (id: string) => fetch(`${API_BASE}/api/skills/${id}`, { method: "DELETE" }).then(j);
export const uploadSkill = (file: File) => {
  const fd = new FormData();
  fd.append("file", file);
  return fetch(`${API_BASE}/api/skills/upload`, { method: "POST", body: fd }).then(j<Skill>);
};

export const editAgentRunOutput = (callId: string, agentRunId: string, output: Record<string, unknown>) =>
  fetch(`${API_BASE}/api/calls/${callId}/agent-runs/${agentRunId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ output }),
  }).then(j<{ ok: boolean; edited: boolean }>);
export const resetAgentRunOutput = (callId: string, agentRunId: string) =>
  fetch(`${API_BASE}/api/calls/${callId}/agent-runs/${agentRunId}/reset`, { method: "POST" }).then(
    j<{ ok: boolean; edited: boolean }>,
  );
```

`editAgentRunOutput`/`resetAgentRunOutput` replace the deleted `saveInsights`/`resetInsights` — they call Task 9's `PATCH /api/calls/{id}/agent-runs/{agent_run_id}` and its `/reset` sibling, keyed on the `AgentRun`'s own `id` (not `agent_id`).

- [ ] **Step 2: Build the left-nav shell**

Create `web/components/Nav.tsx`:

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  { href: "/", label: "Calls" },
  { href: "/agents", label: "Agents" },
  { href: "/skills", label: "Skills" },
];

export default function Nav() {
  const pathname = usePathname();
  return (
    <nav className="flex h-screen w-56 shrink-0 flex-col border-r border-neutral-200 px-4 py-5">
      <Link href="/" className="mb-6 flex items-center gap-2 px-2">
        <span className="text-lg font-semibold tracking-tight">Open Gong</span>
      </Link>
      <ul className="space-y-1">
        {ITEMS.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                className={`block rounded-lg px-3 py-2 text-sm ${
                  active ? "bg-neutral-900 text-white" : "text-neutral-600 hover:bg-neutral-100"
                }`}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
```

Modify `web/app/layout.tsx` — read the existing file first, then wrap `children` in a flex row with `<Nav />` beside it:

```tsx
import Nav from "@/components/Nav";
// ...existing imports stay...

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={/* existing className, unchanged */ inter.className}>
        <div className="flex">
          <Nav />
          <div className="min-h-screen flex-1">{children}</div>
        </div>
      </body>
    </html>
  );
}
```

(Preserve whatever the existing `layout.tsx` already does with fonts/metadata — only add the `Nav` wrapper around `children`.)

Delete `web/components/Header.tsx` and remove its `<Header />` usage from `web/app/page.tsx` (the nav now lives in the persistent left sidebar, not a per-page header) — replace `<Header />` with nothing, keeping `<main className="mx-auto max-w-3xl px-6 py-10">` as the first element inside the page's returned fragment.

- [ ] **Step 3: Drop the retired `intent` badge from the calls list**

In `web/app/page.tsx`, remove the `{c.intent && <span className="capitalize">{c.intent} call</span>}` line from the calls list rendering (the `intent` field no longer exists on `CallSummary` after Task 9/Step 1 of this task) — leave the duration/date spans next to it unchanged.

- [ ] **Step 4: Build the Agents CRUD page**

Create `web/app/agents/page.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import {
  attachSkill, createAgent, deleteAgent, detachSkill, getAgent, listAgents, listSkills, updateAgent,
  type Agent, type Skill,
} from "@/lib/api";

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selected, setSelected] = useState<Agent | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listAgents().then(setAgents).catch(() => {});
    listSkills().then(setSkills).catch(() => {});
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function openAgent(a: Agent) {
    const full = await getAgent(a.id);
    setSelected(full);
  }

  async function submit() {
    setError(null);
    try {
      if (!name.trim() || !description.trim() || !systemPrompt.trim()) {
        throw new Error("name, description, and system prompt are all required");
      }
      await createAgent({ name, description, system_prompt: systemPrompt });
      setName(""); setDescription(""); setSystemPrompt("");
      refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  async function toggleEnabled(a: Agent) {
    await updateAgent(a.id, { enabled: !a.enabled });
    refresh();
  }

  async function remove(a: Agent) {
    await deleteAgent(a.id);
    if (selected?.id === a.id) setSelected(null);
    refresh();
  }

  async function attach(skillId: string) {
    if (!selected) return;
    await attachSkill(selected.id, skillId);
    openAgent(selected);
  }

  async function detach(skillId: string) {
    if (!selected) return;
    await detachSkill(selected.id, skillId);
    openAgent(selected);
  }

  return (
    <main className="mx-auto flex max-w-5xl gap-8 px-6 py-10">
      <div className="flex-1">
        <h1 className="eyebrow mb-4 text-lg font-semibold">Agents</h1>

        <div className="mb-6 rounded-xl border border-neutral-200 p-4">
          <h2 className="mb-2 text-sm font-medium">New agent</h2>
          <input
            value={name} onChange={(e) => setName(e.target.value)} placeholder="Name"
            className="mb-2 w-full rounded-lg border border-neutral-300 px-3 py-1.5 text-sm"
          />
          <textarea
            value={description} onChange={(e) => setDescription(e.target.value)}
            placeholder="Description (what the orchestrator sees)"
            className="mb-2 w-full rounded-lg border border-neutral-300 px-3 py-1.5 text-sm" rows={2}
          />
          <textarea
            value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="System prompt (when to use which skill)"
            className="mb-2 w-full rounded-lg border border-neutral-300 px-3 py-1.5 text-sm" rows={3}
          />
          {error && <p className="mb-2 text-xs text-red-600">{error}</p>}
          <button onClick={submit} className="btn">Create agent</button>
        </div>

        <ul className="space-y-2">
          {agents.map((a) => (
            <li key={a.id} className="rounded-xl border border-neutral-200 px-4 py-3">
              <div className="flex items-center justify-between">
                <button onClick={() => openAgent(a)} className="text-left font-medium hover:underline">
                  {a.name}
                </button>
                <div className="flex items-center gap-2 text-xs">
                  <button onClick={() => toggleEnabled(a)} className="text-neutral-500 hover:text-neutral-900">
                    {a.enabled ? "Disable" : "Enable"}
                  </button>
                  <button onClick={() => remove(a)} className="text-red-500 hover:text-red-700">Delete</button>
                </div>
              </div>
              <p className="mt-1 text-xs text-neutral-500">{a.description}</p>
            </li>
          ))}
          {agents.length === 0 && (
            <li className="rounded-xl border border-dashed border-neutral-200 px-4 py-8 text-center text-sm text-neutral-400">
              No agents yet — create one above.
            </li>
          )}
        </ul>
      </div>

      {selected && (
        <div className="w-72 shrink-0 rounded-xl border border-neutral-200 p-4">
          <h2 className="mb-2 text-sm font-medium">{selected.name} — skills</h2>
          <ul className="mb-3 space-y-1">
            {(selected.skills ?? []).map((s) => (
              <li key={s.id} className="flex items-center justify-between text-sm">
                <span>{s.name}</span>
                <button onClick={() => detach(s.id)} className="text-xs text-red-500 hover:text-red-700">Remove</button>
              </li>
            ))}
            {(selected.skills ?? []).length === 0 && (
              <li className="text-xs text-neutral-400">No skills attached.</li>
            )}
          </ul>
          <label className="text-xs text-neutral-500">Attach a skill</label>
          <select
            onChange={(e) => e.target.value && attach(e.target.value)}
            value=""
            className="mt-1 w-full rounded-lg border border-neutral-300 px-2 py-1.5 text-sm"
          >
            <option value="">Choose a skill…</option>
            {skills
              .filter((s) => !(selected.skills ?? []).some((a) => a.id === s.id))
              .map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
          </select>
        </div>
      )}
    </main>
  );
}
```

- [ ] **Step 5: Build the Skills CRUD page**

Create `web/app/skills/page.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createSkill, deleteSkill, listSkills, uploadSkill, type Skill } from "@/lib/api";

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [whenToUse, setWhenToUse] = useState("");
  const [bodyMd, setBodyMd] = useState("");
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => listSkills().then(setSkills).catch(() => {}), []);
  useEffect(() => { refresh(); }, [refresh]);

  async function submit() {
    setError(null);
    try {
      if (!name.trim() || !description.trim() || !whenToUse.trim() || !bodyMd.trim()) {
        throw new Error("all fields are required");
      }
      await createSkill({ name, description, when_to_use: whenToUse, body_md: bodyMd, fields: null });
      setName(""); setDescription(""); setWhenToUse(""); setBodyMd("");
      refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  async function upload(file: File) {
    setError(null);
    try {
      await uploadSkill(file);
      refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  async function remove(s: Skill) {
    await deleteSkill(s.id);
    refresh();
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="eyebrow mb-4 text-lg font-semibold">Skills</h1>

      <div className="mb-6 rounded-xl border border-neutral-200 p-4">
        <h2 className="mb-2 text-sm font-medium">New skill</h2>
        <input
          value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (kebab-case)"
          className="mb-2 w-full rounded-lg border border-neutral-300 px-3 py-1.5 text-sm"
        />
        <input
          value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Description"
          className="mb-2 w-full rounded-lg border border-neutral-300 px-3 py-1.5 text-sm"
        />
        <input
          value={whenToUse} onChange={(e) => setWhenToUse(e.target.value)} placeholder="When to use"
          className="mb-2 w-full rounded-lg border border-neutral-300 px-3 py-1.5 text-sm"
        />
        <textarea
          value={bodyMd} onChange={(e) => setBodyMd(e.target.value)} placeholder="Instructions (prose)"
          className="mb-2 w-full rounded-lg border border-neutral-300 px-3 py-1.5 text-sm" rows={4}
        />
        {error && <p className="mb-2 text-xs text-red-600">{error}</p>}
        <div className="flex items-center gap-2">
          <button onClick={submit} className="btn">Create skill</button>
          <span className="text-xs text-neutral-400">or</span>
          <button onClick={() => fileRef.current?.click()} className="btn">Upload .md file</button>
          <input
            ref={fileRef} type="file" accept=".md" className="hidden"
            onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
          />
        </div>
      </div>

      <ul className="space-y-2">
        {skills.map((s) => (
          <li key={s.id} className="rounded-xl border border-neutral-200 px-4 py-3">
            <div className="flex items-center justify-between">
              <span className="font-medium">{s.name}</span>
              <div className="flex items-center gap-2 text-xs">
                <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-neutral-500">{s.source}</span>
                <button onClick={() => remove(s)} className="text-red-500 hover:text-red-700">Delete</button>
              </div>
            </div>
            <p className="mt-1 text-xs text-neutral-500">{s.description}</p>
          </li>
        ))}
        {skills.length === 0 && (
          <li className="rounded-xl border border-dashed border-neutral-200 px-4 py-8 text-center text-sm text-neutral-400">
            No skills yet — create or upload one above.
          </li>
        )}
      </ul>
    </main>
  );
}
```

- [ ] **Step 6: Rewrite `CallView.tsx` to render `agent_runs` generically**

Update `stageLabels` in `web/lib/status.ts` — the old dict maps the 8 retired stage names; replace it with the new skill names (any name not listed here already falls back to the raw name via the existing `?? s.name` call sites, so this dict only needs the common ones):

```typescript
export const stageLabels: Record<string, string> = {
  transcribe: "Transcribe",
  "summary-and-next-steps": "Summary & next steps",
  "sales-scorecard": "Sales scorecard",
  "support-scorecard": "Support scorecard",
  "compliance-check": "Compliance check",
  "follow-up-email": "Follow-up email",
};
```

Replace `web/components/CallView.tsx` in full. The old version rendered hardcoded fields (`summary`, `next_steps`, `scorecard`, `compliance`, `follow_up_email`) with per-field editing. Those fields don't exist anymore — output is a list of `AgentRunSummary`, each holding `{skill_name: {field_name: value}}`. This version renders every agent's every skill's fields generically (by shape — a score, a check, or a claims list — same approach as `render.py::_render_field` on the backend, for consistency) and edits a whole `AgentRun`'s output as JSON rather than field-by-field, which is a real simplification but not a regression: nothing in this plan asked for per-field inline editing of an arbitrary, user-defined skill schema, and building that generically was never in scope. Evidence citations (`jumpTo`/the "❝ proof" chip), retry, share, export, and the processing-details step badges are preserved as-is:

```tsx
"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  getCall,
  retryCall,
  editAgentRunOutput,
  resetAgentRunOutput,
  createShare,
  exportMarkdownUrl,
  exportJsonUrl,
  type CallDetail,
  type Evidence,
  type AgentRunSummary,
} from "@/lib/api";
import { humanizeStatus, tonePill, stageLabels } from "@/lib/status";
import Header from "@/components/Header";

function jumpTo(line: number) {
  const el = document.getElementById(`line-${line}`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("flash");
  void el.offsetWidth; // restart the animation
  el.classList.add("flash");
}

function Cite({ evidence }: { evidence: Evidence[] }) {
  if (!evidence?.length) return null;
  const title = evidence.map((e) => `L${e.line}: "${e.quote}"`).join("\n");
  return (
    <button className="cite" title={title} onClick={() => jumpTo(evidence[0].line)}>
      ❝ proof{evidence.length > 1 ? ` ·${evidence.length}` : ""}
    </button>
  );
}

function renderScalarField(value: unknown): { text: string; evidence: Evidence[] } {
  if (value && typeof value === "object" && "score" in (value as Record<string, unknown>)) {
    const v = value as { score: number; justification?: string; evidence: Evidence[] };
    return { text: `${v.score} — ${v.justification ?? ""}`, evidence: v.evidence ?? [] };
  }
  if (value && typeof value === "object" && "value" in (value as Record<string, unknown>)) {
    const v = value as { value: boolean | null; evidence: Evidence[] };
    return { text: v.value ? "yes" : v.value === false ? "no" : "—", evidence: v.evidence ?? [] };
  }
  return { text: String(value ?? ""), evidence: [] };
}

function SkillOutput({ skillName, fields }: { skillName: string; fields: Record<string, unknown> }) {
  return (
    <div className="card">
      <div className="eyebrow">{skillName.replaceAll("-", " ")}</div>
      <ul className="mt-2 space-y-2 text-sm leading-relaxed">
        {Object.entries(fields).map(([name, value]) => {
          if (Array.isArray(value)) {
            const items = value as { text: string; evidence: Evidence[] }[];
            if (items.length === 0) return <li key={name} className="text-neutral-400">{name.replaceAll("_", " ")}: none</li>;
            return items.map((item, i) => (
              <li key={`${name}-${i}`}>{item.text} <Cite evidence={item.evidence} /></li>
            ));
          }
          const rendered = renderScalarField(value);
          return (
            <li key={name} className="flex items-center gap-2">
              <span className="capitalize text-neutral-700">{name.replaceAll("_", " ")}:</span>
              <span className="font-medium">{rendered.text}</span>
              <Cite evidence={rendered.evidence} />
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function AgentRunCard({ callId, agentRun, onChanged }: { callId: string; agentRun: AgentRunSummary; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draftText, setDraftText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function startEdit() {
    setDraftText(JSON.stringify(agentRun.output ?? {}, null, 2));
    setError(null);
    setEditing(true);
  }
  async function save() {
    setBusy(true);
    setError(null);
    try {
      const parsed = JSON.parse(draftText);
      await editAgentRunOutput(callId, agentRun.id, parsed);
      setEditing(false);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function reset() {
    setBusy(true);
    try {
      await resetAgentRunOutput(callId, agentRun.id);
      setEditing(false);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">
          {agentRun.agent_name}
          {agentRun.edited && <span className="ml-2 rounded bg-purple-100 px-1.5 py-0.5 text-xs text-purple-700">edited by you</span>}
        </h2>
        {!editing ? (
          <button onClick={startEdit} className="btn text-xs">Edit</button>
        ) : (
          <div className="flex gap-2">
            <button onClick={save} disabled={busy} className="btn btn-primary text-xs">Save</button>
            <button onClick={() => setEditing(false)} className="btn text-xs">Cancel</button>
            {agentRun.edited && <button onClick={reset} disabled={busy} className="btn text-xs">Revert to AI original</button>}
          </div>
        )}
      </div>
      {editing ? (
        <div>
          <textarea
            className="edit-field min-h-[240px] w-full font-mono text-xs"
            value={draftText}
            onChange={(e) => setDraftText(e.target.value)}
          />
          {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
        </div>
      ) : (
        Object.entries(agentRun.output ?? {}).map(([skillName, fields]) => (
          <SkillOutput key={skillName} skillName={skillName} fields={fields} />
        ))
      )}
    </div>
  );
}

export default function CallView({ id }: { id: string }) {
  const [data, setData] = useState<CallDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [share, setShare] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => getCall(id).then(setData).catch((e) => setErr(String(e))), [id]);

  useEffect(() => {
    load();
    const t = setInterval(() => {
      setData((prev) => {
        if (prev && (prev.run.status === "running" || prev.run.status === "pending")) load();
        return prev;
      });
    }, 2500);
    return () => clearInterval(t);
  }, [load]);

  if (err) return <Shell><p className="text-sm text-red-600">{err}</p></Shell>;
  if (!data) return <Shell><p className="text-sm text-neutral-500">Loading…</p></Shell>;

  const { call, run, transcript, agent_runs } = data;
  const st = humanizeStatus(run.status);

  if (!transcript || agent_runs.length === 0) {
    return (
      <Shell>
        <h1 className="text-xl font-semibold tracking-tight">{call.title}</h1>
        <div className="mt-6 flex items-center gap-2 text-sm text-neutral-500">
          <span className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
          {transcript ? "Writing the notes…" : "Transcribing the call…"} This updates on its own.
        </div>
      </Shell>
    );
  }

  async function doRetry() { setBusy(true); try { await retryCall(id); await load(); } finally { setBusy(false); } }
  async function doShare() {
    setBusy(true);
    try { const { token } = await createShare(id); setShare(`${window.location.origin}/share/${token}`); } finally { setBusy(false); }
  }

  const failedSteps = agent_runs.flatMap((ar) => ar.steps.filter((s) => s.status === "failed"));

  return (
    <Shell>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{call.title}</h1>
          <p className="mt-1 text-xs text-neutral-500">
            {call.participants.join(", ")} · {new Date(call.recorded_at).toLocaleDateString()}
          </p>
        </div>
        <span className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ${tonePill[st.tone]}`}>
          {st.busy && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
          {st.label}
        </span>
      </div>

      {(run.status === "partial" || run.status === "failed") && failedSteps.length > 0 && (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div className="flex items-start justify-between gap-3">
            <div>
              {failedSteps.map((s) => (
                <div key={s.name}><strong>{stageLabels[s.name] ?? s.name}</strong> couldn&apos;t finish: {s.error}</div>
              ))}
            </div>
            <button onClick={doRetry} disabled={busy} className="btn btn-warn shrink-0">Retry</button>
          </div>
        </div>
      )}

      {run.orchestrator_reasoning && (
        <p className="mt-3 text-xs text-neutral-400">Why these agents ran: {run.orchestrator_reasoning}</p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button onClick={doShare} disabled={busy} className="btn">Share link</button>
        <a href={exportMarkdownUrl(id)} className="btn" download>Export .md</a>
        <a href={exportJsonUrl(id)} className="btn" download>Export .json</a>
      </div>

      {share && (
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm">
          <span className="text-emerald-800">Public link:</span>
          <input readOnly value={share} className="flex-1 bg-transparent text-emerald-900" />
          <button onClick={() => { navigator.clipboard.writeText(share); setCopied(true); setTimeout(() => setCopied(false), 1500); }} className="btn">
            {copied ? "Copied ✓" : "Copy"}
          </button>
          <button onClick={() => setShare(null)} className="text-neutral-400">✕</button>
        </div>
      )}

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-12">
        <div className="space-y-6 lg:col-span-7">
          {agent_runs.map((ar) => (
            <AgentRunCard key={ar.id} callId={id} agentRun={ar} onChanged={load} />
          ))}
          <ProcessingDetails run={run} />
        </div>

        <div className="lg:col-span-5">
          <div className="sticky top-4">
            <div className="mb-2 flex items-center justify-between">
              <div className="eyebrow">Transcript</div>
              <span className="text-xs text-neutral-400">click &quot;❝ proof&quot; in the notes to jump here</span>
            </div>
            <ol className="max-h-[72vh] space-y-1 overflow-y-auto rounded-xl border border-neutral-200 bg-white p-4 text-sm">
              {transcript.lines.map((l) => (
                <li key={l.line} id={`line-${l.line}`} className="flex scroll-mt-4 gap-3 rounded px-1 py-0.5">
                  <span className="w-6 shrink-0 text-right text-xs text-neutral-300">{l.line}</span>
                  <span><span className="font-medium">{l.speaker}:</span> <span className="text-neutral-700">{l.text}</span></span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <Header />
      <main className="mx-auto max-w-5xl px-6 py-8">
        <Link href="/" className="text-xs text-neutral-500 hover:text-neutral-800">← All calls</Link>
        <div className="mt-3">{children}</div>
      </main>
    </>
  );
}

function ProcessingDetails({ run }: { run: CallDetail["run"] }) {
  return (
    <details className="rounded-xl border border-neutral-200 px-5 py-3">
      <summary className="cursor-pointer text-xs font-medium text-neutral-500">Processing details</summary>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {run.stages.map((s) => (
          <span key={s.name} title={s.error ?? ""}
            className={`rounded px-1.5 py-0.5 text-xs ${
              s.status === "ok" ? "bg-emerald-50 text-emerald-700"
                : s.status === "failed" ? "bg-red-50 text-red-700"
                : s.status === "skipped" ? "bg-neutral-100 text-neutral-400"
                : "bg-blue-50 text-blue-700"
            }`}>
            {stageLabels[s.name] ?? s.name}
          </span>
        ))}
      </div>
    </details>
  );
}
```

Note: this drops the old `dropped_claims`-count line from the failure banner (it used to read `insights.dropped_claims`, a field that no longer exists — dropped claims are now visible per-skill as missing fields in `SkillOutput`, which is sufficient; a dedicated count is a nicety, not required for parity).

- [ ] **Step 7: Type-check and smoke-test the frontend**

Run: `cd web && npm run build`
Expected: builds cleanly, no TypeScript errors.

Run: `cd web && npm run dev` (in one terminal) and `cd backend && uv run uvicorn app.main:app --reload` (in another, with `backend/.env` keys set), then in a browser: visit `http://localhost:3000/`, confirm the left nav shows Calls/Agents/Skills; visit `/agents`, create an agent, attach a skill created on `/skills`, confirm it appears in the agent's skill list and detaches cleanly; visit `/` and confirm the calls list still loads and no longer shows a call-type badge; open a seeded sample call's detail page and confirm each agent's skill output renders with working evidence citations, and that editing/reverting an agent run's output round-trips correctly.

- [ ] **Step 8: Commit**

```bash
git add web/components/Nav.tsx web/app/layout.tsx web/app/agents/page.tsx web/app/skills/page.tsx \
        web/lib/api.ts web/app/page.tsx web/components/CallView.tsx web/lib/status.ts
git rm web/components/Header.tsx
git commit -m "feat: add left-nav shell, functional Agent/Skill CRUD screens, and generic agent-run rendering"
```

---

## Verification (from spec)

After Task 11, confirm every item from the spec's Verification section:

1. **Behavior parity** — Task 9 Step 11's manual seed-and-inspect check.
2. **Routing** — Task 9's `test_agent_executor.py` plus a live check during Step 11 (sales sample → `sales-scorecard` present in output, not `support-scorecard`, and vice versa).
3. **Evidence gate intact** — Task 3/5's fabricated-claim tests.
4. **Isolation** — Task 9's `test_one_agent_failing_does_not_affect_sibling`.
5. **Budget** — Task 4's budget tests plus Task 9's harness reuse.
6. **Config round-trip** — Task 11 Step 7's manual UI check (create agent, upload skill, attach, confirm it appears in routing candidates — full "it runs on the next call" confirmation requires re-running a call through `/api/ingest/upload` after attaching, worth doing once manually here).
7. **Full backend suite green; frontend type-check clean** — Task 9 Step 10, Task 11 Step 7.
