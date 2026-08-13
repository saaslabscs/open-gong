"""Compile a plain-English rubric into an insight pack.

"We're a sales team — score against MEDDIC, track deal size and competitor
mentions, and flag if they ask about security." → a pack the pipeline runs.

The LLM only chooses the fields (grounded on the framework library for named
methodologies); packs.assemble_pack builds the valid structure. So the output
is always a runnable pack — the human reviews/edits the fields, not raw schema.
"""

from .frameworks import framework_reference
from .llm import complete_json
from .packs import assemble_pack

SYSTEM = (
    "You design call-scoring rubrics. Given a team's plain-English description, "
    "you choose which yes/no checks and which 1-5 judgment scores to evaluate on "
    "each call. Prefer the grounded framework definitions when a methodology is "
    "named. Use snake_case field names. Never invent transcript content."
)


def compile_pack(instructions: str) -> tuple[dict, float]:
    """Prose → (pack dict, cost). The pack is a draft for human review."""
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "short kebab-case pack name"},
            "applies_to": {"type": "string", "enum": ["sales", "support", "any"]},
            "deterministic_fields": {
                "type": "array",
                "description": "yes/no checks answerable from the transcript",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["name", "description"],
                },
            },
            "judgment_fields": {
                "type": "array",
                "description": "qualitative 1-5 scores needing reasoning",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "max_score": {"type": "integer"},
                        "instructions": {"type": "string"},
                    },
                    "required": ["name", "instructions"],
                },
            },
        },
        "required": ["name", "applies_to", "deterministic_fields", "judgment_fields"],
    }
    user = (
        "Design a scoring pack for this team description:\n\n"
        f'"""{instructions}"""\n\n'
        "If a named framework applies, use its grounded fields below "
        "(you may add extras the description asks for):\n\n"
        f"{framework_reference()}\n\n"
        "Return the field lists only — do not write JSON Schema."
    )
    spec, cost = complete_json(SYSTEM, user, schema, max_tokens=1500)
    pack = assemble_pack(
        spec["name"],
        spec.get("deterministic_fields", []),
        spec.get("judgment_fields", []),
        applies_to=spec.get("applies_to", "any"),
    )
    return pack, cost
