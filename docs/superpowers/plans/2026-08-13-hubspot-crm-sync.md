# HubSpot CRM Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After every call finishes processing, automatically write its insights into HubSpot — a Note (always) on the matched Contact, plus a small fixed set of Properties — with inline call-outs so a human can see what needs attention whenever they look, in HubSpot itself.

**Architecture:** A new `crm_sync` pipeline stage (non-critical, last in the chain) matches the call to a HubSpot Contact by phone/email, then writes via a narrow `CrmAdapter` interface (HubSpot is the only implementation). A new `CrmSync` table is the audit trail and idempotency key — one row per `(call, run)`, checkpointed field-by-field so a retry never reposts an already-written Note.

**Tech Stack:** Python/FastAPI backend (existing), SQLAlchemy models, `httpx` for HubSpot REST calls (no SDK), `phonenumbers` for E.164 phone normalization (new dependency), Next.js frontend (existing).

**Spec:** `docs/superpowers/specs/2026-08-13-hubspot-crm-sync-design.md` — read it before starting; this plan implements it section-by-section and cross-references it below.

## Global Constraints

- No approval gate in this feature — both the Note and the Properties write directly, no human confirmation step (spec Context).
- No OAuth — a single HubSpot Private App token via `HUBSPOT_ACCESS_TOKEN` in `backend/.env`, matching the existing `PYAI_API_KEY`/`ANTHROPIC_API_KEY` pattern (spec §1).
- "Confident match" = exactly one HubSpot Contact for the normalized phone or the email — zero or more-than-one both count as no match (spec §2).
- No dynamic per-pack HubSpot properties. Properties are a small, fixed, pack-agnostic set of exactly 4 fields; everything pack-specific stays in the Note only (spec §3).
- Properties are upserted (overwritten) every sync; Notes are never re-posted for the same `(call, run)` — idempotency lives on the `CrmSync` row, not on stage retry state (spec §4/§5).
- `crm_sync` is a non-critical pipeline stage: it is never added to `CRITICAL_STAGES`; its failure marks the run `partial`, never `failed` (spec §5/§7).
- If `HUBSPOT_ACCESS_TOKEN` is unset, `crm_sync` no-ops immediately — `status=ok`, zero HubSpot calls, no `CrmSync` row created (spec §5/§7).
- Contact-only sync. No Deal object writes, no new-Contact creation (spec Non-goals).
- One real CRM implementation (HubSpot) behind the `CrmAdapter` protocol; no `mock.py`/`real.py` split like the PyAI adapter — tests stub `httpx` directly (spec §6/§8).

---

### Task 1: Data model — caller identity + CrmSync audit table

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/api/ingest.py`
- Test: `backend/tests/test_models_crm.py` (create)
- Test: `backend/tests/test_ingest.py` (extend)

**Interfaces:**
- Consumes: nothing new — `Call`, `Run`, `Base`, `_uuid`, `_now` already exist in `app/models.py`.
- Produces:
  - `Call.caller_phone: str | None`, `Call.caller_email: str | None` (new columns)
  - `CrmSync` model: `id, call_id, run_id, status, hubspot_contact_id, hubspot_note_id, properties_written: dict | None, error: str | None, attempts: int, created_at, updated_at`
  - `_create_call(session, *, title, source, external_id, audio_path, caller_phone=None, caller_email=None) -> tuple[Call, bool]` (extended signature)

The current codebase has no way to associate a phone number or email with a `Call` at all — every later task depends on this existing, since matching is impossible without it.

- [ ] **Step 1: Write the failing tests for the model**

Create `backend/tests/test_models_crm.py`:

```python
"""CrmSync model + Call caller-identity fields (Task 1 of the HubSpot sync plan).
See docs/superpowers/specs/2026-08-13-hubspot-crm-sync-design.md §4.
"""

from app.db import get_session
from app.models import Call, CrmSync, Run


def _seed_call_and_run():
    with get_session() as session:
        call = Call(
            title="t", source="upload", external_id="crm-model-test-1",
            caller_phone="+15551234567", caller_email="a@b.com",
        )
        session.add(call)
        session.flush()
        run = Run(call_id=call.id, status="running", stages=[])
        session.add(run)
        session.commit()
        return call.id, run.id


def test_call_stores_caller_phone_and_email():
    call_id, _ = _seed_call_and_run()
    with get_session() as session:
        call = session.get(Call, call_id)
        assert call.caller_phone == "+15551234567"
        assert call.caller_email == "a@b.com"


def test_call_caller_fields_default_to_none():
    with get_session() as session:
        call = Call(title="t2", source="upload", external_id="crm-model-test-2")
        session.add(call)
        session.commit()
        assert call.caller_phone is None
        assert call.caller_email is None


def test_crm_sync_round_trips():
    call_id, run_id = _seed_call_and_run()
    with get_session() as session:
        sync = CrmSync(call_id=call_id, run_id=run_id, status="unmatched", error="no_phone_or_email")
        session.add(sync)
        session.commit()
        sync_id = sync.id

    with get_session() as session:
        row = session.get(CrmSync, sync_id)
        assert row.status == "unmatched"
        assert row.error == "no_phone_or_email"
        assert row.hubspot_contact_id is None
        assert row.hubspot_note_id is None
        assert row.properties_written is None
        assert row.attempts == 0
        assert row.created_at is not None
        assert row.updated_at is not None


def test_crm_sync_default_status_is_unmatched():
    call_id, run_id = _seed_call_and_run()
    with get_session() as session:
        sync = CrmSync(call_id=call_id, run_id=run_id)
        session.add(sync)
        session.commit()
        assert sync.status == "unmatched"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_models_crm.py -v`
Expected: FAIL — `ImportError: cannot import name 'CrmSync' from 'app.models'` (and `Call(...)` rejecting the unexpected `caller_phone` kwarg once the import is fixed).

- [ ] **Step 3: Add the fields and the new model**

In `backend/app/models.py`, add `caller_phone`/`caller_email` to `Call` (insert after the `audio_path` line, inside the `Call` class):

```python
    audio_path: Mapped[str | None] = mapped_column(String, nullable=True)
    caller_phone: Mapped[str | None] = mapped_column(String, nullable=True)  # for CRM contact matching
    caller_email: Mapped[str | None] = mapped_column(String, nullable=True)
```

Add a new `CrmSync` class after `ShareLink` (end of the file):

```python
class CrmSync(Base):
    __tablename__ = "crm_syncs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    # unmatched | auto_written | pending_review | approved | rejected | failed
    # only unmatched/auto_written/failed are reachable today; the rest are
    # reserved for the future approval gate — see
    # docs/superpowers/specs/2026-08-13-hubspot-crm-sync-design.md §4
    status: Mapped[str] = mapped_column(String, default="unmatched")
    hubspot_contact_id: Mapped[str | None] = mapped_column(String, nullable=True)
    hubspot_note_id: Mapped[str | None] = mapped_column(String, nullable=True)
    properties_written: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
```

No new imports needed — `JSON`, `Integer`, `Text`, `DateTime`, `ForeignKey`, `String`, `Mapped`, `mapped_column` are already imported at the top of `models.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_models_crm.py -v`
Expected: PASS (4 tests). Note: since there's no migration system (SQLite dev DB is recreated from `Base.metadata.create_all` on each fresh run — see `app/main.py`'s lifespan and `scripts/seed.py`), you may need to delete `backend/data/opengong.sqlite3` if a stale schema causes a `no such column` error: `rm -f backend/data/opengong.sqlite3`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/tests/test_models_crm.py
git commit -m "feat(crm): add Call caller identity fields and CrmSync audit table"
```

