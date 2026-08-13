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
