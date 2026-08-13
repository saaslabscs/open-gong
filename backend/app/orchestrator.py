"""Orchestrator dispatch: given a transcript and the roster of enabled
agents, decide which agents this call needs. One LLM call, steered by the
orchestrator's own editable system prompt. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §1, §4.

An empty result is valid, not an error — the "orchestrator selects no
agents" edge case is a deliberate, storable outcome, not a failure.
"""

from . import llm

_SCHEMA_TEMPLATE = {
    "type": "object",
    "properties": {
        "agent_ids": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": ["agent_ids", "reasoning"],
}


def _transcript_text(lines: list[dict]) -> str:
    return "\n".join(f"[{l['line']}] {l['speaker']}: {l['text']}" for l in lines)


def dispatch(
    transcript_lines: list[dict], agents: list[dict], system_prompt: str
) -> tuple[list[str], str, float]:
    """Returns (selected_agent_ids, reasoning, cost). `agents` is
    [{"id","name","description"}, ...] — only enabled agents should be
    passed in by the caller."""
    if not agents:
        return [], "No agents are configured.", 0.0

    roster = "\n".join(f"- {a['id']}: {a['name']} — {a['description']}" for a in agents)
    valid_ids = {a["id"] for a in agents}
    schema = dict(_SCHEMA_TEMPLATE)
    schema["properties"] = dict(_SCHEMA_TEMPLATE["properties"])
    schema["properties"]["agent_ids"] = {"type": "array", "items": {"type": "string", "enum": list(valid_ids)}}

    user = (
        f"{system_prompt}\n\n"
        f"Decide which of these agents should run on this call. Return only "
        f"the ids of agents that should run — an empty list is valid if none "
        f"apply. Briefly explain your reasoning.\n\nAgents:\n{roster}\n\n"
        f"Transcript:\n{_transcript_text(transcript_lines)}"
    )
    result, cost = llm.complete_json("You route calls to the right agents.", user, schema, max_tokens=500)
    selected = [a_id for a_id in result.get("agent_ids", []) if a_id in valid_ids]
    return selected, result.get("reasoning", ""), cost