- [ ] **Step 6: Write the failing tests for ingestion accepting caller identity**

Add to `backend/tests/test_ingest.py` (append at the end of the file):

```python
def test_upload_stores_caller_phone_and_email():
    with TestClient(app) as c:
        resp = c.post(
            "/api/ingest/upload",
            files={"file": ("call.wav", WAV_HEADER, "audio/wav")},
            data={"caller_phone": "+15551234567", "caller_email": "rep@example.com"},
        )
        assert resp.status_code == 200
        call_id = resp.json()["call_id"]

    from app.db import get_session
    from app.models import Call
    with get_session() as session:
        call = session.get(Call, call_id)
        assert call.caller_phone == "+15551234567"
        assert call.caller_email == "rep@example.com"


def test_upload_without_caller_fields_still_works():
    with TestClient(app) as c:
        resp = c.post("/api/ingest/upload", files={"file": ("call2.wav", WAV_HEADER, "audio/wav")})
        assert resp.status_code == 200
        call_id = resp.json()["call_id"]

    from app.db import get_session
    from app.models import Call
    with get_session() as session:
        call = session.get(Call, call_id)
        assert call.caller_phone is None
        assert call.caller_email is None
```

- [ ] **Step 7: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_ingest.py -v -k caller`
Expected: FAIL on `test_upload_stores_caller_phone_and_email` — `call.caller_phone` is `None` even though `"+15551234567"` was posted (the endpoint currently discards it silently; FastAPI ignores unknown form fields rather than erroring).

- [ ] **Step 8: Implement — accept the fields in `ingest.py`**

In `backend/app/api/ingest.py`, change the import line to add `Form`:

```python
from fastapi import APIRouter, Form, HTTPException, UploadFile
```

Change `_create_call`'s signature and body:

```python
def _create_call(
    session, *, title: str, source: str, external_id: str, audio_path: str,
    caller_phone: str | None = None, caller_email: str | None = None,
) -> tuple[Call, bool]:
    existing = session.scalars(select(Call).where(Call.external_id == external_id)).first()
    if existing:
        return existing, False
    call = Call(
        title=title, source=source, external_id=external_id, audio_path=audio_path,
        caller_phone=caller_phone, caller_email=caller_email,
    )
    session.add(call)
    session.flush()
    session.add(Run(call_id=call.id, status="running", stages=_fresh_stages()))
    return call, True
```

Change the `upload` endpoint signature and its call to `_create_call`:

```python
@router.post("/upload")
async def upload(
    file: UploadFile,
    caller_phone: str | None = Form(None),
    caller_email: str | None = Form(None),
) -> dict:
    suffix = Path(file.filename or "audio.wav").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(422, f"unsupported audio type {suffix!r}")
    content = await file.read()
    if len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "file too large")

    digest = hashlib.sha256(content).hexdigest()
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    audio_path = UPLOADS_DIR / f"{digest[:16]}{suffix}"
    audio_path.write_bytes(content)

    with get_session() as session:
        call, created = _create_call(
            session,
            title=Path(file.filename or "Uploaded call").stem,
            source="upload",
            external_id=f"sha256:{digest}",
            audio_path=str(audio_path),
            caller_phone=caller_phone,
            caller_email=caller_email,
        )
        session.commit()
        call_id = call.id
    if created:
        enqueue("process_call", {"call_id": call_id})
    return {"call_id": call_id, "created": created}
```

Also extend `UrlIngest` and `ingest_url` the same way, for parity (the `/url` path can carry caller identity too):

```python
class UrlIngest(BaseModel):
    url: HttpUrl
    caller_phone: str | None = None
    caller_email: str | None = None


@router.post("/url")
def ingest_url(body: UrlIngest) -> dict:
    url = str(body.url)
    try:
        with httpx.Client(follow_redirects=True, timeout=60) as client:
            resp = client.get(url)
            resp.raise_for_status()
            content = resp.content
    except httpx.HTTPError as e:
        raise HTTPException(422, f"could not fetch recording: {e}") from e
    if len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "file too large")

    suffix = Path(httpx.URL(url).path).suffix.lower() or ".mp3"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    audio_path = UPLOADS_DIR / f"{digest[:16]}{suffix}"
    audio_path.write_bytes(content)

    with get_session() as session:
        call, created = _create_call(
            session,
            title=Path(httpx.URL(url).path).stem or "Linked call",
            source="url",
            external_id=f"url:{url}",
            audio_path=str(audio_path),
            caller_phone=body.caller_phone,
            caller_email=body.caller_email,
        )
        session.commit()
        call_id = call.id
    if created:
        enqueue("process_call", {"call_id": call_id})
    return {"call_id": call_id, "created": created}
```

- [ ] **Step 9: Run to verify they pass**

Run: `cd backend && uv run pytest tests/test_ingest.py -v`
Expected: PASS (all tests in the file, including the two new ones).

- [ ] **Step 10: Run the full backend suite to check for regressions**

Run: `cd backend && uv run pytest -q`
Expected: PASS, no regressions (existing test count plus the 6 new tests from this task).

- [ ] **Step 11: Commit**

```bash
git add backend/app/api/ingest.py backend/tests/test_ingest.py
git commit -m "feat(crm): accept optional caller phone/email at ingestion"
```

---

### Task 2: Connection & HubSpot adapter

**Files:**
- Create: `backend/app/adapters/crm/__init__.py`
- Create: `backend/app/adapters/crm/base.py`
- Create: `backend/app/adapters/crm/hubspot.py`
- Modify: `backend/app/setup_env.py`
- Modify: `backend/.env.example`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/test_crm_adapter.py` (create)
- Test: `backend/tests/test_setup.py` (extend)

**Interfaces:**
- Consumes: nothing from Task 1 — this task is independent and can be built in parallel with it.
- Produces:
  - `ContactMatch(contact_id: str | None, reason: str)` — `reason` is one of `"matched" | "no_phone_or_email" | "no_match" | "ambiguous"`
  - `CrmAdapter` protocol: `find_contact(*, phone, email) -> ContactMatch`, `write_note(contact_id, html_body) -> str`, `upsert_properties(contact_id, properties: dict) -> None`
  - `is_crm_configured() -> bool`
  - `get_crm_adapter() -> HubSpotAdapter` (importable from `app.adapters.crm.base`; Task 4 will monkeypatch this name as imported into `app.pipeline`)
  - `normalize_phone(raw: str | None, default_region: str = "US") -> str | None`
  - `HubSpotAdapter(token: str | None = None)` — raises `CrmError` if no token resolves
  - `setup_env.status()` gains a `"crm"` key: `{"configured": bool, "detail": str}`

- [ ] **Step 1: Add the `phonenumbers` dependency**

Run: `cd backend && uv add phonenumbers`
Expected: `pyproject.toml` gains `phonenumbers>=...` under `dependencies`, `uv.lock` updates.

- [ ] **Step 2: Write the failing tests for phone normalization and the adapter shape**

Create `backend/tests/test_crm_adapter.py`:

