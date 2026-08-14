# Integrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Integrations section where a user connects HubSpot or Pipedrive by pasting an API token that is verified against the provider before it is stored, with Salesforce shown as an honest placeholder.

**Architecture:** A declarative provider registry (`app/integrations/providers.py`) holds one spec per CRM, including a `verify(token)` function that proves the token works. A thin FastAPI router (`app/api/integrations.py`) turns that registry plus one `integrations` table into four endpoints; the token is never returned, only a four-character hint. The frontend renders whatever the registry reports — a card grid with an inline connect panel — so there is no hardcoded provider list in the UI.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0, httpx (already a dependency; `httpx.MockTransport` for tests), pytest. Next.js 16.3.0, React 19.2.8, TypeScript, Tailwind CSS 4.

**Spec:** `docs/superpowers/specs/2026-08-14-integrations-design.md`

## Global Constraints

- **No new dependencies**, Python or npm. `httpx.MockTransport` covers HTTP stubbing; do not add `respx`.
- **The access token is never returned by any endpoint, and never logged** — not in a log line, not in an error message, not in an exception. Only `token_hint` (`••••` + last 4 chars) leaves the backend.
- **The database is the only source of truth.** Do not read or write environment variables for integrations. Do not add integration keys to `app/setup_env.py`.
- **Verify before persist.** A token that fails verification must leave zero rows written.
- **No CRM writes, no OAuth, no encryption at rest, no `/api/status` rows** — all out of scope (spec §"Non-goals").
- Keep every file under 500 lines.
- Follow existing house style: `with get_session() as session:` + explicit `session.commit()`; `HTTPException(404, "message")` positional; a module-level `_serialize()` helper per router; a module docstring citing the spec section.
- Frontend: light theme only. Reuse the `.card`, `.btn`, `.btn-primary`, `.eyebrow` helpers from `web/app/globals.css` and `tonePill` from `web/lib/status.ts`. Use typographic apostrophes (’) in user-facing copy, as the rest of the app does.
- **Never call `Date.now()` in a React render body** — the `react-hooks/purity` lint rule fails the build. Use the established `const [now] = useState(() => Date.now())` pattern (see `web/app/page.tsx`) and pass the value down.
- Status must be conveyed by **text and** color, never color alone.
- No logo image files and no external asset fetches — provider marks are CSS-tinted letter tiles.
- Run all backend commands from the `backend/` directory.

---

### Task 1: Provider registry and token verification

The registry is the single source of truth for what exists and how to prove a token works. It has no database or HTTP-layer knowledge, so it is testable on its own.

**Files:**
- Create: `backend/app/integrations/__init__.py`
- Create: `backend/app/integrations/providers.py`
- Modify: `backend/tests/conftest.py` (add a `crm_http` fixture next to the existing `url_ingest` fixture)
- Test: `backend/tests/test_integration_providers.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `VerifyResult(ok: bool, account_label: str | None, account_ref: str | None, error: str | None)` — frozen dataclass, all fields default `None` except `ok`.
  - `Provider(key, label, blurb, available, token_label, docs_url, setup_steps, verify)` — frozen dataclass. `setup_steps` is a `tuple[str, ...]`; `verify` is `Callable[[str], VerifyResult] | None`.
  - `PROVIDERS: tuple[Provider, ...]` — ordered `hubspot`, `pipedrive`, `salesforce`.
  - `BY_KEY: dict[str, Provider]`.
  - `verify_hubspot(token: str) -> VerifyResult`, `verify_pipedrive(token: str) -> VerifyResult`.
  - `_TRANSPORT: httpx.BaseTransport | None` — module-level test injection hook, mirroring `app/api/ingest.py:45`.

- [ ] **Step 1: Add the shared HTTP-stub fixture to conftest**

Append to `backend/tests/conftest.py` (it already imports `pytest` and `app.api.ingest as ingest_mod`; add the two new imports at the top alongside them):

```python
import httpx

import app.integrations.providers as providers_mod
```

```python
@pytest.fixture
def crm_http(monkeypatch):
    """Point CRM token verification at a fake transport (mirrors url_ingest).

    Routes are (url_fragment, status_code, json_body). A fresh Response is built
    per request so the same route can serve repeated calls.
    """

    def install(*routes: tuple[str, int, dict]):
        def handler(request: httpx.Request) -> httpx.Response:
            for fragment, status, body in routes:
                if fragment in str(request.url):
                    return httpx.Response(status, json=body)
            return httpx.Response(404, json={"message": "no route registered in test"})

        monkeypatch.setattr(providers_mod, "_TRANSPORT", httpx.MockTransport(handler))

    return install
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_integration_providers.py`:

```python
"""Token verification for each CRM provider (integrations design §1, §2)."""

import httpx
import pytest

import app.integrations.providers as providers
from app.integrations.providers import BY_KEY, PROVIDERS, verify_hubspot, verify_pipedrive

CONTACTS = "crm/v3/objects/contacts"
ACCOUNT = "account-info/v3/details"
PIPEDRIVE_ME = "users/me"


def test_registry_lists_three_providers_in_order():
    assert [p.key for p in PROVIDERS] == ["hubspot", "pipedrive", "salesforce"]
    assert BY_KEY["hubspot"].available is True
    # Salesforce is a placeholder: nothing to connect with, so nothing to verify.
    assert BY_KEY["salesforce"].available is False
    assert BY_KEY["salesforce"].verify is None


def test_hubspot_verify_ok_uses_portal_id_as_label(crm_http):
    crm_http(
        (CONTACTS, 200, {"results": []}),
        (ACCOUNT, 200, {"portalId": 12345678}),
    )
    result = verify_hubspot("pat-na1-good")
    assert result.ok is True
    assert result.account_ref == "12345678"
    assert result.account_label == "Portal 12345678"
    assert result.error is None


