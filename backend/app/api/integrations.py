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