```python
"""HubSpot adapter (Task 2 of the HubSpot sync plan): phone normalization,
contact matching (confident/ambiguous/no-match), Note write + association,
property upsert with lazy property creation. Stubs httpx directly — no live
HubSpot spike per spec §8 (their REST API is stable/well-documented).
See docs/superpowers/specs/2026-08-13-hubspot-crm-sync-design.md §2/§6.
"""

import httpx
import pytest

import app.adapters.crm.base as crm_base
import app.adapters.crm.hubspot as hs
from app.adapters.crm.hubspot import CrmError, HubSpotAdapter, normalize_phone


def test_normalize_phone_variants():
    assert normalize_phone("(555) 123-4567") == "+15551234567"
    assert normalize_phone("+15551234567") == "+15551234567"
    assert normalize_phone("555-123-4567") == "+15551234567"


def test_normalize_phone_invalid_returns_none():
    assert normalize_phone("not a phone") is None
    assert normalize_phone("123") is None
    assert normalize_phone(None) is None


def test_missing_token_raises():
    with pytest.raises(CrmError):
        HubSpotAdapter(token=None)


def _adapter(monkeypatch, token="test-token"):
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", token)
    return HubSpotAdapter()


def test_find_contact_confident_phone_match(monkeypatch):
    def fake_post(url, **kw):
        assert url.endswith("/crm/v3/objects/contacts/search")
        return httpx.Response(200, json={"results": [{"id": "42"}]})
    monkeypatch.setattr(hs.httpx, "post", fake_post)

    adapter = _adapter(monkeypatch)
    match = adapter.find_contact(phone="555-123-4567", email=None)
    assert match.contact_id == "42"
    assert match.reason == "matched"


def test_find_contact_no_phone_results_falls_back_to_email(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append(kw["json"])
        if len(calls) == 1:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"results": [{"id": "99"}]})
    monkeypatch.setattr(hs.httpx, "post", fake_post)

    adapter = _adapter(monkeypatch)
    match = adapter.find_contact(phone="555-123-4567", email="a@b.com")
    assert match.contact_id == "99"
    assert match.reason == "matched"
    assert len(calls) == 2  # phone search, then email search


def test_find_contact_ambiguous_phone_no_email_is_ambiguous(monkeypatch):
    def fake_post(url, **kw):
        return httpx.Response(200, json={"results": [{"id": "1"}, {"id": "2"}]})
    monkeypatch.setattr(hs.httpx, "post", fake_post)

    adapter = _adapter(monkeypatch)
    match = adapter.find_contact(phone="555-123-4567", email=None)
    assert match.contact_id is None
    assert match.reason == "ambiguous"


def test_find_contact_neither_phone_nor_email(monkeypatch):
    adapter = _adapter(monkeypatch)
    match = adapter.find_contact(phone=None, email=None)
    assert match.contact_id is None
    assert match.reason == "no_phone_or_email"


def test_find_contact_unparseable_phone_and_no_email_is_no_match(monkeypatch):
    def fake_post(url, **kw):
        return httpx.Response(200, json={"results": []})
    monkeypatch.setattr(hs.httpx, "post", fake_post)
    adapter = _adapter(monkeypatch)
    # "not a phone" fails normalize_phone, so this should behave like no phone
    # was given at all and still try nothing (no email either) -> no_match,
    # since phone WAS provided (just unusable) but email was not.
    match = adapter.find_contact(phone="not a phone", email=None)
    assert match.contact_id is None
    assert match.reason == "no_match"


def test_write_note_creates_and_associates(monkeypatch):
    posted = {}

    def fake_post(url, **kw):
        posted["url"] = url
        posted["json"] = kw["json"]
        return httpx.Response(201, json={"id": "note-1"})

    put_calls = []

    def fake_put(url, **kw):
        put_calls.append(url)
        return httpx.Response(200, json={})

    monkeypatch.setattr(hs.httpx, "post", fake_post)
    monkeypatch.setattr(hs.httpx, "put", fake_put)

    adapter = _adapter(monkeypatch)
    note_id = adapter.write_note("42", "<p>hello</p>")
    assert note_id == "note-1"
    assert posted["json"]["properties"]["hs_note_body"] == "<p>hello</p>"
    assert put_calls == [f"{hs.BASE}/crm/v4/objects/notes/note-1/associations/default/contacts/42"]


def test_upsert_properties_ensures_then_patches(monkeypatch):
    get_calls = []
    post_calls = []
    patch_calls = []

    def fake_get(url, **kw):
        get_calls.append(url)
        return httpx.Response(404)  # none exist yet

    def fake_post(url, **kw):
        post_calls.append(kw["json"]["name"])
        return httpx.Response(201, json={})

    def fake_patch(url, **kw):
        patch_calls.append((url, kw["json"]))
        return httpx.Response(200, json={})

    monkeypatch.setattr(hs.httpx, "get", fake_get)
    monkeypatch.setattr(hs.httpx, "post", fake_post)
    monkeypatch.setattr(hs.httpx, "patch", fake_patch)

    adapter = _adapter(monkeypatch)
    adapter.upsert_properties("42", {"open_gong_score": 4})

    assert len(get_calls) == len(hs.PROPERTY_DEFS)
    assert set(post_calls) == {name for name, _, _ in hs.PROPERTY_DEFS}
    assert patch_calls == [(f"{hs.BASE}/crm/v3/objects/contacts/42", {"open_gong_score": 4})]


def test_upsert_properties_skips_creation_when_already_exists(monkeypatch):
    def fake_get(url, **kw):
        return httpx.Response(200, json={"name": "x"})  # already exists

    post_calls = []

    def fake_post(url, **kw):
        post_calls.append(url)
        return httpx.Response(201, json={})

    def fake_patch(url, **kw):
        return httpx.Response(200, json={})

    monkeypatch.setattr(hs.httpx, "get", fake_get)
    monkeypatch.setattr(hs.httpx, "post", fake_post)
    monkeypatch.setattr(hs.httpx, "patch", fake_patch)

    adapter = _adapter(monkeypatch)
    adapter.upsert_properties("42", {"open_gong_score": 4})
    assert post_calls == []  # nothing created, all already existed


def test_is_crm_configured(monkeypatch):
    monkeypatch.delenv("HUBSPOT_ACCESS_TOKEN", raising=False)
    assert crm_base.is_crm_configured() is False
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "x")
    assert crm_base.is_crm_configured() is True


def test_get_crm_adapter_returns_hubspot_adapter(monkeypatch):
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "x")
    adapter = crm_base.get_crm_adapter()
    assert isinstance(adapter, HubSpotAdapter)
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_crm_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.adapters.crm'`.

- [ ] **Step 4: Implement `app/adapters/crm/__init__.py`**

```python
```

(empty file, matching `app/adapters/__init__.py` and `app/adapters/pyai/__init__.py`)

- [ ] **Step 5: Implement `app/adapters/crm/base.py`**

```python
"""The CRM adapter seam. HubSpot is the only implementation today, but the
interface is narrow enough that a second CRM later doesn't touch this one.
See docs/superpowers/specs/2026-08-13-hubspot-crm-sync-design.md §6.
"""

import os
from dataclasses import dataclass
from typing import Protocol


@dataclass
class ContactMatch:
    contact_id: str | None
    reason: str  # "matched" | "no_phone_or_email" | "no_match" | "ambiguous"


class CrmAdapter(Protocol):
    def find_contact(self, *, phone: str | None, email: str | None) -> ContactMatch: ...
    def write_note(self, contact_id: str, html_body: str) -> str: ...
    def upsert_properties(self, contact_id: str, properties: dict) -> None: ...


def is_crm_configured() -> bool:
    return bool(os.environ.get("HUBSPOT_ACCESS_TOKEN"))


def get_crm_adapter() -> "CrmAdapter":
    from .hubspot import HubSpotAdapter

    return HubSpotAdapter()
```

- [ ] **Step 6: Implement `app/adapters/crm/hubspot.py`**