def test_hubspot_verify_ok_without_account_info_scope(crm_http):
    """A private app may lack the account-info scope. A missing cosmetic label
    must not fail an otherwise-working connection."""
    crm_http(
        (CONTACTS, 200, {"results": []}),
        (ACCOUNT, 403, {"message": "missing scope"}),
    )
    result = verify_hubspot("pat-na1-good")
    assert result.ok is True
    assert result.account_label is None
    assert result.account_ref is None


def test_hubspot_verify_rejects_bad_token(crm_http):
    crm_http((CONTACTS, 401, {"message": "invalid token"}))
    result = verify_hubspot("nope")
    assert result.ok is False
    assert "HubSpot" in result.error
    assert "401" in result.error


def test_pipedrive_verify_ok_captures_company(crm_http):
    crm_http(
        (PIPEDRIVE_ME, 200, {"data": {"company_name": "Acme Inc", "company_domain": "acme"}}),
    )
    result = verify_pipedrive("token")
    assert result.ok is True
    assert result.account_label == "Acme Inc"
    assert result.account_ref == "acme"


def test_pipedrive_verify_rejects_bad_token(crm_http):
    crm_http((PIPEDRIVE_ME, 401, {"error": "unauthorized"}))
    result = verify_pipedrive("nope")
    assert result.ok is False
    assert "Pipedrive" in result.error


@pytest.mark.parametrize("verify", [verify_hubspot, verify_pipedrive])
def test_verify_reports_unreachable_provider(monkeypatch, verify):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(providers, "_TRANSPORT", httpx.MockTransport(boom))
    result = verify("token")
    assert result.ok is False
    assert "couldn’t reach" in result.error


def test_verify_never_echoes_the_token(crm_http):
    crm_http((CONTACTS, 401, {"message": "invalid token"}))
    secret = "pat-na1-supersecret"
    result = verify_hubspot(secret)
    assert secret not in (result.error or "")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_integration_providers.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'app.integrations'`

- [ ] **Step 4: Create the package init**

Create `backend/app/integrations/__init__.py`:

```python
"""CRM connection layer: which providers exist and how to prove a token works."""
```

- [ ] **Step 5: Write the registry and verify functions**

Create `backend/app/integrations/providers.py`:

```python
"""Which CRMs Open Gong can connect to, and how to prove a token works.

See docs/superpowers/specs/2026-08-14-integrations-design.md §1, §2.

Verification is deliberately a real API call the sync feature will also need
(HubSpot: read a contact) rather than a generic whoami — that way a token
missing the scope fails here, at connect time, instead of at first sync.
"""

from collections.abc import Callable
from dataclasses import dataclass

import httpx

# Tests inject an httpx.MockTransport here; None means a real network client.
_TRANSPORT: httpx.BaseTransport | None = None
_TIMEOUT = 15

HUBSPOT_API = "https://api.hubapi.com"
PIPEDRIVE_API = "https://api.pipedrive.com/v1"


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    account_label: str | None = None
    account_ref: str | None = None
    error: str | None = None


VerifyFn = Callable[[str], VerifyResult]


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    blurb: str
    available: bool
    token_label: str | None = None
    docs_url: str | None = None
    # A tuple default is immutable, so it needs no dataclasses.field wrapper.
    setup_steps: tuple[str, ...] = ()
    verify: VerifyFn | None = None


def _client() -> httpx.Client:
    return httpx.Client(transport=_TRANSPORT, timeout=_TIMEOUT)


def _rejection(resp: httpx.Response, label: str) -> str:
    """A message that helps without ever quoting the token back."""
    if resp.status_code in (401, 403):
        return (
            f"{label} rejected that token ({resp.status_code}). Check it was copied "
            f"in full and has read access to contacts."
        )
    return f"{label} returned HTTP {resp.status_code}."


def verify_hubspot(token: str) -> VerifyResult:
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with _client() as client:
            resp = client.get(
                f"{HUBSPOT_API}/crm/v3/objects/contacts",
                params={"limit": 1},
                headers=headers,
            )
            if resp.status_code >= 300:
                return VerifyResult(ok=False, error=_rejection(resp, "HubSpot"))
            # Best-effort label. HubSpot's account-info endpoint needs its own
            # scope and only exposes a portal id, so a private app without that
            # scope still connects — just without a friendly name.
            label = ref = None
            info = client.get(f"{HUBSPOT_API}/account-info/v3/details", headers=headers)
            if info.status_code < 300:
                portal = info.json().get("portalId")
                if portal:
                    ref = str(portal)
                    label = f"Portal {ref}"
            return VerifyResult(ok=True, account_label=label, account_ref=ref)
    except httpx.HTTPError as e:
        return VerifyResult(ok=False, error=f"couldn’t reach HubSpot: {e}")


def verify_pipedrive(token: str) -> VerifyResult:
    try:
        with _client() as client:
            resp = client.get(f"{PIPEDRIVE_API}/users/me", headers={"x-api-token": token})
    except httpx.HTTPError as e:
        return VerifyResult(ok=False, error=f"couldn’t reach Pipedrive: {e}")
    if resp.status_code >= 300:
        return VerifyResult(ok=False, error=_rejection(resp, "Pipedrive"))
    data = resp.json().get("data") or {}
    return VerifyResult(
        ok=True,
        account_label=data.get("company_name"),
        # Pipedrive's API is per-company-subdomain; sync will need this.
        account_ref=data.get("company_domain"),
    )


