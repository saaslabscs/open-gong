# Integrations — Design

**Status:** Approved for planning
**Date:** 2026-08-14
**Phase:** Open Gong Phase 2, connection layer (precedes CRM sync)

## Context

Open Gong needs to write call insights into the CRM its users already live in.
Before any of that can happen, a user has to be able to *connect* their CRM —
and today there is no place in the product to do it.

The earlier CRM design
(`docs/superpowers/specs/2026-08-13-hubspot-crm-sync-design.md`, §1 "Connection")
answered this with a single HubSpot Private App token pasted into
`backend/.env`, on the grounds that a self-hosted single-tenant tool doesn't
need per-org OAuth. That reasoning holds for *OAuth*. It does not hold for
*invisibility*: a token in a hidden dotfile has no status, no verification, no
account label, and no way to tell "not configured" apart from "configured
wrong". With more than one provider it stops being manageable at all.

**This spec supersedes §1 of the HubSpot CRM sync design.** Connections are now
a first-class, UI-managed surface with their own nav section. Nothing else in
that spec changes; its matching rules, Note/Property writes, and `crm_sync`
pipeline stage remain the plan of record for the sync half.

## Goals

- A first-class **Integrations** section in the app where a user can see every
  supported CRM and its real connection state at a glance.
- Connect HubSpot and Pipedrive from the UI by pasting an API token, with
  in-context instructions for where to find that token.
- **A connection that exists is a connection that works** — credentials are
  verified against the provider before they are ever persisted.
- Make the state legible: which account, when connected, when last verified,
  and the exact provider error when something breaks.

## Non-goals (this feature)

- **No CRM writes.** No contact matching, no Note or Property writes, no
  `crm_sync` pipeline stage. That is the HubSpot sync spec's job, and it
  becomes buildable once connections exist.
- **No OAuth.** Salesforce ships as a visible, honest placeholder. Building
  OAuth machinery before a single provider needs it would be speculative.
- **No environment-variable configuration for integrations.** The database is
  the single source of truth (see §3).
- **No multi-account.** One connection per provider.
- **No encryption at rest.** See §6.
- **No `/api/status` integration rows.** Until sync exists, "no CRM connected"
  in the readiness banner reports on a capability that does nothing.

## Design

### 1. Provider registry

`app/integrations/providers.py` holds one declarative spec per provider:

```python
@dataclass(frozen=True)
class Provider:
    key: str                    # hubspot | pipedrive | salesforce
    label: str                  # "HubSpot"
    blurb: str                  # one line, shown on the card
    available: bool             # False → cannot be connected yet
    token_label: str | None     # "Private app access token"
    docs_url: str | None        # deep link to the exact settings page
    setup_steps: list[str]      # numbered instructions rendered in the card
    verify: VerifyFn | None     # (token) -> VerifyResult
```

`VerifyResult` is `{ok, account_label, account_ref, error}`.

Rejected alternatives:

- **An adapter class hierarchy** (`CrmAdapter` protocol + one subclass per
  provider), as the HubSpot spec proposed for the sync layer. At
  connections-only scope that protocol has exactly one method, so it is class
  ceremony wrapped around a function. When sync lands, these registry entries
  grow write behavior — or get a real adapter then, with a second method to
  justify it.
- **`if provider == "hubspot"` branching in the route handler.** Workable for
  two providers, sprawls at four, and tangles HTTP concerns with vendor quirks.

The registry is also the source of the catalog the UI renders (§5), so the
frontend has no hardcoded provider list that can drift.

### 2. Verify before persist

**A token is only written to the database after it successfully authenticates
against the provider.** A rejected token returns `400` carrying the provider's
own reason and stores nothing. This makes the broken-but-apparently-connected
state unrepresentable rather than merely unlikely.

| provider | verification call | why this call |
|---|---|---|
| HubSpot | `GET /crm/v3/objects/contacts?limit=1` (Bearer) | Proves the token *and* the `crm.objects.contacts.read` scope that sync will need — a generic whoami would prove only that the string is a token, deferring the real failure to first sync. |
| Pipedrive | `GET /v1/users/me` (`x-api-token` header) | Returns `company_name` (label) and `company_domain`, which is stored because Pipedrive's API is per-company-subdomain and sync will need it. |
| Salesforce | none | `available=False`; connect attempts are rejected before any network call. |

HubSpot's account label comes from `/account-info/v3/details` on a **best-effort**
basis: a private app may not hold that scope, and failing an otherwise-valid
connection over a cosmetic label would be wrong. No label is shown in that case.

### 3. Data model

One new table, `integrations`:

| column | notes |
|---|---|
| `provider` | pk — `hubspot` \| `pipedrive` |
| `access_token` | never returned by any endpoint |
| `account_label` | nullable, e.g. "Acme Inc" — from the verify call |
| `account_ref` | nullable — HubSpot portal id / Pipedrive `company_domain`; sync needs it |
| `status` | `connected` \| `error` |
| `last_error` | nullable, the provider's last failure reason |
| `connected_at` | |
| `last_verified_at` | |

The absence of a row **is** the not-connected state — there is no `disconnected`
status value, so disconnect is a delete and cannot leave a half-state behind.

`provider` as the primary key means one connection per provider, which is
correct for a single-tenant self-hosted tool and makes every operation
naturally idempotent. If multi-account ever matters this becomes an `id` primary
key with a `provider` index — a migration, not a redesign.

The database is the **only** source of truth. An earlier iteration of this
design let an `HUBSPOT_ACCESS_TOKEN` environment variable take precedence, for
continuity with `make init` and env-var deploys; that was dropped deliberately,
because two sources of truth for one credential means every status the UI
reports needs an "unless…" qualifier.

