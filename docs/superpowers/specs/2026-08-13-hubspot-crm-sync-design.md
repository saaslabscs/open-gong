# HubSpot CRM Sync — Design

**Status:** Approved for planning
**Date:** 2026-08-13
**Phase:** Open Gong Phase 2, first feature (P2 in the roadmap)

## Context

Open Gong's Phase 1 loop (upload → transcribe → evidence-cited insights →
review/edit → share/export) is complete and verified on real audio. Phase 2
extends this to write insights into HubSpot automatically after every call.

The original Phase-1-era plan assumed a mandatory human-approval gate before
any CRM write (draft → review → approve → write). Discussion for this feature
revised that: **the target users are on the go, taking calls from a phone,
and will not reliably log into a separate web app to approve something
before it ships.** Meeting them where they live (CRM, Slack, SMS — plural,
to be built later) is the actual differentiator; gating everything behind a
web-app approval step that goes unchecked defeats it.

Decision for this feature: **remove the pre-write approval gate. Log
everything directly, with inline call-outs so a human can see what needs
attention whenever they look — in their own time, in whatever tool they're
already in.** A human-approval mechanism (multi-channel: HubSpot Task, Slack,
SMS) is planned for later and will initially gate **property writes only**
(Notes stay direct, permanently — see "Why Notes are always direct" below).
This spec's data model is built so that later gate is a status-flow change,
not new tables or a rewrite.

## Goals

- After a call finishes processing, automatically write its insights into
  HubSpot: a Note (always) and a small fixed set of Properties (for now).
- Never write to the wrong record. If we can't confidently match the call to
  a HubSpot Contact, skip the write and flag it rather than guess.
- Never silently drop a write. Every attempt is auditable; failures are
  visible and retryable, exactly like the existing pipeline stages.
- Work with ANY insight pack — built-in sales/support or a custom pack
  compiled from prose (M9) — without per-pack HubSpot property management.

## Non-goals (this feature)

- No approval gate (planned later, for properties only — see Context).
- No OAuth app flow — single private-app token, matching the existing
  `.env`-based key pattern for PyAI/LLM.
- No dynamic per-pack-field HubSpot properties. Pack-specific detail lives in
  the Note; Properties are a small, fixed, pack-agnostic set.
- No multi-CRM support. HubSpot only; the connector is written behind a
  narrow interface so a second CRM later doesn't require touching this one.
- No Deal object sync — Contact only, for this feature. (HubSpot Deals need
  pipeline/stage mapping that's a reasonable follow-up, not required to
  prove the loop.)

## Design

### 1. Connection

A HubSpot **Private App access token**, pasted via `make init` /
`backend/.env` (`HUBSPOT_ACCESS_TOKEN`) — the same onboarding pattern already
used for `PYAI_API_KEY` / `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY`. No
per-org OAuth, no token refresh logic: this is a self-hosted, single-tenant
tool, and OAuth only earns its keep for a multi-tenant hosted SaaS.

`app/setup_env.py`'s `status()` gains a `crm` section
(`{configured: bool, detail}`) so `doctor`/the UI banner can say whether
HubSpot sync is live, same as the existing LLM/transcription rows.

### 2. Matching

Match by the call's phone number (E.164-normalized) against HubSpot Contact
`phone`/`mobilephone`, then by email if the call carries one (future
ingestion paths might). **"Confident match" is defined narrowly: exactly one
HubSpot Contact returned for the normalized phone or the email — zero
results or more than one both count as no match.** No confident match →
skip the write, record `status=unmatched` on the CrmSync row, and surface it
as a call-out in the run (same place dropped-evidence claims and failed
stages already show).
This is deliberately conservative: a wrong match silently corrupts someone's
CRM data, which is worse than not writing at all. No new-contact creation in
this feature — that's a meaningfully different trust decision (do we want
Open Gong creating people in your CRM?) left for later.

### 3. What gets written

