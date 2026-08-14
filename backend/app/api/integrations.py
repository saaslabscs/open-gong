"""CRM connection management. See
docs/superpowers/specs/2026-08-14-integrations-design.md §4.

The access token never leaves this module: responses carry only a four-character
hint. The database is the sole source of truth — no environment variables.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..db import get_session
from ..integrations.providers import BY_KEY, PROVIDERS, Provider
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
