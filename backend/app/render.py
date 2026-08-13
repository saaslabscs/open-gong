"""Rendering: effective insights, Markdown export, and the shareable snapshot.

Shared by the export endpoints and the share-link snapshot so a call renders
identically whether downloaded or shared. The share snapshot deliberately
excludes the raw transcript and the internal compliance panel.
"""

from typing import Any


def effective_insights(run) -> dict | None:
    """Human edits win over the original AI output for anything user-facing."""
    return run.edited_insights or run.insights


def _cite(evidence: list) -> str:
    if not evidence:
        return ""
    return " " + " ".join(f"[L{e['line']}]" for e in evidence)


def to_markdown(call, run, insights: dict, *, include_transcript: bool = False, transcript=None) -> str:
    lines: list[str] = [f"# {call.title}", ""]
    lines.append(f"_Status: {run.status}_" + (" · _edited_" if run.edited_insights else ""))
    lines.append("")

    intent = insights.get("intent") or {}
    if intent.get("value"):
        lines.append(f"**Intent:** {intent['value']} ({round((intent.get('confidence') or 0) * 100)}%)")
        lines.append("")

    if insights.get("summary"):
        lines.append("## Summary")
        for s in insights["summary"]:
            lines.append(f"- {s['text']}{_cite(s.get('evidence', []))}")
        lines.append("")

    if insights.get("objections"):
        lines.append("## Objections & concerns")
        for o in insights["objections"]:
            status = f" ({o['status']})" if o.get("status") else ""
            lines.append(f"- **{o['label']}**{status}: {o['detail']}{_cite(o.get('evidence', []))}")
        lines.append("")

    if insights.get("next_steps"):
        lines.append("## Next steps")
        for n in insights["next_steps"]:
            owner = f" — _{n['owner']}_" if n.get("owner") else ""
            lines.append(f"- {n['text']}{owner}{_cite(n.get('evidence', []))}")
        lines.append("")

    sc = insights.get("scorecard") or {}
    if sc.get("fields"):
        lines.append(f"## Scorecard ({sc.get('pack', '')})")
        for f in sc["fields"]:
            if f["kind"] == "deterministic":
                val = "yes" if f.get("value") else "no" if f.get("value") is False else "—"
                lines.append(f"- {f['name'].replace('_', ' ')}: **{val}**{_cite(f.get('evidence', []))}")
            else:
                lines.append(
                    f"- {f['name'].replace('_', ' ')}: **{f.get('score')}/{f.get('max_score')}** "
                    f"— {f.get('justification', '')}{_cite(f.get('evidence', []))}"
                )
        lines.append("")

    email = insights.get("follow_up_email")
    if email:
        lines.append("## Follow-up email")
        lines.append(f"**Subject:** {email['subject']}")
        lines.append("")
        lines.append(email["body"])
        lines.append("")

    if include_transcript and transcript:
        lines.append("## Transcript")
        for l in transcript.lines:
            lines.append(f"{l['line']}. **{l['speaker']}:** {l['text']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def export_json(call, run, insights: dict) -> dict[str, Any]:
    """Full structured export — includes evidence, excludes internal compliance."""
    return {
        "call": {
            "id": call.id,
            "title": call.title,
            "duration_s": call.duration_s,
            "recorded_at": call.recorded_at.isoformat(),
        },
        "run": {"status": run.status, "edited": run.edited_insights is not None},
        "insights": insights,
    }


def share_snapshot(call, run, insights: dict) -> dict[str, Any]:
    """Frozen at share time. No raw transcript, no compliance panel."""
    return {
        "title": call.title,
        "recorded_at": call.recorded_at.isoformat(),
        "duration_s": call.duration_s,
        "intent": (insights.get("intent") or {}).get("value"),
        "summary": insights.get("summary", []),
        "objections": insights.get("objections", []),
        "next_steps": insights.get("next_steps", []),
        "scorecard": insights.get("scorecard"),
        "follow_up_email": insights.get("follow_up_email"),
    }