**Note for the sync feature:** the `crm_sync` stage must read its token from
this table, not from `os.environ`. No accessor is written here — nothing in
this feature calls one, and unused code is worse than an obvious call site.

### 4. API surface

`app/api/integrations.py`, mounted at `/api/integrations`:

| endpoint | behavior |
|---|---|
| `GET /api/integrations` | Catalog + state for every provider in the registry. Returns `token_hint` (`••••ab12`), never the token. |
| `PUT /api/integrations/{provider}` | `{token}` → verify → persist. `400` with the provider's reason on rejection, storing nothing. Replaces an existing connection (idempotent on `provider`). |
| `POST /api/integrations/{provider}/test` | Re-verify the stored token; update `status`, `last_error`, `last_verified_at`. |
| `DELETE /api/integrations/{provider}` | Disconnect — deletes the row. |

### 5. Frontend

| file | change |
|---|---|
| `web/components/Nav.tsx` | One line added to `ITEMS`. Kept minimal on purpose: another agent is working in `web/`. |
| `web/app/integrations/page.tsx` | New — fetch, refresh, action wiring. |
| `web/components/IntegrationCard.tsx` | New — the three card states plus the inline connect panel. |
| `web/lib/api.ts` | Types + four functions appended, in the existing style. |

Layout: a two-column grid of provider cards. Clicking **Connect** expands that
card in place into a panel holding the numbered setup steps, a deep link to the
provider's settings page, and the token field. No modal or drawer — the app has
no such primitive today, and inline expansion matches the existing form pattern
on the Skills page.

Card states:

- **Connected** — green status dot, account label, `••••ab12 · verified 2h ago`,
  with **Test** and **Disconnect**.
- **Not connected** — neutral status text and a **Connect** button.
- **Unavailable** (Salesforce) — muted dashed card, "Soon" pill, one line
  explaining it needs OAuth. **No button that does nothing.**
- **Error** — a connected row whose last verify failed: amber "Needs attention"
  plus the provider's error text, still offering Test and Disconnect.

UX decisions:

- **The instructions are the feature.** Finding a HubSpot private-app token is
  the step people actually get stuck on, so the steps and the deep link sit
  inside the connect panel, not behind a docs link.
- **No logo image files.** A small inline-SVG monogram tile per provider, tinted
  with the brand hue — self-contained, no network fetch, no trademark asset
  committed to the repo.
- **Errors persist in the card.** Never a toast that disappears before the 401
  has been read.
- The connect panel is a real `<form>` so Enter submits; the token input is
  `type="password"` with `autoComplete="off"` and `spellCheck={false}`; verify
  is a network round-trip, so the button shows a pending state and disables.
- Status is conveyed by text *and* color, never color alone. Each card is a
  `<section>` with a heading.

### 6. Security posture

The token is stored in the database in plaintext. Stating the reasoning
explicitly rather than leaving it implied: the SQLite file lives in gitignored
`backend/data/`, and an encryption key stored on the same machine as the
ciphertext defends against nothing that plaintext doesn't, while adding
key-rotation and key-loss failure modes.

What does reduce real risk, and is part of this feature:

- No endpoint ever returns the token — only a four-character hint.
- The token never appears in a log line, error message, or exception.
- The input is `type="password"` with autocomplete disabled.

If the database ever moves to shared or hosted Postgres, encryption at rest
becomes a genuine control and belongs then. Flagged in Open Questions rather
than silently decided.

### 7. Error handling

| situation | behavior |
|---|---|
| Empty or whitespace-only token | `400` before any network call — boundary validation |
| Token rejected (401/403) | `400` with the provider's reason; **nothing persisted**; error rendered inline in the card |
| Network error reaching the provider | `400` "couldn't reach HubSpot"; nothing persisted |
| Valid token, missing required scope | Treated as a rejection — failing at connect beats failing at first sync |
| `test` on a previously-good token that now fails | Row retained, `status=error` + `last_error`; card shows "Needs attention" |
| Connect attempt on an unavailable provider | `400`, not available yet |
| Any request for an unknown provider key | `404` |

### 8. Testing approach

Backend, `tests/test_api_integrations.py`, following the existing monkeypatch
style in `tests/conftest.py`. **No new dependencies** — httpx's built-in
`MockTransport` covers verify parsing, so `respx` is not needed.

1. Successful connect stores the account label and hint; the response body
   never contains the token.
2. Rejected token → `400` and **zero rows** written.
3. `test` on a now-invalid token → `status=error`, row retained.
4. Connect attempt on Salesforce → `400`; unknown provider → `404`.
5. Disconnect removes the row; `GET` then reports not-connected.
6. Verify parsers against `MockTransport` fixtures: HubSpot 200 and 401,
   HubSpot valid-token-but-no-label-scope fallback, Pipedrive `users/me`.

Frontend: `web/` has **no test runner** (`package.json` carries only eslint), so
verification there is `npm run build`, `npm run lint`, and driving the real page
against a running backend. No frontend tests will be claimed.

## Open questions (flagged, not blocking)

1. **Encryption at rest** — deliberately skipped while the database is a local
   SQLite file (§6). Revisit if Open Gong grows a hosted or shared-database
   deployment.
2. **Pipedrive API base URL** — `company_domain` is captured at connect time
   because per-company subdomains are the documented pattern for later calls;
   whether sync must use it or can stay on `api.pipedrive.com` is a sync-feature
   question, not a connection one.
3. **Salesforce OAuth** — needs a Connected App, a redirect URI, and
   sandbox-vs-production login hosts. Its own spec when it earns priority.
4. **Re-verification cadence** — `last_verified_at` is only refreshed on an
   explicit Test today. A periodic background health check is a reasonable
   follow-up once sync exists and a silently-dead token has real consequences.