PROVIDERS: tuple[Provider, ...] = (
    Provider(
        key="hubspot",
        label="HubSpot",
        blurb="Log call notes and insights against your HubSpot contacts.",
        available=True,
        token_label="Private app access token",
        docs_url="https://app.hubspot.com/private-apps",
        setup_steps=(
            "In HubSpot, open Settings → Integrations → Private Apps.",
            "Create a private app (or open an existing one) and grant it the "
            "crm.objects.contacts.read scope.",
            "Copy the access token from the app’s Auth tab and paste it below.",
        ),
        verify=verify_hubspot,
    ),
    Provider(
        key="pipedrive",
        label="Pipedrive",
        blurb="Log call notes and insights against your Pipedrive people.",
        available=True,
        token_label="Personal API token",
        docs_url="https://app.pipedrive.com/settings/api",
        setup_steps=(
            "In Pipedrive, open Personal preferences → API.",
            "Copy your personal API token and paste it below.",
        ),
        verify=verify_pipedrive,
    ),
    Provider(
        key="salesforce",
        label="Salesforce",
        blurb="Needs an OAuth connected app — not available yet.",
        available=False,
    ),
)

BY_KEY: dict[str, Provider] = {p.key: p for p in PROVIDERS}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_integration_providers.py -v`
Expected: 9 passed (the parametrized unreachable test counts as 2)

- [ ] **Step 7: Commit**

```bash
git add backend/app/integrations backend/tests/conftest.py backend/tests/test_integration_providers.py
git commit -m "feat: add the CRM provider registry and token verification"
```

---

### Task 2: The Integration model and the catalog endpoint

Ships the read side: the UI can render all three providers and their state.

**Files:**
- Modify: `backend/app/models.py` (append a new class after `EntryRule`)
- Create: `backend/app/api/integrations.py`
- Modify: `backend/app/main.py` (import at line ~18-24, `include_router` at line ~72-78)
- Test: `backend/tests/test_api_integrations.py`

**Interfaces:**
- Consumes: `PROVIDERS`, `BY_KEY` from Task 1.
- Produces:
  - `Integration` model — `provider` (pk), `access_token`, `account_label`, `account_ref`, `status`, `last_error`, `connected_at`, `last_verified_at`.
  - `router` in `app/api/integrations.py` at prefix `/api/integrations`.
  - `_serialize(provider: Provider, row: Integration | None) -> dict` and `_hint(token: str) -> str`, used by Tasks 3 and 4.
  - `GET /api/integrations` → JSON list, one object per provider, in registry order.

No migration step exists in this project: `Base.metadata.create_all(engine)` runs in the `lifespan` hook (`app/main.py:32`) and in the `isolated_db` test fixture, so defining the model is sufficient.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_api_integrations.py`:

```python
"""CRM connection management API (integrations design §4)."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_catalog_lists_every_provider_unconnected():
    resp = client.get("/api/integrations")
    assert resp.status_code == 200
    items = resp.json()
    assert [i["key"] for i in items] == ["hubspot", "pipedrive", "salesforce"]

    hubspot = items[0]
    assert hubspot["label"] == "HubSpot"
    assert hubspot["available"] is True
    assert hubspot["connected"] is False
    assert hubspot["status"] is None
    assert hubspot["token_hint"] is None
    # The setup steps are the point of the card — they must reach the client.
    assert len(hubspot["setup_steps"]) == 3
    assert hubspot["docs_url"].startswith("https://")

    assert items[2]["available"] is False
    assert items[2]["setup_steps"] == []


def test_catalog_never_exposes_a_stored_token():
    from app.db import get_session
    from app.models import Integration

    with get_session() as session:
        session.add(Integration(provider="hubspot", access_token="pat-na1-supersecret"))
        session.commit()

    resp = client.get("/api/integrations")
    assert "supersecret" not in resp.text
    assert "access_token" not in resp.text
    hubspot = resp.json()[0]
    assert hubspot["connected"] is True
    assert hubspot["token_hint"] == "••••cret"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_api_integrations.py -v`
Expected: FAIL — both tests 404, because the router does not exist yet

- [ ] **Step 3: Add the model**

Append to `backend/app/models.py` (after the `EntryRule` class; `String`, `Text`, `DateTime`, `Mapped`, `mapped_column`, and `_now` are already imported/defined in that file):

```python
class Integration(Base):
    __tablename__ = "integrations"

    # One connection per provider, so every write is naturally idempotent.
    provider: Mapped[str] = mapped_column(String, primary_key=True)  # hubspot | pipedrive
    # Plaintext by design: the SQLite file is local and gitignored, and a key
    # stored beside its own ciphertext defends against nothing. Never returned
    # by the API, never logged — see the integrations design doc §6.
    access_token: Mapped[str] = mapped_column(String)
    account_label: Mapped[str | None] = mapped_column(String, nullable=True)
    # HubSpot portal id / Pipedrive company_domain — the sync feature needs it.
    account_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="connected")  # connected | error
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
```

- [ ] **Step 4: Write the router with the catalog endpoint**

Create `backend/app/api/integrations.py`:

```python
"""CRM connection management. See
docs/superpowers/specs/2026-08-14-integrations-design.md §4.

The access token never leaves this module: responses carry only a four-character
hint. The database is the sole source of truth — no environment variables.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..db import get_session
from ..integrations.providers import PROVIDERS, Provider
from ..models import Integration

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hint(token: str) -> str:
    """Enough to recognise which token is installed, useless to an attacker."""
    return f"••••{token[-4:]}" if len(token) >= 4 else "••••"


def _serialize(p: Provider, row: Integration | None) -> dict:
    return {
        "key": p.key,
        "label": p.label,
        "blurb": p.blurb,
        "available": p.available,
        "token_label": p.token_label,
        "docs_url": p.docs_url,
        "setup_steps": list(p.setup_steps),
        # An absent row *is* the not-connected state; there is no
        # "disconnected" status value to get out of sync with reality.
        "connected": row is not None,
        "status": row.status if row else None,
        "account_label": row.account_label if row else None,
        "token_hint": _hint(row.access_token) if row else None,
        "last_error": row.last_error if row else None,
        "connected_at": row.connected_at.isoformat() if row else None,
        "last_verified_at": row.last_verified_at.isoformat() if row else None,
    }


@router.get("")
def list_integrations() -> list[dict]:
    with get_session() as session:
        rows = {r.provider: r for r in session.scalars(select(Integration)).all()}
        return [_serialize(p, rows.get(p.key)) for p in PROVIDERS]
```

- [ ] **Step 5: Mount the router**

In `backend/app/main.py`, add the import alongside the other router imports (keeping alphabetical order, after `from .api.ingest import router as ingest_router`):

```python
from .api.integrations import router as integrations_router
```

And after `app.include_router(ingest_router)`:

```python
app.include_router(integrations_router)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_api_integrations.py -v`
Expected: 2 passed

- [ ] **Step 7: Run the full suite to confirm nothing regressed**

Run: `cd backend && uv run pytest`
Expected: all tests pass (the new model adds a table; no existing behavior changes)

- [ ] **Step 8: Commit**

```bash
git add backend/app/models.py backend/app/api/integrations.py backend/app/main.py backend/tests/test_api_integrations.py
git commit -m "feat: serve the CRM integration catalog"
```

---

### Task 3: Connect and disconnect

The heart of the feature: a token is persisted only after it authenticates.

**Files:**
- Modify: `backend/app/api/integrations.py` (append endpoints)
- Test: `backend/tests/test_api_integrations.py` (append tests)

**Interfaces:**
- Consumes: `_serialize`, `_hint`, `_now` from Task 2; `BY_KEY` and each `Provider.verify` from Task 1; the `crm_http` fixture from Task 1.
- Produces:
  - `ConnectBody` pydantic model with a single `token: str` field.
  - `_connectable(key: str) -> Provider` — raises `HTTPException(404)` for an unknown key, `HTTPException(400)` for a provider that cannot be connected. Reused by Task 4.
  - `PUT /api/integrations/{provider}` → the same object shape as the catalog.
  - `DELETE /api/integrations/{provider}` → `{"ok": True}`.

- [ ] **Step 1: Write the failing tests**

Add `import httpx` to the imports at the top of `backend/tests/test_api_integrations.py`, then append:

```python
CONTACTS = "crm/v3/objects/contacts"
ACCOUNT = "account-info/v3/details"


def _rows() -> list:
    from sqlalchemy import select

    from app.db import get_session
    from app.models import Integration

    with get_session() as session:
        return list(session.scalars(select(Integration)).all())


def test_connect_stores_a_verified_token(crm_http):
    crm_http((CONTACTS, 200, {"results": []}), (ACCOUNT, 200, {"portalId": 42}))

    resp = client.put("/api/integrations/hubspot", json={"token": "pat-na1-goodtoken"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["status"] == "connected"
    assert body["account_label"] == "Portal 42"
    assert body["token_hint"] == "••••oken"
    assert body["last_error"] is None
    assert "goodtoken" not in resp.text

    rows = _rows()
    assert len(rows) == 1
    assert rows[0].access_token == "pat-na1-goodtoken"
    assert rows[0].account_ref == "42"


def test_connect_with_a_rejected_token_persists_nothing(crm_http):
    crm_http((CONTACTS, 401, {"message": "invalid"}))

    resp = client.put("/api/integrations/hubspot", json={"token": "bad"})
    assert resp.status_code == 400
    assert "rejected that token" in resp.json()["detail"]
    assert _rows() == []


def test_connect_rejects_a_blank_token_without_calling_the_provider(monkeypatch):
    import app.integrations.providers as providers

    def explode(request):
        raise AssertionError("a blank token must never reach the provider")

    monkeypatch.setattr(providers, "_TRANSPORT", httpx.MockTransport(explode))

    resp = client.put("/api/integrations/hubspot", json={"token": "   "})
    assert resp.status_code == 400
    assert "required" in resp.json()["detail"]
    assert _rows() == []


def test_reconnecting_replaces_the_token_in_place(crm_http):
    crm_http((CONTACTS, 200, {"results": []}), (ACCOUNT, 200, {"portalId": 42}))
    client.put("/api/integrations/hubspot", json={"token": "pat-na1-first"})
    client.put("/api/integrations/hubspot", json={"token": "pat-na1-second"})

    rows = _rows()
    assert len(rows) == 1
    assert rows[0].access_token == "pat-na1-second"


def test_connect_clears_a_previous_error(crm_http):
    from app.db import get_session
    from app.models import Integration

    with get_session() as session:
        session.add(Integration(provider="pipedrive", access_token="stale", status="error",
                                last_error="Pipedrive rejected that token (401)."))
        session.commit()

    crm_http(("users/me", 200, {"data": {"company_name": "Acme Inc", "company_domain": "acme"}}))
    body = client.put("/api/integrations/pipedrive", json={"token": "fresh-token"}).json()
    assert body["status"] == "connected"
    assert body["last_error"] is None
    assert body["account_label"] == "Acme Inc"


def test_connect_to_an_unavailable_provider_is_refused():
    resp = client.put("/api/integrations/salesforce", json={"token": "anything"})
    assert resp.status_code == 400
    assert "Salesforce" in resp.json()["detail"]
    assert _rows() == []


def test_unknown_provider_is_404():
    assert client.put("/api/integrations/zoho", json={"token": "x"}).status_code == 404
    assert client.delete("/api/integrations/zoho").status_code == 404


def test_disconnect_removes_the_connection(crm_http):
    crm_http((CONTACTS, 200, {"results": []}), (ACCOUNT, 200, {"portalId": 42}))
    client.put("/api/integrations/hubspot", json={"token": "pat-na1-goodtoken"})

    assert client.delete("/api/integrations/hubspot").status_code == 200
    assert _rows() == []
    assert client.get("/api/integrations").json()[0]["connected"] is False
    # Idempotent: disconnecting an absent connection is not an error.
    assert client.delete("/api/integrations/hubspot").status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_api_integrations.py -v`