```python
"""HubSpot connector: contact matching by phone/email, Note writes, and a
small fixed set of upserted Properties. Plain REST calls (httpx) — no
HubSpot SDK dependency, matching the rest of Open Gong's adapters.
See docs/superpowers/specs/2026-08-13-hubspot-crm-sync-design.md §2/§3/§6.
"""

import os
from datetime import datetime, timezone

import httpx
import phonenumbers

from .base import ContactMatch

BASE = "https://api.hubapi.com"

# Contact properties Open Gong writes to. Created on first use if missing.
# (name, HubSpot property type, HubSpot fieldType)
PROPERTY_DEFS = [
    ("open_gong_deal_size", "string", "text"),
    ("open_gong_score", "number", "number"),
    ("open_gong_flags", "string", "textarea"),
    ("open_gong_last_synced_at", "string", "text"),
]


class CrmError(Exception):
    pass


def normalize_phone(raw: str | None, default_region: str = "US") -> str | None:
    """Best-effort E.164 normalization. Returns None if unparseable/invalid."""
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


class HubSpotAdapter:
    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("HUBSPOT_ACCESS_TOKEN")
        if not self.token:
            raise CrmError("HUBSPOT_ACCESS_TOKEN not set")
        self._properties_ensured = False

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _search(self, filter_groups: list[dict]) -> tuple[str | None, int]:
        resp = httpx.post(
            f"{BASE}/crm/v3/objects/contacts/search",
            headers=self._headers(),
            json={"filterGroups": filter_groups, "limit": 10},
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if len(results) == 1:
            return results[0]["id"], 1
        return None, len(results)

    def find_contact(self, *, phone: str | None, email: str | None) -> ContactMatch:
        normalized = normalize_phone(phone)
        if normalized:
            contact_id, n = self._search([
                {"filters": [{"propertyName": "phone", "operator": "EQ", "value": normalized}]},
                {"filters": [{"propertyName": "mobilephone", "operator": "EQ", "value": normalized}]},
            ])
            if contact_id:
                return ContactMatch(contact_id, "matched")
            if n > 1 and not email:
                return ContactMatch(None, "ambiguous")
        if email:
            contact_id, n = self._search(
                [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}]
            )
            if contact_id:
                return ContactMatch(contact_id, "matched")
            if n > 1:
                return ContactMatch(None, "ambiguous")
        if not phone and not email:
            return ContactMatch(None, "no_phone_or_email")
        return ContactMatch(None, "no_match")

    def write_note(self, contact_id: str, html_body: str) -> str:
        resp = httpx.post(
            f"{BASE}/crm/v3/objects/notes",
            headers=self._headers(),
            json={
                "properties": {
                    "hs_note_body": html_body,
                    "hs_timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                }
            },
            timeout=30,
        )
        resp.raise_for_status()
        note_id = resp.json()["id"]

        assoc = httpx.put(
            f"{BASE}/crm/v4/objects/notes/{note_id}/associations/default/contacts/{contact_id}",
            headers=self._headers(),
            timeout=30,
        )
        assoc.raise_for_status()
        return note_id

    def upsert_properties(self, contact_id: str, properties: dict) -> None:
        self._ensure_properties()
        resp = httpx.patch(
            f"{BASE}/crm/v3/objects/contacts/{contact_id}",
            headers=self._headers(),
            json={"properties": properties},
            timeout=30,
        )
        resp.raise_for_status()

    def _ensure_properties(self) -> None:
        """Create Open Gong's custom contact properties if missing. Cheap to
        call every sync — one GET per property, only POSTs on first use."""
        if self._properties_ensured:
            return
        for name, type_, field_type in PROPERTY_DEFS:
            check = httpx.get(
                f"{BASE}/crm/v3/properties/contacts/{name}", headers=self._headers(), timeout=30
            )
            if check.status_code == 404:
                create = httpx.post(
                    f"{BASE}/crm/v3/properties/contacts",
                    headers=self._headers(),
                    json={
                        "name": name,
                        "label": name.replace("_", " ").title(),
                        "type": type_,
                        "fieldType": field_type,
                        "groupName": "contactinformation",
                    },
                    timeout=30,
                )
                create.raise_for_status()
        self._properties_ensured = True
```

- [ ] **Step 7: Run to verify they pass**

Run: `cd backend && uv run pytest tests/test_crm_adapter.py -v`
Expected: PASS (14 tests).

- [ ] **Step 8: Commit**

```bash
git add backend/app/adapters/crm backend/tests/test_crm_adapter.py backend/pyproject.toml backend/uv.lock
git commit -m "feat(crm): HubSpot connector — contact matching, Note write, property upsert"
```

- [ ] **Step 9: Write the failing test for `setup_env.status()`'s new `crm` section**

Add to `backend/tests/test_setup.py` (append at the end of the file):

```python
def test_status_includes_crm_configured(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "LLM_PROVIDER=anthropic\nANTHROPIC_API_KEY=sk-x\nPYAI_ADAPTER=mock\n"
        "HUBSPOT_ACCESS_TOKEN=pat-123\n"
    )
    monkeypatch.setattr(se, "ENV_FILE", env)
    for k in ("PYAI_API_KEY", "PYAI_ADAPTER", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "HUBSPOT_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    s = se.status()
    assert s["crm"]["configured"] is True


def test_status_crm_disabled_when_not_configured(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=anthropic\nANTHROPIC_API_KEY=sk-x\nPYAI_ADAPTER=mock\n")
    monkeypatch.setattr(se, "ENV_FILE", env)
    for k in ("PYAI_API_KEY", "PYAI_ADAPTER", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "HUBSPOT_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    s = se.status()
    assert s["crm"]["configured"] is False
    assert s["can_process_uploads"] is True  # CRM being off never blocks upload processing
```

- [ ] **Step 10: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_setup.py -v -k crm`
Expected: FAIL — `KeyError: 'crm'`.

- [ ] **Step 11: Implement — extend `setup_env.py`**

In `backend/app/setup_env.py`, extend the `KEYS` list:

```python
KEYS = [
    "LLM_PROVIDER", "LLM_MODEL", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
    "PYAI_API_KEY", "PYAI_ADAPTER", "HUBSPOT_ACCESS_TOKEN",
]
```

Extend `status()`'s return dict (add the `"crm"` key before `"can_process_uploads"`):

```python
def status() -> dict:
    """Readiness snapshot for `doctor` and the UI banner."""
    env = read_env()
    llm_ok, llm_msg = _llm_ok(env)
    adapter = env.get("PYAI_ADAPTER", "mock")
    pyai_key = bool(env.get("PYAI_API_KEY"))
    hubspot_configured = bool(env.get("HUBSPOT_ACCESS_TOKEN"))
    return {
        "llm": {"ok": llm_ok, "detail": llm_msg, "provider": env.get("LLM_PROVIDER", "anthropic")},
        "transcription": {
            "adapter": adapter,
            "ok": adapter == "mock" or pyai_key,
            "detail": "offline mock (samples only)" if adapter == "mock"
            else ("real PyAI" if pyai_key else "PYAI_ADAPTER=real but PYAI_API_KEY missing"),
        },
        "crm": {
            "configured": hubspot_configured,
            "detail": "HubSpot sync active" if hubspot_configured
            else "HUBSPOT_ACCESS_TOKEN not set — CRM sync disabled (optional)",
        },
        # can the user process a NEW upload (not just browse samples)?
        "can_process_uploads": llm_ok and (adapter == "mock" or pyai_key),
    }
```

- [ ] **Step 12: Run to verify it passes**

Run: `cd backend && uv run pytest tests/test_setup.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 13: Update `.env.example`**

In `backend/.env.example`, append:

```
# HubSpot Private App access token (optional) — enables automatic Note +
# Property sync to a matched Contact after each call. Leave empty to skip;
# CRM sync is fully opt-in and never blocks call processing.
HUBSPOT_ACCESS_TOKEN=
```

- [ ] **Step 14: Run the full backend suite to check for regressions**

