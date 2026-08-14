# Guaranteed Call Summaries + Call Log UI — Design

## Context

Every call used to get a summary, objections, next steps and a follow-up email,
each claim carrying a verbatim quote and line number. Commit `66da4e2` ("cut
over to the agent executor") replaced the fixed stage chain

```
STAGES = ["transcribe", "detect_intent", "extract", "validate", "score", "compliance", "compose_email"]
CRITICAL_STAGES = {"transcribe", "extract", "validate"}
```

with `_fresh_stages() -> ["transcribe"]` and made everything downstream
conditional on an orchestrator LLM choosing to dispatch an agent that happens to
own a relevant skill. Summaries stopped appearing.

The regression is worse than "new calls lack summaries": `/api/calls/{id}` and
`render.py` also stopped surfacing `Run.insights`, so the **9 pre-cutover runs
that still hold complete insight data render nothing**. Exports are empty too.

Two changes follow. First, put the baseline back and make it structurally
impossible to lose again. Second, restructure the UI into a call log and a
tabbed call detail, so the guaranteed output is the default view and agent output
is one click away.

Stored insights already have the target shape, so old and new calls render
identically:

```
summary:         [{text, evidence: [{quote, line}]}]
objections:      [{label, detail, status, evidence}]
next_steps:      [{text, owner, evidence}]
follow_up_email: {subject, body}
```

## Part 1 — The guaranteed baseline

Two fixed stages run after `transcribe` and **before** agent dispatch. They are
pipeline stages, not agents: not listed among agents, not configurable, not
disableable. No LLM decision sits between a call and its summary.

The restored chain is exactly:

```python
STAGES = ["transcribe", "summarize", "compose_email"]
CRITICAL_STAGES = {"transcribe", "summarize"}
```

**`summarize`** (function `insights.summarize`, adapted from the deleted
`insights.extract` minus its `pack` argument) — one `llm.complete_json` call
against a **built-in schema** for
`summary` / `objections` / `next_steps`, every claim requiring `{quote, line}`
evidence. Output goes through `evidence.validate_extraction()` **unchanged** —
that function already documents these exact three fields as its list-of-claims
case, and drops claims whose quote doesn't match the cited line. Dropped claims
are counted onto the stage, as before.

No pack selection. The scorecard and compliance checks are out of scope, which is
what lets `select_pack`, the `intent` stage, and the `Run.pack_id` coupling stay
retired rather than being revived.

**`compose_email`** — restore from `66da4e2^:backend/app/insights.py`. Schema
`{subject, body}`, grounded only in the validated `next_steps`/`summary`/
`objections`, no `[Name]` placeholders.

**Criticality**, mirroring the old `CRITICAL_STAGES`: `summarize` is critical —
if it fails after retries the run is `failed` and says so. `compose_email` is
not: its failure yields `partial` with the summary still shipped.

**Storage**: the existing, currently-unused `Run.insights` and
`Run.edited_insights` columns. **No migration.** The edit-then-share flow that
already prefers `edited_insights` over `insights` keeps working untouched.

**Agents are untouched.** They dispatch after the baseline and remain purely
additive. The user's `Call Summarizer` agent becomes redundant; leaving it alone
is a configuration decision, not part of this change.

## Part 2 — Call detail (layout B)

Two tabs over the main column, **transcript as a permanent right rail visible on
both tabs**.

The rail is not decoration. `CallView.tsx:23-33` binds every `.cite` chip to
`jumpTo(line)`, which scrolls `#line-N` into view and flashes it. Putting the
transcript behind its own tab would make each "receipt" click abandon the reader's
place in the summary — breaking the product's central promise. The rail keeps
claim and proof on screen together.

- **Summary tab (default)** — summary claims with citation chips, objections,
  next steps with owners, and the follow-up email (subject + body, copyable).
  Reuses the existing `EvidenceLinks` / `.cite` treatment.
- **Agent runs tab** — count badge; the existing per-agent cards, edit and retry
  behaviour unchanged.

**An agent run that did nothing must explain itself.** Today a run whose skill
router selected no skills is stored `status: "shipped"` with `output: NULL`
(`agent_runtime.py:92` returns `"shipped"` for an empty step list) and
`CallView.tsx:129` renders `Object.entries(null ?? {})` — an empty card with a
green badge. The router's explanation is computed at `pipeline.py:141` as
`router_reasoning` and **discarded**. Add `AgentRun.routing_reasoning` (nullable
text), persist it, serialize it, and render "No skills applied — <reason>" in
place of an empty card.

> `routing_reasoning` is a new column and this project has no migration tooling —
> `Base.metadata.create_all` will not add it to an existing table. Needs a manual
> `ALTER TABLE agent_runs ADD COLUMN routing_reasoning TEXT`, the same way
> `agents.is_orchestrator` was added.

**Responsive**: below `lg`, the rail moves beneath the main column; citations
scroll to it rather than sitting beside it.

## Part 3 — Call log (layout A)

`/` becomes a dense table, one line per call:

| Date | Call | Source | Length | Status | Agents |

Sortable columns. Filter toolbar above: free-text search, date range, source,
status, agent. **Only filters the data can back** — see Data gaps.

The always-open dashed drop zone stops owning the top of the page. An **Add
call** button reveals the file input and link field. The log is the page; ingest
is an action. The duplicate notice and error line added in `e92375c` move with it.

`GET /api/calls` gains an agent-run count per call for the Agents column.

## Out of scope / data gaps

- **No call direction.** `Call` stores `title, source (upload|url|sample),
  duration_s, recorded_at` — there is no inbound/outbound, rep, or phone number.
  "Call type" can only mean how the recording arrived. Direction, rep and number
  filters need new fields captured at ingest; that is separate work, and this
  design deliberately does not fake them.
- **Scorecard / InsightPack** stays retired. The `InsightPack` model, `/api/packs`
  CRUD and the Packs page remain orphaned; this design neither uses nor removes
  them.
- **Compliance checks** stay unwired. `compliance.run_compliance_check` remains
  available for a later change.
- **Uploads have no duration.** `duration_s` is computed for WAV only, so the
  four pre-existing MP3 uploads show blank in the Length column. Not backfilled.

### Optional (strike if unwanted)

Under layout A there is no summary snippet, so calls titled from a recording ID
(`REbe3fec20e15a80aa8d5a97bd81adcdd8`) are unreadable in the log — 4 of the
current 12 calls. Once `summarize` has run, derive a short human title from the
summary and use it when the ingested title looks like a bare identifier. Keeps
layout A scannable without adding a second line per row.

## Files

**Backend**
- `app/insights.py` — add `summarize` (from the deleted `extract`, built-in
  schema, no pack arg) and restore `compose_email` from `66da4e2^`;
  `prettify_transcript` unchanged.
- `app/run_state.py` — reinstate `STAGES` / `CRITICAL_STAGES` as given above.
- `app/pipeline.py` — run `summarize` → `compose_email` in `run_insights` before
  `_dispatch_and_run`; persist to `Run.insights`; persist `router_reasoning`.
- `app/agent_runtime.py` — `final_status()` for an empty step list.
- `app/models.py` — `AgentRun.routing_reasoning`.
- `app/main.py` — serialize `insights` on call detail; agent count on list.
- `app/render.py` — put insights back into `.md` / `.json` exports.

**Frontend**
- `web/app/page.tsx` — call log table, filter toolbar, Add call.
- `web/components/CallView.tsx` — tabs, transcript rail, no-skills-applied state.
- `web/lib/api.ts` — `Insights` type, `routing_reasoning`, list agent count.

## Testing

Reuses existing patterns: `TestClient(app)`, `monkeypatch` on module attributes,
`fakes.fake_llm`, `_drain_jobs`.

**Backend**
1. `summarize` runs on every call — no agent configured at all, summary still present.
2. Unproven claims are dropped by the evidence gate and counted.
3. `summarize` failure after retries ⇒ run `failed`.
4. `compose_email` failure ⇒ run `partial`, summary intact.
5. `insights` appears in `/api/calls/{id}` and in both export formats.
6. A pre-cutover run's stored insights serialize unchanged (backward compatibility).
7. Zero-step agent run: status is not a bare "shipped" with no explanation, and
   `routing_reasoning` is persisted.
8. `GET /api/calls` returns the agent count.

**Frontend**: typecheck and lint; then the verification below, since there is no
frontend test harness and adding one is not part of this change.

## Verification

1. `cd backend && uv run pytest -q` — existing 134 plus the new cases.
2. `cd web && npx tsc --noEmit && npx eslint app components lib`
   (`app/agents/page.tsx:36` has a pre-existing `react-hooks/set-state-in-effect`
   error unrelated to this work).
3. Run the app. On a **pre-cutover** call (Brightline discovery): Summary tab is
   the default and populated, citation chips scroll and flash the rail.
4. Ingest a **new** call and confirm summary, next steps and follow-up email
   appear with no agent configured — the guarantee.
5. On a call whose QA agent selected no skills, the Agent runs tab states that
   and gives the reason instead of an empty card.
6. Call log: filter by date/source/status, sort a column, add a call via **Add
   call**, and confirm the duplicate notice still appears for a repeat link.
7. Export `.md` and `.json` and confirm insights and citations are present.