Expected: the 2 catalog tests pass; the 8 new ones fail with 405 Method Not Allowed

- [ ] **Step 3: Implement connect and disconnect**

Update the imports at the top of `backend/app/api/integrations.py` — add `BaseModel`, and add `BY_KEY` to the providers import (Task 2 deliberately left it out to avoid an unused import):

```python
from pydantic import BaseModel

from ..integrations.providers import BY_KEY, PROVIDERS, Provider
```

Append to `backend/app/api/integrations.py`:

```python
class ConnectBody(BaseModel):
    token: str


def _known(key: str) -> Provider:
    p = BY_KEY.get(key)
    if p is None:
        raise HTTPException(404, "unknown integration")
    return p


def _connectable(key: str) -> Provider:
    p = _known(key)
    if not p.available or p.verify is None:
        raise HTTPException(400, f"{p.label} can’t be connected yet")
    return p


@router.put("/{provider}")
def connect(provider: str, body: ConnectBody) -> dict:
    """Verify first, persist second — a stored connection is a working one."""
    p = _connectable(provider)
    token = body.token.strip()
    if not token:
        raise HTTPException(400, "a token is required")

    result = p.verify(token)
    if not result.ok:
        raise HTTPException(400, result.error or f"{p.label} rejected that token")

    with get_session() as session:
        row = session.get(Integration, provider)
        if row is None:
            row = Integration(provider=provider)
            session.add(row)
        row.access_token = token
        row.account_label = result.account_label
        row.account_ref = result.account_ref
        row.status = "connected"
        row.last_error = None
        row.connected_at = _now()
        row.last_verified_at = _now()
        session.commit()
        return _serialize(p, row)


@router.delete("/{provider}")
def disconnect(provider: str) -> dict:
    _known(provider)
    with get_session() as session:
        row = session.get(Integration, provider)
        if row is not None:
            session.delete(row)
            session.commit()
    return {"ok": True}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_api_integrations.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/integrations.py backend/tests/test_api_integrations.py
git commit -m "feat: connect and disconnect a CRM from the API"
```

---

### Task 4: Re-test a stored connection

A token can be revoked in the CRM long after it was connected. This endpoint makes that visible on demand instead of at first sync.

**Files:**
- Modify: `backend/app/api/integrations.py` (append one endpoint)
- Test: `backend/tests/test_api_integrations.py` (append tests)

**Interfaces:**
- Consumes: `_connectable`, `_serialize`, `_now` from Tasks 2-3.
- Produces: `POST /api/integrations/{provider}/test` → the same object shape as the catalog.