Run: `cd backend && uv run pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 15: Commit**

```bash
git add backend/app/setup_env.py backend/tests/test_setup.py backend/.env.example
git commit -m "feat(crm): surface HubSpot connection status in doctor/status"
```

---

### Task 3: Note & Properties content builder

**Files:**
- Create: `backend/app/crm_sync.py`
- Test: `backend/tests/test_crm_content.py` (create)

**Interfaces:**
- Consumes: nothing new — operates on the existing `insights` dict shape (`summary`, `objections`, `next_steps`, `scorecard: {pack, fields: [{name, kind, value?, score?, max_score?, justification?, evidence}]}`, `follow_up_email`, `dropped_claims: [{where, reason}]`) and the existing `compliance` dict shape (`{verdict, findings: [{rule, severity, detail, evidence}]}` or `None`). Both shapes already exist in `app/insights.py`/`app/compliance.py` — no changes to those files.
- Produces:
  - `build_callouts(insights: dict, compliance: dict | None) -> list[str]`
  - `build_note_html(insights: dict, compliance: dict | None) -> str`
  - `build_properties(insights: dict, compliance: dict | None, synced_at: str) -> dict` — always returns exactly the 4 keys `open_gong_deal_size`, `open_gong_score`, `open_gong_flags`, `open_gong_last_synced_at`

This task is independent of Tasks 1 and 2 — it's pure functions over plain dicts, testable with zero DB/network.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_crm_content.py`:

```python
"""Note/Properties content builder (Task 3 of the HubSpot sync plan):
call-out inclusion (dropped claims, low scores, compliance findings), HTML
escaping, pack-agnostic property mapping.
See docs/superpowers/specs/2026-08-13-hubspot-crm-sync-design.md §3.
"""

from app.crm_sync import build_callouts, build_note_html, build_properties

INSIGHTS = {
    "summary": [{"text": "Renewal at risk.", "evidence": []}],
    "next_steps": [{"text": "Send pricing", "owner": "Ana", "evidence": []}],
    "dropped_claims": [{"where": "objections[1]", "reason": "no evidence"}],
    "scorecard": {
        "pack": "sales-default",
        "fields": [
            {
                "name": "budget_discussed", "kind": "deterministic", "value": True,
                "evidence": [{"quote": "we budgeted fifteen thousand a year", "line": 5}],
            },
            {
                "name": "discovery_quality", "kind": "judgment", "score": 1, "max_score": 5,
                "justification": "Barely asked questions.",
            },
            {
                "name": "objection_handling", "kind": "judgment", "score": 4, "max_score": 5,
                "justification": "Handled well.",
            },
        ],
    },
    "follow_up_email": {"subject": "Next steps", "body": "Hi <there>,\nThanks."},
}

COMPLIANCE_WARN = {
    "verdict": "WARN",
    "findings": [
        {
            "rule": "missing_recording_disclosure", "severity": "high",
            "detail": "No disclosure was made.", "evidence": [],
        }
    ],
}


def test_callouts_include_dropped_claims_low_scores_and_compliance():
    callouts = build_callouts(INSIGHTS, COMPLIANCE_WARN)
    assert any("objections[1]" in c for c in callouts)
    assert any("discovery quality" in c for c in callouts)
    assert not any("objection handling" in c for c in callouts)  # 4/5 is not low
    assert any("missing recording disclosure" in c for c in callouts)


def test_callouts_empty_for_clean_call():
    clean = {
        **INSIGHTS,
        "dropped_claims": [],
        "scorecard": {
            "pack": "x",
            "fields": [
                {"name": "discovery_quality", "kind": "judgment", "score": 5, "max_score": 5, "justification": "great"},
            ],
        },
    }
    assert build_callouts(clean, None) == []


def test_note_html_escapes_and_includes_callout_banner():
    html = build_note_html(INSIGHTS, COMPLIANCE_WARN)
    assert "<h3>" in html
    assert "Needs review" in html
    assert "Renewal at risk." in html
    assert "&lt;there&gt;" in html  # follow-up email body escaped, not raw-injected
    assert "<script>" not in html


def test_note_html_omits_callout_banner_when_clean():
    clean = {**INSIGHTS, "dropped_claims": [], "scorecard": {"pack": "x", "fields": []}}
    html = build_note_html(clean, None)
    assert "Needs review" not in html


def test_properties_picks_first_judgment_score_and_budget_quote():
    props = build_properties(INSIGHTS, COMPLIANCE_WARN, "2026-08-13T00:00:00+00:00")
    assert props["open_gong_score"] == 1  # first judgment field in the pack, not the highest
    assert props["open_gong_deal_size"] == "we budgeted fifteen thousand a year"
    assert "missing recording disclosure" in props["open_gong_flags"]
    assert props["open_gong_last_synced_at"] == "2026-08-13T00:00:00+00:00"


def test_properties_empty_deal_size_and_zero_score_when_pack_has_neither():
    no_budget = {
        **INSIGHTS,
        "scorecard": {
            "pack": "support-default",
            "fields": [
                {"name": "issue_identified", "kind": "deterministic", "value": True, "evidence": []},
            ],
        },
    }
    props = build_properties(no_budget, None, "2026-08-13T00:00:00+00:00")
    assert props["open_gong_deal_size"] == ""
    assert props["open_gong_score"] == 0
    assert props["open_gong_flags"] == ""
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_crm_content.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.crm_sync'`.

- [ ] **Step 3: Implement `app/crm_sync.py`**