**Note — always, direct, no gate.** One HubSpot Note (engagement) per call,
associated with the matched Contact. Content: summary, next steps, full
scorecard breakdown, the follow-up email draft, and **inline ⚠️ call-outs**
for:
- dropped claims (insufficient evidence — the exact same list already shown
  in the review UI's failure banner)
- judgment scores below a threshold (configurable later; hardcoded low bar
  for v1 — see Open Questions)
- compliance findings, if present — HubSpot is internal to the org, not a
  public artifact, so this doesn't violate the existing "compliance never
  leaves the building" rule established for share links/exports; it's a
  different building.

**Why Notes are always direct, even after the future approval gate lands:**
HubSpot Notes are append-only and don't overwrite anything — the worst case
is a human ignores it. They're the low-risk half of this feature and there's
no safety reason to gate them, now or later.

**Properties — direct for now, gated later.** A small, fixed, pack-agnostic
set, upserted (overwritten) on every synced call:
- `open_gong_deal_size` (text — free-form, since extraction may return
  "$15k/year" not a clean number; parsing to currency is a later nicety)
- `open_gong_score` (number — the primary/first judgment score from the
  active pack's scoring spec, 0 if none)
- `open_gong_flags` (text — the same call-out summary as the Note, short
  form, so it's visible in list/table views without opening the Note)
- `open_gong_last_synced_at` (datetime)

These are **overwritten** each call (upsert, not append) — that's exactly
the risk that pushed "properties should eventually be gated" in the first
place. Fixing that is explicitly deferred; this spec only has to make the
future fix a status-flow change, not a rewrite (see §4).

Nothing pack-specific becomes a Property in this feature. A custom pack's
own fields (e.g. `economic_buyer_identified` from a MEDDIC pack) live in the
Note only. Revisit if/when there's real demand for per-field HubSpot
reporting — that's dynamic-schema-management complexity not justified yet.

### 4. Data model

One new table, `CrmSync`:

| column | notes |
|---|---|
| `id` | pk |
| `call_id` | fk → Call |
| `run_id` | fk → Run (which run's insights were synced) |
| `status` | `unmatched \| auto_written \| pending_review \| approved \| rejected \| failed` — only `unmatched`/`auto_written`/`failed` are reachable in this feature; the rest exist now so the future approval gate is a new code path writing into existing states, not a new column |
| `hubspot_contact_id` | nullable — null when unmatched |
| `hubspot_note_id` | nullable — set once the Note write succeeds |
| `properties_written` | JSON snapshot of what was sent, for audit/debugging |
| `error` | nullable, last failure reason |
| `attempts` | int |
| `created_at`, `updated_at` | |

One `CrmSync` row per (call, run) — a retry updates the same row rather than
creating a new one (idempotency; see §5).

### 5. Pipeline integration

New non-critical stage, `crm_sync`, added to `run_state.STAGES` after
`compose_email` (last stage — it's the "ship it out" step once everything
else is ready, and the Note can reference the drafted email). Same harness
guarantees as `compliance`/`compose_email`: capped retries, failure marks the
run `partial` (never `failed` — a CRM sync problem shouldn't erase the
insights themselves), reason attached, retryable via the existing
`/api/calls/{id}/retry` endpoint (which already generically retries failed
stages).

Idempotency: the stage checks for an existing `CrmSync` row for this
`(call_id, run_id)` before writing. If `hubspot_note_id` is already set,
skip re-posting the Note (log-once); Properties are naturally idempotent
(upsert). This mirrors the idempotency pattern already used for
transcription (`Call.external_id`) and the job queue.

Graceful absence: if `HUBSPOT_ACCESS_TOKEN` isn't configured, the stage
short-circuits to `status=ok` immediately with no HubSpot calls — CRM sync
is opt-in, not a hard dependency, same posture as the Trace-vs-in-house
compliance fallback.

### 6. Connector shape

`app/adapters/crm/base.py` — a narrow `CrmAdapter` protocol
(`find_contact`, `write_note`, `upsert_properties`), with `hubspot.py` as the
one real implementation. No `mock`/`real` split like the PyAI adapter this
time — HubSpot's own developer test accounts are free and realistic enough
that tests hit a `responses`-style stubbed HTTP layer instead of a bespoke
mock adapter. (This mirrors "don't build the interface until a second
implementation exists" from the Phase 1 red-team review — the interface here
is justified because CRM #2 is explicitly on the roadmap, unlike PyAI where
there was never going to be a second transcription vendor.)

### 7. Error handling summary

| situation | behavior |
|---|---|
| No `HUBSPOT_ACCESS_TOKEN` configured | stage no-ops, `status=ok`, no CrmSync row |
| No confident contact match | `CrmSync.status=unmatched`, call-out surfaced in run, no write attempted |
| HubSpot API error (auth/rate-limit/network) | capped retries (matches `run_state.MAX_ATTEMPTS`), then `status=failed`, run → `partial`, reason attached, retryable |
| Retry after partial success (e.g. Note wrote, Properties failed) | Note is not re-posted (idempotent on `hubspot_note_id`); only the missing half retries |

### 8. Testing approach

- Unit tests for matching logic (phone normalization edge cases) with no
  network.
- Unit tests for the Note/Properties content builder (call-out inclusion
  logic) against fixture insights — including the dropped-claims and
  compliance-finding call-out paths.
- Pipeline tests (same pattern as `test_insights.py`/`test_compliance.py`):
  fake the HubSpot adapter, verify `crm_sync` stage behavior — success,
  unmatched, failure/retry, idempotent re-run.
- No live-HubSpot spike is planned before implementation (unlike the PyAI M7
  gate) — HubSpot's REST API and Private App auth are extremely
  well-documented and don't carry the same "does this even work like the
  docs say" risk PyAI did. A short manual smoke test against a real HubSpot
  developer test account happens during implementation, not as a
  pre-implementation gate.

## Open questions (flagged, not blocking)

1. **Judgment-score "low" threshold for call-outs** — hardcoding a bar (e.g.
   ≤2/5) for v1; making this configurable per pack is natural follow-up once
   custom packs see real use.
2. **Deal size parsing** — stored as free text for v1 (`"$15k/year"`); a
   currency-normalization pass is a nicety, not required to prove the loop.
3. **Multiple candidate contacts** (e.g. shared phone line) — currently
   treated the same as "no confident match" (skip + flag). Worth revisiting
   once real usage shows how often this happens.