Note the deliberate contract: a **failed** verification still returns `200`. The failure is state carried on the returned object (`status: "error"`, `last_error`), not a transport error — the card re-renders from the response body, and a 4xx would make "the check ran and found a problem" indistinguishable from "the check could not run".

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_api_integrations.py`:

```python
def test_test_marks_a_revoked_token_as_error_but_keeps_the_row(crm_http):
    crm_http((CONTACTS, 200, {"results": []}), (ACCOUNT, 200, {"portalId": 42}))
    client.put("/api/integrations/hubspot", json={"token": "pat-na1-goodtoken"})

    # The token has since been revoked in HubSpot.
    crm_http((CONTACTS, 401, {"message": "invalid"}))
    resp = client.post("/api/integrations/hubspot/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["status"] == "error"
    assert "rejected that token" in body["last_error"]
    # The row survives so the user can see what broke and re-paste a token.
    assert len(_rows()) == 1


def test_test_clears_the_error_once_the_token_works_again(crm_http):
    from app.db import get_session
    from app.models import Integration

    with get_session() as session:
        session.add(Integration(provider="pipedrive", access_token="token", status="error",
                                last_error="Pipedrive rejected that token (401)."))
        session.commit()

    crm_http(("users/me", 200, {"data": {"company_name": "Acme Inc", "company_domain": "acme"}}))
    body = client.post("/api/integrations/pipedrive/test").json()
    assert body["status"] == "connected"
    assert body["last_error"] is None
    assert body["account_label"] == "Acme Inc"


def test_test_on_a_provider_that_is_not_connected_is_404():
    resp = client.post("/api/integrations/hubspot/test")
    assert resp.status_code == 404
    assert "isn’t connected" in resp.json()["detail"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_api_integrations.py -v`
Expected: the 3 new tests fail with 404 (no such route)

- [ ] **Step 3: Implement the test endpoint**

Append to `backend/app/api/integrations.py`:

```python
@router.post("/{provider}/test")
def test_connection(provider: str) -> dict:
    """Re-verify the stored token. A failed check is 200 + status=error, not a
    4xx: the card re-renders from this body, and "the check found a problem"
    must stay distinguishable from "the check could not run"."""
    p = _connectable(provider)
    with get_session() as session:
        row = session.get(Integration, provider)
        if row is None:
            raise HTTPException(404, f"{p.label} isn’t connected")

        result = p.verify(row.access_token)
        row.last_verified_at = _now()
        if result.ok:
            row.status = "connected"
            row.last_error = None
            row.account_label = result.account_label or row.account_label
            row.account_ref = result.account_ref or row.account_ref
        else:
            row.status = "error"
            row.last_error = result.error
        session.commit()
        return _serialize(p, row)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_api_integrations.py -v`
Expected: 13 passed

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && uv run pytest`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/integrations.py backend/tests/test_api_integrations.py
git commit -m "feat: re-verify a stored CRM connection on demand"
```

---

### Task 5: Frontend API client

**Files:**
- Modify: `web/lib/api.ts` (append at the end of the file)

**Interfaces:**
- Consumes: the four endpoints from Tasks 2-4.
- Produces: the `Integration` type plus `listIntegrations()`, `connectIntegration(key, token)`, `testIntegration(key)`, `disconnectIntegration(key)`. Tasks 6 and 7 import these from `@/lib/api`.

`web/` has no test runner, so verification here is the TypeScript compiler.

- [ ] **Step 1: Append the types and client functions**

Append to `web/lib/api.ts` (the module-local `j<T>` helper and `API_BASE` are already defined at the top of the file):

```ts
// A provider's catalog entry merged with its connection state. `connected`
// false means no row exists — there is no "disconnected" status value.
export type Integration = {
  key: string;
  label: string;
  blurb: string;
  available: boolean;
  token_label: string | null;
  docs_url: string | null;
  setup_steps: string[];
  connected: boolean;
  status: "connected" | "error" | null;
  account_label: string | null;
  token_hint: string | null;
  last_error: string | null;
  connected_at: string | null;
  last_verified_at: string | null;
};

export const listIntegrations = () =>
  fetch(`${API_BASE}/api/integrations`, { cache: "no-store" }).then(j<Integration[]>);

export const connectIntegration = (key: string, token: string) =>
  fetch(`${API_BASE}/api/integrations/${key}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  }).then(j<Integration>);

export const testIntegration = (key: string) =>
  fetch(`${API_BASE}/api/integrations/${key}/test`, { method: "POST" }).then(j<Integration>);

export const disconnectIntegration = (key: string) =>
  fetch(`${API_BASE}/api/integrations/${key}`, { method: "DELETE" }).then(j<{ ok: boolean }>);
```

- [ ] **Step 2: Verify it compiles**

Run: `cd web && npx tsc --noEmit`
Expected: no output (success)

- [ ] **Step 3: Commit**

```bash
git add web/lib/api.ts
git commit -m "feat: add the integrations API client"
```

---

### Task 6: The integration card component

Every state a connection can be in, plus the inline connect panel. Built before the page so the page has something real to render.

**Files:**
- Create: `web/components/IntegrationCard.tsx`

**Interfaces:**
- Consumes: `Integration`, `connectIntegration`, `testIntegration`, `disconnectIntegration` from Task 5; `tonePill` from `@/lib/status`.
- Produces: default-exported `IntegrationCard` with props
  `{ integration: Integration; now: number; onChange: (next: Integration) => void; onRemoved: () => void }`.
  Task 7 renders it.

Why `now` is a prop: `Date.now()` in a render body trips the `react-hooks/purity` lint rule and fails the build. The page owns the timestamp (see `web/app/page.tsx:39`) and passes it down.

- [ ] **Step 1: Write the component**

Create `web/components/IntegrationCard.tsx`:

```tsx
"use client";

import { useState, type FormEvent } from "react";
import {
  connectIntegration,
  disconnectIntegration,
  testIntegration,
  type Integration,
} from "@/lib/api";
import { tonePill, type UiStatus } from "@/lib/status";

// FastAPI sends errors as {"detail": "..."} and the api helper rethrows the raw
// body — show the sentence a human wrote, not the JSON wrapper.
function detailOf(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e);
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.detail === "string") return parsed.detail;
  } catch {
    /* not JSON — fall through to the raw message */
  }
  return raw || "Something went wrong.";
}

function agoLabel(iso: string | null, now: number): string {
  if (!iso) return "";
  const mins = Math.round((now - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

// A tinted letter tile instead of a logo file: no network fetch, no asset to
// license, and it can't break the layout.
const TILE: Record<string, string> = {
  hubspot: "bg-orange-50 text-orange-600",
  pipedrive: "bg-emerald-50 text-emerald-700",
  salesforce: "bg-sky-50 text-sky-700",
};

type Props = {
  integration: Integration;
  now: number;
  onChange: (next: Integration) => void;
  onRemoved: () => void;
};

export default function IntegrationCard({ integration: it, now, onChange, onRemoved }: Props) {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [reveal, setReveal] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState<"connect" | "test" | "disconnect" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pill: { text: string; tone: UiStatus["tone"] } = !it.available
    ? { text: "Coming soon", tone: "neutral" }
    : !it.connected
      ? { text: "Not connected", tone: "neutral" }
      : it.status === "error"
        ? { text: "Needs attention", tone: "amber" }
        : { text: "Connected", tone: "green" };

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy("connect");
    setError(null);
    try {
      const next = await connectIntegration(it.key, token);
      setToken("");
      setReveal(false);
      setOpen(false);
      onChange(next);
    } catch (err) {
      setError(detailOf(err));
    } finally {
      setBusy(null);
    }
  }

  async function runTest() {
    setBusy("test");
    setError(null);
    try {
      onChange(await testIntegration(it.key));
    } catch (err) {
      setError(detailOf(err));
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    setBusy("disconnect");
    setError(null);
    try {
      await disconnectIntegration(it.key);
      setConfirming(false);
      onRemoved();
    } catch (err) {
      setError(detailOf(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section
      className={`card ${open ? "sm:col-span-2" : ""} ${
        it.available ? "" : "border-dashed bg-neutral-50/60"
      }`}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-sm font-semibold ${
            TILE[it.key] ?? "bg-neutral-100 text-neutral-600"
          }`}
        >
          {it.label[0]}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h2 className={`font-medium ${it.available ? "" : "text-neutral-500"}`}>{it.label}</h2>
            <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${tonePill[pill.tone]}`}>
              {pill.text}
            </span>
          </div>
          <p className="mt-1 text-xs text-neutral-500">{it.blurb}</p>

          {it.connected && (
            <p className="mt-2 text-xs text-neutral-400">
              {it.account_label ? `${it.account_label} · ` : ""}
              {it.token_hint}
              {it.last_verified_at ? ` · verified ${agoLabel(it.last_verified_at, now)}` : ""}
            </p>
          )}

          {it.status === "error" && it.last_error && (
            <p className="mt-2 rounded-lg bg-amber-50 px-2 py-1.5 text-xs text-amber-900">
              {it.last_error}
            </p>
          )}
          {error && (
            <p className="mt-2 rounded-lg bg-red-50 px-2 py-1.5 text-xs text-red-700">{error}</p>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {it.available && !it.connected && !open && (
              <button onClick={() => setOpen(true)} className="btn btn-primary">
                Connect
              </button>
            )}
            {it.connected && !confirming && (
              <>
                <button onClick={runTest} disabled={busy !== null} className="btn">
                  {busy === "test" ? "Testing…" : "Test connection"}
                </button>
                <button onClick={() => setOpen((v) => !v)} disabled={busy !== null} className="btn">
                  Replace token
                </button>
                <button
                  onClick={() => setConfirming(true)}
                  disabled={busy !== null}
                  className="text-xs text-neutral-500 hover:text-red-600"
                >
                  Disconnect
                </button>
              </>
            )}
            {confirming && (
              <>
                <span className="text-xs text-neutral-600">
                  Disconnect {it.label}? You’ll need the token again to reconnect.
                </span>
                <button onClick={remove} disabled={busy !== null} className="btn btn-warn">
                  {busy === "disconnect" ? "Disconnecting…" : "Yes, disconnect"}
                </button>
                <button onClick={() => setConfirming(false)} className="btn">
                  Cancel
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {open && it.available && (
        <form onSubmit={submit} className="mt-4 border-t border-neutral-100 pt-4">
          <ol className="space-y-1 text-xs text-neutral-600">
            {it.setup_steps.map((step, i) => (
              <li key={i}>
                <span className="mr-1 font-medium text-neutral-400">{i + 1}.</span>
                {step}
              </li>
            ))}
          </ol>
          {it.docs_url && (
            <a
              href={it.docs_url}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-block text-xs font-medium text-blue-700 hover:underline"
            >
              Open {it.label} settings ↗
            </a>
          )}
          <label className="eyebrow mt-4 block" htmlFor={`token-${it.key}`}>
            {it.token_label}
          </label>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <input
              id={`token-${it.key}`}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              type={reveal ? "text" : "password"}
              autoComplete="off"
              spellCheck={false}
              placeholder={`Paste your ${it.label} token`}
              className="edit-field min-w-64 flex-1"
            />
            <button
              type="button"
              onClick={() => setReveal((v) => !v)}
              className="text-xs text-neutral-500 hover:text-neutral-800"
            >
              {reveal ? "Hide" : "Show"}
            </button>
            <button type="submit" disabled={busy !== null || !token.trim()} className="btn btn-primary">
              {busy === "connect" ? "Verifying…" : "Connect"}
            </button>
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setError(null);
                setToken("");
              }}
              className="btn"
            >
              Cancel
            </button>
          </div>
          <p className="mt-2 text-xs text-neutral-400">
            Open Gong checks the token with {it.label} before saving it, and never shows it again.
          </p>
        </form>
      )}
    </section>
  );
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cd web && npx tsc --noEmit`
Expected: no output (success)

- [ ] **Step 3: Commit**

```bash
git add web/components/IntegrationCard.tsx
git commit -m "feat: add the integration card"
```

---

### Task 7: The Integrations page and nav item

**Files:**
- Create: `web/app/integrations/page.tsx`
- Modify: `web/components/Nav.tsx:6-10` (one line added to `ITEMS`)

**Interfaces:**
- Consumes: `listIntegrations` and `Integration` from Task 5; `IntegrationCard` from Task 6.
- Produces: the `/integrations` route.

The nav item is added in this task, not earlier, so the link never points at a route that does not exist.

- [ ] **Step 1: Write the page**

Create `web/app/integrations/page.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import IntegrationCard from "@/components/IntegrationCard";
import { listIntegrations, type Integration } from "@/lib/api";

export default function IntegrationsPage() {
  const [items, setItems] = useState<Integration[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Lazy initializer keeps Date.now() out of the render body (react-hooks/purity).
  // Minute-granularity "verified 2h ago" doesn't need to tick live.
  const [now] = useState(() => Date.now());

  const refresh = useCallback(
    () =>
      listIntegrations()
        .then((rows) => {
          setItems(rows);
          setLoadError(null);
        })
        .catch(() =>
          setLoadError("Couldn’t reach the Open Gong API. Is the backend running on port 8000?"),
        ),
    [],
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <h1 className="text-lg font-semibold">Integrations</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Connect the CRM your team already works in. Every token is checked against the provider
        before it’s saved, so a connection that appears here is one that works.
      </p>

      {loadError && (
        <div className="mt-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {loadError}
        </div>
      )}

      {items === null && !loadError && <p className="mt-6 text-sm text-neutral-400">Loading…</p>}

      <div className="mt-6 grid items-start gap-4 sm:grid-cols-2">
        {items?.map((it) => (
          <IntegrationCard
            key={it.key}
            integration={it}
            now={now}
            onChange={(next) =>
              setItems((prev) => prev?.map((p) => (p.key === next.key ? next : p)) ?? null)
            }
            onRemoved={refresh}
          />
        ))}
      </div>
    </main>
  );
}
```

- [ ] **Step 2: Add the nav item**

In `web/components/Nav.tsx`, add one entry to the end of `ITEMS`:

```tsx
const ITEMS = [
  { href: "/", label: "Calls" },
  { href: "/agents", label: "Agents" },
  { href: "/skills", label: "Skills" },
  { href: "/integrations", label: "Integrations" },
];
```

- [ ] **Step 3: Verify the build and lint pass**

Run: `cd web && npm run build && npm run lint`
Expected: build succeeds with `/integrations` in the route list; lint reports no errors

- [ ] **Step 4: Commit**

```bash
git add web/app/integrations/page.tsx web/components/Nav.tsx
git commit -m "feat: add the Integrations page and nav item"
```

---

### Task 8: End-to-end verification

The bad-token path can be verified against the **real** HubSpot and Pipedrive APIs with no credentials at all — a garbage token genuinely returns 401. That exercises the whole stack: form, HTTP, live provider, error surfacing.

**Files:** none — this task changes no code. If it finds a defect, fix it with a test that reproduces it first.

- [ ] **Step 1: Start the app**

Run: `make demo` (from the repo root of this worktree)
Expected: backend on :8000, web on :3000

- [ ] **Step 2: Verify the nav and the empty state**

Open `http://localhost:3000/integrations`. Confirm:
- "Integrations" appears in the sidebar and is highlighted as active
- Three cards render: HubSpot and Pipedrive with a **Connect** button, Salesforce with a dashed border, "Coming soon", and **no button**
- Nothing on the page reports a connection

- [ ] **Step 3: Verify the real rejection path**

Click **Connect** on HubSpot. Confirm the numbered steps and the "Open HubSpot settings ↗" link appear, and that the card widens to full width. Paste `pat-na1-definitely-not-a-real-token` and submit. Confirm:
- The button reads "Verifying…" while in flight
- A red inline message appears saying HubSpot rejected the token (401) — **and it stays on screen**
- The card still shows "Not connected"
- The token you typed does not appear anywhere in the message

Repeat with Pipedrive.

- [ ] **Step 4: Verify nothing was persisted**

Run: `curl -s http://localhost:8000/api/integrations | python3 -m json.tool | grep -E '"key"|"connected"'`
Expected: `connected: false` for all three providers — a rejected token writes no row

- [ ] **Step 5: Verify the token reveal toggle and keyboard submit**

In the connect panel, type any text, click **Show** — the text becomes visible; click **Hide** — it masks again. With the field focused, press Enter and confirm the form submits (rather than doing nothing).

- [ ] **Step 6: Verify the happy path (needs a real token)**

This step requires a HubSpot private-app token or a Pipedrive personal API token. Paste a real one and confirm:
- The card flips to "Connected" with a green pill
- The account label, `••••` hint, and "verified just now" appear
- **Test connection** succeeds and refreshes the timestamp
- **Disconnect** asks for confirmation inline, and confirming returns the card to "Not connected"

If no real token is available, stop here and report that this step is unverified rather than claiming the happy path works.

- [ ] **Step 7: Verify the full backend suite is green**

Run: `cd backend && uv run pytest`
Expected: all tests pass

- [ ] **Step 8: Commit any fixes**

If steps 2-7 surfaced defects, each fix gets its own reproducing test and its own commit. If nothing needed fixing, there is nothing to commit here.

---

## Plan self-review notes

Spec coverage check, section by section:

| spec section | task |
|---|---|
| §1 Provider registry | Task 1 |
| §2 Verify before persist | Tasks 1, 3 |
| §3 Data model | Task 2 |
| §4 API surface (GET/PUT/DELETE/test) | Tasks 2, 3, 4 |
| §5 Frontend (nav, page, card, api client) | Tasks 5, 6, 7 |
| §6 Security posture (no token returned/logged, masked hint, password input) | Task 2 (`_hint`, `_serialize`), Task 1 (`test_verify_never_echoes_the_token`), Task 3 (`assert "goodtoken" not in resp.text`), Task 6 (`type="password"`) |
| §7 Error handling (all seven rows) | blank token → Task 3; 401/403 → Tasks 1, 3; unreachable → Task 1; missing scope → Task 1 (treated as rejection by `_rejection`); stale token → Task 4; unavailable provider → Task 3; unknown key → Task 3 |
| §8 Testing approach (6 listed cases) | Tasks 1-4 cover all six; frontend verified by build/lint/manual per Task 8 |

**One deviation from the spec, recorded deliberately:** §2 gives "Acme Inc" as the HubSpot account-label example. HubSpot's `/account-info/v3/details` does not return a company name — only a `portalId` — so the implementation uses `Portal 12345678`. Pipedrive does return `company_name`, so "Acme Inc" is accurate there. §5's "inline-SVG monogram tile" is implemented as a CSS-tinted letter tile, which needs no SVG at all.