```python
"""Builds what gets written to HubSpot: a Note (HTML, always includes
inline call-outs) and a small fixed set of Properties (pack-agnostic).
See docs/superpowers/specs/2026-08-13-hubspot-crm-sync-design.md §3.

Deal size is approximated from the `budget_discussed` field's own evidence
quote when the active pack has that exact field (the built-in sales pack
does; support/custom packs generally won't) — see spec Open Question #2.
"""

# Judgment scores at or below this get a call-out. Hardcoded for v1 —
# spec Open Question #1 flags per-pack configurability as a later follow-up.
LOW_SCORE_THRESHOLD = 2


def build_callouts(insights: dict, compliance: dict | None) -> list[str]:
    """Short strings describing what needs human attention. Feeds both the
    Note's inline warnings and the open_gong_flags property."""
    callouts = []
    for d in insights.get("dropped_claims", []):
        callouts.append(f"Unverified claim dropped ({d['where']}): {d['reason']}")
    for f in (insights.get("scorecard") or {}).get("fields", []):
        if f["kind"] == "judgment" and (f.get("score") or 0) <= LOW_SCORE_THRESHOLD:
            callouts.append(f"Low score — {f['name'].replace('_', ' ')}: {f.get('score')}/{f.get('max_score')}")
    if compliance and compliance.get("findings"):
        for finding in compliance["findings"]:
            callouts.append(f"Compliance: {finding['rule'].replace('_', ' ')} — {finding['detail']}")
    return callouts


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_note_html(insights: dict, compliance: dict | None) -> str:
    callouts = build_callouts(insights, compliance)
    parts = ["<h3>Open Gong call summary</h3>"]

    if callouts:
        parts.append("<p><strong>⚠️ Needs review:</strong></p><ul>")
        parts.extend(f"<li>{_escape(c)}</li>" for c in callouts)
        parts.append("</ul>")

    if insights.get("summary"):
        parts.append("<p><strong>Summary</strong></p><ul>")
        parts.extend(f"<li>{_escape(s['text'])}</li>" for s in insights["summary"])
        parts.append("</ul>")

    if insights.get("next_steps"):
        parts.append("<p><strong>Next steps</strong></p><ul>")
        for n in insights["next_steps"]:
            owner = f" — {_escape(n['owner'])}" if n.get("owner") else ""
            parts.append(f"<li>{_escape(n['text'])}{owner}</li>")
        parts.append("</ul>")

    sc = insights.get("scorecard") or {}
    if sc.get("fields"):
        parts.append(f"<p><strong>Scorecard</strong> ({_escape(sc.get('pack', ''))})</p><ul>")
        for f in sc["fields"]:
            label = f["name"].replace("_", " ")
            if f["kind"] == "deterministic":
                val = "yes" if f.get("value") else "no" if f.get("value") is False else "unknown"
                parts.append(f"<li>{_escape(label)}: {val}</li>")
            else:
                parts.append(
                    f"<li>{_escape(label)}: {f.get('score')}/{f.get('max_score')} "
                    f"— {_escape(f.get('justification', ''))}</li>"
                )
        parts.append("</ul>")

    email = insights.get("follow_up_email")
    if email:
        parts.append("<p><strong>Drafted follow-up email</strong></p>")
        parts.append(f"<p><em>{_escape(email['subject'])}</em></p>")
        parts.append(f"<p>{_escape(email['body']).replace(chr(10), '<br>')}</p>")

    return "".join(parts)


def _primary_judgment_score(insights: dict) -> int:
    for f in (insights.get("scorecard") or {}).get("fields", []):
        if f["kind"] == "judgment":
            return f.get("score") or 0
    return 0


def _budget_quote(insights: dict) -> str:
    for f in (insights.get("scorecard") or {}).get("fields", []):
        if f["name"] == "budget_discussed" and f.get("value") and f.get("evidence"):
            return f["evidence"][0]["quote"]
    return ""


def build_properties(insights: dict, compliance: dict | None, synced_at: str) -> dict:
    callouts = build_callouts(insights, compliance)
    return {
        "open_gong_deal_size": _budget_quote(insights),
        "open_gong_score": _primary_judgment_score(insights),
        "open_gong_flags": "; ".join(callouts) if callouts else "",
        "open_gong_last_synced_at": synced_at,
    }
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && uv run pytest tests/test_crm_content.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `cd backend && uv run pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/app/crm_sync.py backend/tests/test_crm_content.py
git commit -m "feat(crm): Note/Properties content builder with call-outs"
```

---

### Task 4: Pipeline wiring — the `crm_sync` stage

**Files:**
- Modify: `backend/app/run_state.py`
- Modify: `backend/app/pipeline.py`
- Test: `backend/tests/test_crm_sync_pipeline.py` (create)

**Interfaces:**
- Consumes:
  - From Task 1: `Call.caller_phone`, `Call.caller_email`, `CrmSync` model
  - From Task 2: `get_crm_adapter()`, `is_crm_configured()`, `ContactMatch(contact_id, reason)`
  - From Task 3: `build_note_html(insights, compliance) -> str`, `build_properties(insights, compliance, synced_at) -> dict`
- Produces: `sync_call_to_crm(call_id: str, run_id: str, lines: list[dict], insights: dict, compliance: dict | None) -> None` (raises on any adapter failure — caught by `rs.execute`'s retry loop, same as every other stage). `STAGES` now includes `"crm_sync"` as the last entry.

This task depends on Tasks 1, 2, and 3 all being complete — it's the integration point.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_crm_sync_pipeline.py`:

```python
"""crm_sync pipeline stage (Task 4 of the HubSpot sync plan): configured vs.
not, matched vs. unmatched, write failure + capped retries, idempotent
re-run (no duplicate Notes on partial-success retry).
See docs/superpowers/specs/2026-08-13-hubspot-crm-sync-design.md §5/§7.
"""

from sqlalchemy import select

import app.pipeline as pipeline_mod
from app.adapters.crm.base import ContactMatch
from app.db import get_session
from app.models import Call, CrmSync, Run, Transcript
from app.pipeline import run_insights
from app.run_state import STAGES

LINES = [
    {"line": 1, "speaker": "Ana", "text": "Hi, this call is recorded."},
    {"line": 2, "speaker": "Bob", "text": "Sure, go ahead."},
]


def _seed_call(caller_phone=None, caller_email=None) -> tuple[str, str]:
    stages = [
        {
            "name": s, "status": "ok" if s == "transcribe" else "pending",
            "attempts": 1 if s == "transcribe" else 0, "cost_usd": 0.0, "error": None,
        }
        for s in STAGES
    ]
    with get_session() as session:
        call = Call(
            title="t", source="upload", external_id=f"crm-pipe-{caller_phone}-{caller_email}",
            audio_path="/tmp/x.wav", caller_phone=caller_phone, caller_email=caller_email,
        )
        session.add(call)
        session.flush()
        session.add(Transcript(call_id=call.id, lines=LINES))
        run = Run(call_id=call.id, status="running", stages=stages)
        session.add(run)
        session.commit()
        return call.id, run.id


class FakeAdapter:
    def __init__(self, match=ContactMatch("contact-1", "matched"), note_id="note-1",
                 fail_note=False, fail_properties=False):
        self.match = match
        self.note_id = note_id
        self.fail_note = fail_note
        self.fail_properties = fail_properties
        self.note_calls = 0
        self.property_calls = 0

    def find_contact(self, *, phone, email):
        return self.match

    def write_note(self, contact_id, html_body):
        self.note_calls += 1
        if self.fail_note:
            raise RuntimeError("hubspot note API down")
        return self.note_id

    def upsert_properties(self, contact_id, properties):
        self.property_calls += 1
        if self.fail_properties:
            raise RuntimeError("hubspot properties API down")


def _crm_sync_row(call_id, run_id):
    with get_session() as session:
        return session.scalars(
            select(CrmSync).where(CrmSync.call_id == call_id, CrmSync.run_id == run_id)
        ).first()


def _get_run(call_id):
    with get_session() as session:
        return session.scalars(select(Run).where(Run.call_id == call_id)).first()


def _run_stage(call_id, name):
    run = _get_run(call_id)
    return next(s for s in run.stages if s["name"] == name)


def test_not_configured_short_circuits_with_no_crm_call(monkeypatch):
    monkeypatch.delenv("HUBSPOT_ACCESS_TOKEN", raising=False)
    call_id, run_id = _seed_call(caller_phone="+15551234567")
    run_insights({"call_id": call_id})

    stage = _run_stage(call_id, "crm_sync")
    assert stage["status"] == "ok"
    assert _crm_sync_row(call_id, run_id) is None  # no attempt, no row


def test_configured_and_matched_writes_note_and_properties(monkeypatch):
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "test-token")
    fake = FakeAdapter()
    monkeypatch.setattr(pipeline_mod, "get_crm_adapter", lambda: fake)
    call_id, run_id = _seed_call(caller_phone="+15551234567")

    run_insights({"call_id": call_id})

    row = _crm_sync_row(call_id, run_id)
    assert row.status == "auto_written"
    assert row.hubspot_contact_id == "contact-1"
    assert row.hubspot_note_id == "note-1"
    assert fake.note_calls == 1
    assert fake.property_calls == 1
    stage = _run_stage(call_id, "crm_sync")
    assert stage["status"] == "ok"


def test_unmatched_contact_skips_write_and_flags(monkeypatch):
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "test-token")
    fake = FakeAdapter(match=ContactMatch(None, "no_match"))
    monkeypatch.setattr(pipeline_mod, "get_crm_adapter", lambda: fake)
    call_id, run_id = _seed_call(caller_phone="+15559999999")

    run_insights({"call_id": call_id})

    row = _crm_sync_row(call_id, run_id)
    assert row.status == "unmatched"
    assert row.error == "no_match"
    assert fake.note_calls == 0
    stage = _run_stage(call_id, "crm_sync")
    assert stage["status"] == "ok"  # unmatched is a clean outcome, not a failure


def test_write_failure_marks_run_partial_and_is_retryable(monkeypatch):
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "test-token")
    fake = FakeAdapter(fail_note=True)
    monkeypatch.setattr(pipeline_mod, "get_crm_adapter", lambda: fake)
    call_id, run_id = _seed_call(caller_phone="+15551234567")

    run_insights({"call_id": call_id})

    run = _get_run(call_id)
    assert run.status == "partial"
    stage = _run_stage(call_id, "crm_sync")
    assert stage["status"] == "failed"
    assert "hubspot note API down" in stage["error"]
    assert fake.note_calls == 3  # capped retries (run_state.MAX_ATTEMPTS)


def test_retry_after_note_success_does_not_repost_note(monkeypatch):
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "test-token")
    fake = FakeAdapter(fail_properties=True)
    monkeypatch.setattr(pipeline_mod, "get_crm_adapter", lambda: fake)
    call_id, run_id = _seed_call(caller_phone="+15551234567")

    run_insights({"call_id": call_id})  # note succeeds once; properties keep failing → partial

    assert fake.note_calls == 1  # written once, skipped on each internal retry
    assert fake.property_calls == 3  # properties retried up to the cap

    row = _crm_sync_row(call_id, run_id)
    assert row.hubspot_note_id == "note-1"
    assert row.status != "auto_written"  # never completed
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_crm_sync_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_crm_adapter' from 'app.pipeline'` (and `"crm_sync"` missing from `STAGES`).

