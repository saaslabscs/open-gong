"""Run one skill against a transcript: build a JSON schema from its fields:
declaration, drive one LLM call through the provider-agnostic gateway, gate
the result through the generalized evidence gate. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §2.
"""

from .. import llm
from ..evidence import validate_fields

SYSTEM = (
    "You analyze business call transcripts. You only state what the transcript "
    "supports. Every claim must cite verbatim quotes with their line numbers. "
    "If something was not said on the call, it does not appear in your output."
)

_EVIDENCE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"quote": {"type": "string"}, "line": {"type": "integer"}},
        "required": ["quote", "line"],
    },
}


def _transcript_text(lines: list[dict]) -> str:
    return "\n".join(f"[{l['line']}] {l['speaker']}: {l['text']}" for l in lines)


def _build_schema(fields_spec: dict) -> dict:
    properties: dict = {}
    required: list[str] = []

    for name in fields_spec.get("checks", []):
        properties[name] = {
            "type": "object",
            "properties": {"value": {"type": ["boolean", "null"]}, "evidence": _EVIDENCE_SCHEMA},
            "required": ["value", "evidence"],
        }
        required.append(name)

    for spec in fields_spec.get("scores", []):
        properties[spec["name"]] = {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "minimum": 1, "maximum": spec["max"]},
                "justification": {"type": "string"},
                "evidence": _EVIDENCE_SCHEMA,
            },
            "required": ["score", "justification", "evidence"],
        }
        required.append(spec["name"])

    for name in fields_spec.get("claims", []):
        properties[name] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "evidence": _EVIDENCE_SCHEMA},
                "required": ["text", "evidence"],
            },
        }
        required.append(name)

    return {"type": "object", "properties": properties, "required": required}


def run_skill(skill: dict, transcript_lines: list[dict]) -> tuple[dict, list[dict], float]:
    """Returns (cleaned_output, dropped, cost). `skill` has keys name,
    description, when_to_use, body_md, fields (fields may be None for a
    narrative-only skill, in which case output passes through ungated)."""
    fields_spec = skill.get("fields") or {}
    schema = _build_schema(fields_spec) if fields_spec else {"type": "object", "properties": {}}

    user = f"Run this skill: {skill['name']}\n\n{skill['body_md']}\n\n{_transcript_text(transcript_lines)}"
    raw, cost = llm.complete_json(SYSTEM, user, schema, max_tokens=3000)

    if not fields_spec:
        return raw, [], cost

    cleaned, dropped = validate_fields(fields_spec, raw, transcript_lines)
    return cleaned, dropped, cost
