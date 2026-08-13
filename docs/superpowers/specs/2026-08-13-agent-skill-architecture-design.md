# Agent & Skill Architecture — Design

**Status:** Approved for planning
**Date:** 2026-08-13
**Phase:** Open Gong Phase 3 (P1 in this feature's own roadmap — see "Roadmap after P1" below; unrelated to the Phase-2 HubSpot CRM sync feature, which stays on its own unmerged branch)

## Context

Open Gong's current backend runs a **hardcoded 8-stage pipeline**
(`transcribe → detect_intent → extract → validate → score → compliance →
compose_email → crm_sync`, `backend/app/pipeline.py::run_insights`). It
works — 99 backend tests green, real calls processed end to end — but the
structure is wrong for the product: every capability is a fixed stage in one
chain, and the only configurable surface is an "insight pack" selected by a
hardcoded `intent → pack` lookup (`packs.py`).

What's actually wanted: **user-creatable AI agents** with distinct jobs (Call
Summarizer, CRM Automation, Company Knowledgebase, QA), each carrying
**skills as `.md` files** that apply conditionally — the same summarizer
uses a sales skill on sales calls, a support skill on support calls,
compliance when relevant. An **orchestrator** decides which agents a given
call needs. Agents will later call each other, coordinated by a master
agent. Skills and agents are configured in the UI, not in code — including
uploading a skill file from disk.