- [ ] **Step 3: Add `crm_sync` to `STAGES`**

In `backend/app/run_state.py`, change:

```python
STAGES = ["transcribe", "detect_intent", "extract", "validate", "score", "compliance", "compose_email", "crm_sync"]
```

(`CRITICAL_STAGES` stays unchanged — `crm_sync` must never be critical, per the spec.)

- [ ] **Step 4: Wire the stage into `pipeline.py`**

In `backend/app/pipeline.py`, update imports:

```python
from .adapters.crm.base import get_crm_adapter, is_crm_configured
from .adapters.pyai.base import get_adapter
from .crm_sync import build_note_html, build_properties
from .db import get_session
from .compliance import run_compliance_check
from .evidence import validate_extraction
from .insights import compose_email, detect_intent, extract, prettify_transcript, score
from .jobs import enqueue, handler
from .models import Call, CrmSync, Run
from .packs import BUILTIN_PACKS
from .run_state import BudgetExceeded, RunState, StageFailed
from .transcription import deliver_transcript, update_transcript_lines
```

Add the orchestration function (place it right before `def run_insights`):

```python
def sync_call_to_crm(call_id: str, run_id: str, lines: list[dict], insights: dict, compliance: dict | None) -> None:
    """Match the call to a HubSpot Contact and write a Note + Properties.
    Checkpoints hubspot_contact_id / hubspot_note_id to the CrmSync row as
    soon as each succeeds, so a retry after a partial failure never reposts
    an already-written Note (see spec §5/§7).
    """
    with get_session() as session:
        call = session.get(Call, call_id)
        sync_row = session.scalars(
            select(CrmSync).where(CrmSync.call_id == call_id, CrmSync.run_id == run_id)
        ).first()
        if sync_row is None:
            sync_row = CrmSync(call_id=call_id, run_id=run_id)
            session.add(sync_row)
            session.flush()
        sync_row.attempts += 1
        phone, email = call.caller_phone, call.caller_email
        contact_id = sync_row.hubspot_contact_id
        note_id = sync_row.hubspot_note_id
        sync_id = sync_row.id
        session.commit()

    adapter = get_crm_adapter()

    if contact_id is None:
        match = adapter.find_contact(phone=phone, email=email)
        if match.contact_id is None:
            with get_session() as session:
                row = session.get(CrmSync, sync_id)
                row.status = "unmatched"
                row.error = match.reason
                session.commit()
            return
        contact_id = match.contact_id
        with get_session() as session:
            row = session.get(CrmSync, sync_id)
            row.hubspot_contact_id = contact_id
            session.commit()

    if note_id is None:
        note_html = build_note_html(insights, compliance)
        note_id = adapter.write_note(contact_id, note_html)
        with get_session() as session:
            row = session.get(CrmSync, sync_id)
            row.hubspot_note_id = note_id
            session.commit()

    synced_at = datetime.now(timezone.utc).isoformat()
    properties = build_properties(insights, compliance, synced_at)
    adapter.upsert_properties(contact_id, properties)

    with get_session() as session:
        row = session.get(CrmSync, sync_id)
        row.properties_written = properties
        row.status = "auto_written"
        session.commit()
```

Insert the stage call in `run_insights`, right after the `compose_email` try/except block and before `except BudgetExceeded as e:` (same indentation as the surrounding `try:` body):

```python
        # 7. CRM sync — opt-in; no-ops entirely if HUBSPOT_ACCESS_TOKEN isn't set
        if is_crm_configured():
            try:
                rs.execute(
                    "crm_sync",
                    lambda: sync_call_to_crm(call_id, run_id, lines, insights, compliance),
                )
            except StageFailed:
                pass
        else:
            rs._get("crm_sync").status = "ok"
```

- [ ] **Step 5: Run to verify the tests pass**

Run: `cd backend && uv run pytest tests/test_crm_sync_pipeline.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Run the full backend suite to check for regressions**

Run: `cd backend && uv run pytest -q`
Expected: PASS, no regressions. Existing tests in `test_insights.py`/`test_compliance.py` that assert `run.status == "shipped"` should be unaffected since `crm_sync` is unconfigured in their test environment (`HUBSPOT_ACCESS_TOKEN` unset) and short-circuits to `status=ok`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/run_state.py backend/app/pipeline.py backend/tests/test_crm_sync_pipeline.py
git commit -m "feat(crm): wire crm_sync into the pipeline as a non-critical stage"
```

---

### Task 5: API exposure — `crm_sync` status on `GET /api/calls/{id}`

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api.py` (extend)

**Interfaces:**
- Consumes: `CrmSync` model (Task 1).
- Produces: `GET /api/calls/{id}` response gains a `"crm_sync"` key: `{"status": str, "hubspot_contact_id": str | None, "error": str | None} | None` (`None` when no sync was ever attempted for the current run).

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_api.py`. First check the top of the file for existing imports (`json`, `Path`, `TestClient`, `app`, `SAMPLES_DIR`, `seed`) — add these two tests at the end of the file:

```python
def test_get_call_has_no_crm_sync_by_default():
    with client() as c:
        calls = c.get("/api/calls").json()
        detail = c.get(f"/api/calls/{calls[0]['id']}").json()
        assert detail["crm_sync"] is None


def test_get_call_surfaces_crm_sync_row_when_present():
    from sqlalchemy import select
    from app.db import get_session
    from app.models import CrmSync, Run

    with client() as c:
        calls = c.get("/api/calls").json()
        call_id = calls[0]["id"]

        with get_session() as session:
            run = session.scalars(select(Run).where(Run.call_id == call_id)).first()
            session.add(CrmSync(call_id=call_id, run_id=run.id, status="unmatched", error="no_phone_or_email"))
            session.commit()

        detail = c.get(f"/api/calls/{call_id}").json()
        assert detail["crm_sync"] == {
            "status": "unmatched", "hubspot_contact_id": None, "error": "no_phone_or_email",
        }
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_api.py -v -k crm_sync`
Expected: FAIL — `KeyError: 'crm_sync'`.

- [ ] **Step 3: Implement — extend `main.py`**

In `backend/app/main.py`, update the model import:

```python
from .models import Call, CrmSync, Run
```

Update `get_call`:

```python
@app.get("/api/calls/{call_id}")
def get_call(call_id: str) -> dict:
    with get_session() as session:
        call = session.get(Call, call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="call not found")
        run = _latest_run(session, call_id)
        crm_sync = None
        if run:
            sync = session.scalars(
                select(CrmSync).where(CrmSync.call_id == call_id, CrmSync.run_id == run.id)
            ).first()
            if sync:
                crm_sync = {
                    "status": sync.status,
                    "hubspot_contact_id": sync.hubspot_contact_id,
                    "error": sync.error,
                }
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
                "edited": bool(run.edited_insights) if run else False,
            },
            "transcript": (
                {"language": call.transcript.language, "lines": call.transcript.lines}
                if call.transcript
                else None
            ),
            "insights": (run.edited_insights or run.insights) if run else None,
            "compliance": run.compliance if run else None,
            "crm_sync": crm_sync,
        }
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && uv run pytest tests/test_api.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `cd backend && uv run pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/test_api.py
git commit -m "feat(crm): expose crm_sync status on GET /api/calls/{id}"
```

---

### Task 6: Frontend — CRM sync status section

**Files:**
- Modify: `web/lib/api.ts`
- Modify: `web/lib/status.ts`
- Modify: `web/components/CallView.tsx`

**Interfaces:**
- Consumes: `GET /api/calls/{id}`'s new `"crm_sync"` field (Task 5).
- Produces: a `CrmSyncInfo` type and a small status section rendered in the call detail page. No new backend calls — read-only display.

This project has no frontend test runner configured (no jest/vitest); the established verification pattern for frontend work in this codebase is `npm run build` (type-check) plus a manual smoke check against the running dev servers — follow that pattern here, not a new one.

- [ ] **Step 1: Add the type to `web/lib/api.ts`**

Add this type definition (place it near the other type definitions, e.g. right after `ComplianceFinding`):

```typescript
export type CrmSyncInfo = {
  status: "unmatched" | "auto_written" | "pending_review" | "approved" | "rejected" | "failed";
  hubspot_contact_id: string | null;
  error: string | null;
};
```

Update the `CallDetail` type to add the field (after `compliance`):

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
  run: { status: RunStatus; stages: Stage[]; edited: boolean };
  transcript: { language: string; lines: { line: number; speaker: string; text: string }[] } | null;
  insights: Insights | null;
  compliance: { verdict: string; audit_hash: string; source: string; findings: ComplianceFinding[] } | null;
  crm_sync: CrmSyncInfo | null;
};
```

- [ ] **Step 2: Add the stage label to `web/lib/status.ts`**

Update `stageLabels` (add the `crm_sync` line):

```typescript
export const stageLabels: Record<string, string> = {
  transcribe: "Transcribe",
  detect_intent: "Detect call type",
  extract: "Extract insights",
  validate: "Verify evidence",
  score: "Score",
  compliance: "Compliance check",
  compose_email: "Draft email",
  crm_sync: "CRM sync",
};
```

- [ ] **Step 3: Render the status section in `CallView.tsx`**

Update the destructuring line to include `crm_sync`:

```tsx
  const { call, run, transcript, insights, compliance, crm_sync } = data;
```

Insert the new section right after the existing `FailureBanner` render (immediately following the line `{(run.status === "partial" || run.status === "failed") && <FailureBanner run={run} insights={insights} onRetry={doRetry} busy={busy} />}`):

```tsx
      {crm_sync && <CrmSyncStatus crmSync={crm_sync} />}
```

Add the `CrmSyncStatus` component near the other small display components at the bottom of the file (e.g. right after `ComplianceDetails`):

```tsx
function CrmSyncStatus({ crmSync }: { crmSync: NonNullable<CallDetail["crm_sync"]> }) {
  if (crmSync.status === "auto_written") {
    return (
      <div className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
        ✓ Synced to HubSpot{crmSync.hubspot_contact_id ? ` (contact ${crmSync.hubspot_contact_id})` : ""}
      </div>
    );
  }
  if (crmSync.status === "unmatched") {
    return (
      <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
        ⚠ Not synced to HubSpot — no matching contact found ({crmSync.error}). Link the contact in HubSpot manually.
      </div>
    );
  }
  return null; // "failed" is already shown via the generic FailureBanner/ProcessingDetails
}
```

- [ ] **Step 4: Type-check**

Run: `cd web && npm run build`
Expected: build succeeds with no TypeScript errors.

- [ ] **Step 5: Manual smoke check**

Start both servers (`make backend` and `make web`, or `make demo`), set `HUBSPOT_ACCESS_TOKEN` to a real HubSpot developer test-account Private App token in `backend/.env`, then in a HubSpot test account create one Contact with a phone number. Upload a call via the home page's upload widget with `caller_phone` matching that Contact (the current UI doesn't have a field for this yet — for this smoke test, POST directly: `curl -F "file=@some.wav" -F "caller_phone=+15551234567" http://localhost:8000/api/ingest/upload`). Once the run finishes, open the call in the UI and confirm:
- The "✓ Synced to HubSpot" banner appears
- The Contact in HubSpot has a new Note with the call-outs/summary
- The Contact's `open_gong_score`/`open_gong_flags`/`open_gong_last_synced_at` properties are populated

This is the one live-HubSpot check the spec calls for (§8) — not a pre-implementation gate, done here during implementation.

- [ ] **Step 6: Commit**

```bash
git add web/lib/api.ts web/lib/status.ts web/components/CallView.tsx
git commit -m "feat(crm): show HubSpot sync status on the call detail page"
```

---

## Self-Review

**Spec coverage:**
- §1 Connection → Task 2 (`.env.example`, `setup_env.status()`, `HubSpotAdapter(token)`)
- §2 Matching → Task 2 (`find_contact`, `normalize_phone`, "confident match" = exactly one result) + Task 1 (where phone/email actually come from)
- §3 What gets written → Task 3 (Note HTML + 4 fixed Properties + call-outs)
- §4 Data model → Task 1 (`CrmSync` table, status enum including the reserved future states)
- §5 Pipeline integration → Task 4 (`crm_sync` stage, idempotency via checkpointed `CrmSync` row, graceful absence when unconfigured)
- §6 Connector shape → Task 2 (`CrmAdapter` protocol, `HubSpotAdapter` the only implementation)
- §7 Error handling table → Task 4's five tests map 1:1 to the five table rows
- §8 Testing approach → matching-logic unit tests (Task 2), content-builder unit tests (Task 3), pipeline tests with a faked adapter (Task 4), manual smoke test during implementation (Task 6, Step 5)
- Open Questions (threshold, deal-size parsing, ambiguous-contact handling) → all explicitly reflected in Task 3's `LOW_SCORE_THRESHOLD` comment, `_budget_quote`'s free-text approach, and Task 2's `"ambiguous"` reason — none silently dropped.

**Placeholder scan:** none — every step has complete, runnable code; no TBD/TODO markers.

**Type consistency:** `ContactMatch(contact_id, reason)` is defined once in Task 2 and used with the same two positional fields in Tasks 2 and 4. `sync_call_to_crm`'s signature (`call_id, run_id, lines, insights, compliance`) matches its call site in Task 4 exactly. `build_note_html`/`build_properties`'s signatures in Task 3 match their call sites in Task 4 exactly. The `CrmSync` column names (`hubspot_contact_id`, `hubspot_note_id`, `properties_written`, `error`, `attempts`, `status`) are identical across Tasks 1, 4, and 5. `CrmSyncInfo`'s three fields in Task 6 match exactly what Task 5's endpoint returns.
