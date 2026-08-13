"""Agent skill-router: given a transcript and one agent's attached skills,
decide which skills should run for this call. One LLM call per dispatched
agent, steered by that agent's own editable system prompt. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §1.
"""

from . import llm

_SCHEMA_TEMPLATE = {
    "type": "object",
    "properties": {
        "skill_ids": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": ["skill_ids", "reasoning"],
}


def _transcript_text(lines: list[dict]) -> str:
    return "\n".join(f"[{l['line']}] {l['speaker']}: {l['text']}" for l in lines)


def route_skills(
    transcript_lines: list[dict], agent_system_prompt: str, skills: list[dict]
) -> tuple[list[str], str, float]:
    """Returns (selected_skill_ids, reasoning, cost). `skills` is
    [{"id","name","description","when_to_use"}, ...] — the agent's attached
    skills only."""
    if not skills:
        return [], "No skills are attached to this agent.", 0.0

    catalog = "\n".join(f"- {s['id']}: {s['name']} — {s['description']} (use when: {s['when_to_use']})" for s in skills)
    valid_ids = {s["id"] for s in skills}
    schema = dict(_SCHEMA_TEMPLATE)
    schema["properties"] = dict(_SCHEMA_TEMPLATE["properties"])
    schema["properties"]["skill_ids"] = {"type": "array", "items": {"type": "string", "enum": list(valid_ids)}}

    user = (
        f"{agent_system_prompt}\n\n"
        f"Decide which of these skills should run on this call. Return only "
        f"the ids of skills that should run — an empty list is valid if none "
        f"apply. Briefly explain your reasoning.\n\nSkills:\n{catalog}\n\n"
        f"Transcript:\n{_transcript_text(transcript_lines)}"
    )
    result, cost = llm.complete_json("You choose which skills apply to a call.", user, schema, max_tokens=500)
    selected = [s_id for s_id in result.get("skill_ids", []) if s_id in valid_ids]
    return selected, result.get("reasoning", ""), cost