This is a restructure, not a rewrite: roughly 60% of the backend is
agent-agnostic infrastructure (the LLM gateway, the evidence gate, the job
queue, transcription, ingestion, exports/sharing) that survives untouched.
The guarantees that make the product trustworthy — the evidence gate ("no
proof, no claim"), capped retries with recorded reasons, a per-run budget
cap, and every run ending in exactly one of `shipped`/`partial`/`failed` —
are preserved. They stop being properties of one fixed pipeline and become
reusable primitives applied to a dynamic, per-agent step list.

**Decisions made during design (by the user), each with its own rationale:**
- Agents are **configured, not improvising** — an agent runs only the
  skills attached to it, steered by an editable system prompt. No
  open-ended tool-calling loop. This keeps cost predictable and behavior
  testable, which matters because Open Gong is OSS other people run on
  their own API keys.
- A skill is **one `.md` file**: YAML frontmatter (`name`, `description`,
  `when_to_use`) + prose + an optional `fields:` block — the same shape as
  a Claude Code `SKILL.md`, familiar to the target audience and trivially
  upload-from-disk.
- Routing is **LLM-based at two levels**, each steerable by an editable
  system prompt (not a hardcoded rules table) — because "which agent" and
  "which skill" are exactly the kind of judgment call a fixed rule table
  handles poorly (a call can be ambiguously sales-and-support, a skill's
  applicability is contextual), and the whole point is that behavior is
  reconfigurable from the UI without a code change.
- The **orchestrator dispatches lazily** — agents run only when invoked; a
  call may trigger three agents or one. An `EntryRule` (e.g. "this phone
  line is always support") can pin a single agent and skip the orchestrator
  call entirely — this matters once phone-line configuration exists and the
  routing answer is already known, so paying for an LLM call to reconfirm
  it is pure waste.
- **One `AgentRun` per agent per call** (not one `Run` per call) — because
  agents-calling-agents and per-agent status visibility are both explicit
  requirements; a single shared run object can't show "Summarizer shipped,
  QA partial" independently or attribute cost/retries per agent.
- UI/UX for the resulting screens is designed with the **Impeccable**
  skill, not hand-waved in this spec — see "Non-goals" below.

## Goals

- Replace the fixed pipeline chain with three user-configurable objects —
  **Orchestrator**, **Agent**, **Skill** — created, edited, and assigned
  entirely from the UI (including uploading a skill `.md` from disk).
- Preserve every existing trust guarantee unchanged: the evidence gate, the
  capped-retries/budget-cap harness, and the shipped/partial/failed
  contract — now applied per-`AgentRun` instead of per fixed stage.
- Prove parity: today's summarizer behavior (summary, next steps, scorecard,
  compliance, follow-up email) must be fully reproducible as a seeded
  "Call Summarizer" agent with five seeded skills, run for real against the
  five sample calls.
- Lay the data-model groundwork for agents calling agents (`parent_agent_run_id`)
  and for phone-line-scoped routing bypass (`EntryRule`), without building
  either behavior yet.

## Non-goals (this phase)

- **No agents-calling-agents execution.** The `parent_agent_run_id` column
  exists on `AgentRun`; nothing sets or reads it yet. That's P3.
- **No CRM Automation agent, no side-effecting actions.** The unmerged
  HubSpot branch's adapter/content-builder/`CrmSync` table survive
  untouched and land in P2 as that agent's action set — this phase only
  restructures the analysis path, not the write path.
- **No `EntryRule` UI.** The model and executor support a pinned agent
  bypassing the orchestrator call, but there is no screen to configure a
  phone line yet — the user explicitly flagged this as a later need, not a
  P1 requirement.
- **No polished visual redesign.** The left-nav shell and the agent/skill
  CRUD screens in this phase are functional Tailwind UI matching today's
  visual level, not an Impeccable-designed pass — that's a named follow-up
  once the redesign work is scheduled (see Roadmap).
- **No keyless demo fixtures for the new path yet** (see Known risks below,
  and Roadmap).

## Design

### 1. The model

```
call transcribed
   │
   ├─ entry rule pinned? ──────────────► invoke that agent directly (no orchestrator call)
   │
   └─ otherwise ──► ORCHESTRATOR (system prompt + agent roster)
                         │ decides: invoke [Summarizer, QA] — or just [Summarizer]
                         ▼
              AgentRun(Summarizer)              AgentRun(QA)
                 router → picks skills             router → picks skills
                 runs each skill as a step         runs each skill as a step
                 evidence gate on fielded output   ...
```

Three configurable objects:

| Object | What it is | Configured by |
|---|---|---|
| **Orchestrator** | Singleton. System prompt + roster of dispatchable agents. Decides which agents a call needs. | UI |
| **Agent** | Name, description (what the orchestrator sees), system prompt (`.md` — where "use MEDDIC only for enterprise deals" lives), attached skills. | UI |
| **Skill** | One `.md`: frontmatter (`name`, `description`, `when_to_use`) + prose body + optional `fields:`. | UI editor or file upload |

Two routing levels, both LLM-driven, both steerable by an editable prompt:

1. **Orchestrator dispatch** — sees the transcript, its own system prompt,
   and every enabled agent's `description` → returns the list of agents to
   invoke, plus its reasoning (stored for audit, not discarded).
2. **Agent skill-router** — sees the transcript, the agent's system prompt,
   and its attached skills' `name`/`description`/`when_to_use` → returns
   the list of skills to run for this call.

`detect_intent` (today's hardcoded classifier) disappears entirely — intent
stops being a special stage and becomes an ordinary input the routers weigh
alongside everything else in their prompts.

### 2. Skill file format

```markdown
---
name: sales-scorecard
description: Scores discovery quality and MEDDIC coverage on sales calls
when_to_use: The call is a sales conversation — discovery, demo, pricing, or negotiation
fields:
  checks:                      # boolean, evidence-required when true
    - budget_discussed
    - economic_buyer_identified
  scores:                      # 1..max, justification + evidence required
    - name: discovery_quality
      max: 5
  claims:                      # narrative lists, each item evidence-required
    - summary
    - next_steps
---

Score this call against MEDDIC. For each check, cite the exact line...
```

`checks`, `scores`, and `claims` are the three output shapes a skill can
declare, all nested under one `fields:` key (one JSON column on `Skill`). A
skill with no `fields:` at all produces narrative-only output (no gated
claims — e.g. a pure summarization skill with nothing to fact-check beyond
its prose). **Anything declared under `fields:` goes through the evidence
gate (`evidence.py`) unchanged** — a claim without a verifiable transcript
quote is dropped and the skill's step is marked partial, exactly as today's
`validate_extraction` behavior, just applied generically instead of to one
hardcoded pack shape.

### 3. What survives, what changes

**Reused as-is (~60% of the backend)** — genuinely agent-agnostic:
`llm.py` (provider gateway, JSON-repair retry, cost accounting) ·
`evidence.py` (`validate_extraction`/`validate_claims`/`_check_evidence` —
becomes a primitive called by the skill executor instead of by
`run_insights` directly) · `transcription.py` + `adapters/pyai/*` ·
`jobs.py` (the async job queue) · `db.py`, `config.py` · the `Call`/
`Transcript` models · `api/ingest.py`, `api/webhooks.py` · `render.py`,
`api/share.py` · `adapters/crm/*` and `crm_sync.py` (already the right
shape for P2; untouched here) · the frontend's evidence citation chips,
`jumpTo()`/flash behavior, and the share page.

**Reworked — concept survives, structure changes:**
- `run_state.py` — the guarantees (`MAX_ATTEMPTS`, `BudgetExceeded`,
  `final_status()`'s shipped/partial/failed contract) are kept verbatim;
  the fixed global `STAGES` list becomes a **dynamic step list scoped to
  one `AgentRun`**, built from that agent's routed skills instead of a
  module-level constant.
- `pipeline.py::run_insights` — the hardcoded chain becomes an **agent
  executor**: for each dispatched agent, build its `AgentRun`, route its
  skills, run each skill as a step inside that `AgentRun`'s own `RunState`.
  `process_call`/`poll_transcription`/`sync_call_to_crm` and the job-handler
  wiring around them are untouched (they operate above the insight chain).
- `insights.py` — `extract`/`score`/`compose_email` become the built-in
  seeded skills' implementations rather than fixed stages; `detect_intent`
  is deleted (subsumed by routing, per §1). `prettify_transcript` stays a
  pre-agent transcript-hygiene step, run once per call before any
  `AgentRun` starts (it isn't analysis, and every agent should see cleaned
  text).
- `packs.py` / `pack_compiler.py` / `frameworks.py` — **fold into skills.**
  A pack was already "prose compiled to an output schema"; the compiler
  survives as an optional "draft this skill's `fields:` block from my
  prose" authoring assist reachable from the skill editor.
  `frameworks.py`'s grounded MEDDIC/BANT/SPICED field definitions seed the
  built-in `sales-scorecard` skill's `fields:` and remain available to the
  compiler for custom skills.
- `compliance.py::run_compliance_check` — becomes the seeded
  `compliance-check` skill's implementation, not a hardcoded stage;
  `RULE_CATEGORIES`/`ABSENCE_RULES` move into that skill's authoring, not
  deleted.

**Retired outright:** the fixed-sequencing assumption itself (`STAGES` as a
global constant), and `packs.py`'s hardcoded `intent → pack` dispatch
(`select_pack` in `pipeline.py`). Almost nothing is deleted — most of what
"retires" is a fixed *assignment* of behavior to a stage, not the behavior
itself. `Run.insights`/`Run.compliance` retire as columns; their content
moves to `AgentRun.output`.

### 4. Data model

New tables:

| table | columns | notes |
|---|---|---|
| `Agent` | `id, name, description, system_prompt, enabled, created_at` | `description` is what the orchestrator sees when deciding whether to dispatch this agent |
| `Skill` | `id, name, description, when_to_use, body_md, fields (JSON, nullable), source (ui\|upload), version, created_at` | `fields` holds the parsed `checks`/`scores`/`claims` sub-keys from frontmatter; null means narrative-only |
| `AgentSkill` | `(agent_id, skill_id)` | join table — one skill can be attached to more than one agent |
| `Orchestrator` | `id, system_prompt, enabled` | singleton — always exactly one row |
| `AgentRun` | `id, call_id, run_id, agent_id, parent_agent_run_id (nullable), status (shipped\|partial\|failed), steps (JSON), output (JSON), cost_usd, error, created_at, finished_at` | one row per agent invoked per call; `parent_agent_run_id` is a P3 seam — unused, unset, and unread in this phase |
| `EntryRule` | `id, match_kind (phone_line\|source), match_value, agent_id` | model + executor support only, no UI — the user explicitly flagged phone-line config as a later need, not P1 |

Changed: **`Run`** slims to the per-call orchestration record — it holds
the orchestrator's dispatch decision, its reasoning (for audit), and an
aggregate status derived from its `AgentRun`s. `insights`/`compliance`
columns are removed; their content lives in each `AgentRun.output` instead.
**`Call`** and **`Transcript`** are unchanged.

Budgets: a per-`AgentRun` cap (new `MAX_COST_PER_AGENT_RUN`, same pattern
as today's `MAX_COST_PER_RUN`) **and** the existing overall per-call cap
enforced across the sum of all `AgentRun`s for that call — so a wide
orchestrator fan-out can't run away on aggregate cost even if each
individual agent stays under its own cap.

**Two edge cases, decided explicitly so they aren't discovered later:**
- *Orchestrator selects no agents.* The `Run` completes `shipped` with zero
  `AgentRun`s, and the orchestrator's reasoning is stored — so the UI can
  show **why** nothing ran (e.g. "transcript too short to classify")
  instead of looking broken or stuck.
- *A skill's LLM output fails schema validation* (after the one
  JSON-repair retry `llm.py` already does). Same path as today: the step
  fails with the reason attached, its `AgentRun` goes `partial`. Malformed
  output never reaches the user — this is `run_state.execute()`'s existing
  contract, just invoked per skill-step instead of per pipeline-stage.

### 5. Error handling summary

| situation | behavior |
|---|---|
| Entry rule matches | orchestrator call skipped entirely; the pinned agent's `AgentRun` starts directly |
| No entry rule, orchestrator selects zero agents | `Run` → `shipped`, zero `AgentRun`s, reasoning stored |
| A skill step's LLM output is unrepairable JSON | that step fails (capped retries via `MAX_ATTEMPTS`, same as today); its owning `AgentRun` → `partial` if the skill is non-critical to that agent, `failed` if not |
| A skill's `fields:` claim has no verifiable transcript evidence | claim dropped (not the whole step); `AgentRun` → `partial`, dropped-claim reason recorded, exactly like today's `validate_extraction` |
| One agent's `AgentRun` fails | isolated — sibling `AgentRun`s for the same call are unaffected; the call's aggregate `Run` status reflects the worst `AgentRun` outcome, same shipped/partial/failed vocabulary |
| Per-call budget cap exceeded across `AgentRun`s | remaining pending steps/agents are marked `skipped`, everything finalizes cleanly — no zombie runs |

### 6. Testing approach

- Unit tests for the skill `.md` parser (frontmatter + `fields:` sub-keys,
  malformed frontmatter, missing `when_to_use`, narrative-only skills with
  no `fields:` at all).
- Unit tests for the generalized evidence-gate integration point: same
  fixtures `evidence.py` already has, now driven through a skill's
  arbitrary `fields:` declaration instead of one hardcoded pack schema.
- Executor tests mirroring today's `test_pipeline.py` patterns: a fake
  orchestrator/router (deterministic stub, no LLM) driving a multi-agent,
  multi-skill call through `RunState` per `AgentRun`, covering isolation
  (one agent fails, sibling ships), the zero-agents edge case, and the
  aggregate-budget-exceeded-across-agents case.
- Parity test: the five sample calls run through the new agent path (with
  real keys — see Known risks) and are asserted equivalent in shape and
  substance to their current `Run.insights` output.
- Routing tests: a sales-transcript fixture routes to `sales-scorecard` and
  not `support-scorecard` (and vice versa) via the seeded agent's real
  router call; the orchestrator's stored reasoning is asserted non-empty.

## Known risks

1. **Zero-key demo is deferred (user decision).** The five sample calls
   currently ship as precomputed fixtures shaped as `Run.insights`. Rather
   than re-shape those fixtures into `AgentRun.output` now, this phase
   re-seeds the samples by running them through the new agent path for
   real (the developer has keys; cost is cents). Consequence, stated
   plainly: until this is revisited, a fresh clone with no API keys will
   not see populated sample calls on first run — the keyless-demo
   ship-checklist item (M11 in the Phase-1 backlog) is knowingly parked,
   not silently broken.
2. **Two LLM routing calls per call now** (orchestrator dispatch + each
   dispatched agent's skill router), on top of whatever skills actually
   run. Individually cheap (~$0.002 each at current model pricing) but
   must be measured against the real sample calls, not assumed — the
   entry-rule bypass exists partly to avoid the orchestrator call
   entirely once a phone line's routing answer is already known.
3. **No migration framework.** This codebase has no Alembic; SQLite is
   recreated from `Base.metadata.create_all`. Adding new tables is free;
   *moving* existing `Run.insights`/`Run.compliance` data into
   `AgentRun.output` for a developer's existing local database is not
   automatic. Acceptable — dev data is disposable and the samples reseed —
   but stated here so it's a known tradeoff, not a surprise.

## Roadmap after P1

- **P2** — CRM Automation agent with real side-effecting actions; merges
  the unmerged HubSpot branch's adapter/content-builder/`CrmSync` table as
  that agent's action set; adds multi-CRM adapters (Pipedrive, Salesforce)
  and an integrations directory UI.
- **P3** — Agent-to-agent invocation and master-agent coordination;
  activates `AgentRun.parent_agent_run_id`.
- **P4** — Company Knowledgebase agent: KB ingestion (URL crawl, PDF,
  docs), retrieval, slide-deck generation.
- **P5** — QA agent: rep assessment and coaching scorecards, working in
  tandem with the Knowledgebase agent.
- **Visual redesign** — once scheduled, the Impeccable skill designs the
  left-nav shell and CRUD screens this phase builds functionally; not
  blocking P1 delivery.

## Open questions (flagged, not blocking)

1. **Per-agent vs. global skill library** — this phase treats skills as a
   flat, globally-listed library any agent can attach; whether large
   deployments eventually want skill namespacing/visibility scoping is
   unresolved and not required to prove the model.
2. **Orchestrator roster size** — no cap is placed on how many agents the
   orchestrator's prompt can list; a very large roster may need
   summarization or retrieval-based roster filtering later, but the
   sample-scale roster (a handful of agents) doesn't hit this yet.
3. **Skill versioning semantics** — `Skill.version` exists as a column but
   this phase doesn't define upgrade/rollback behavior for an agent whose
   attached skill is edited after being attached; revisit once editing an
   in-use skill is a real workflow, not just authoring a new one.
