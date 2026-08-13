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
