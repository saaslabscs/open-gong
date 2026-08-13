"""The insight engine: intent → pack → extraction → evidence gate → scoring → email.

Each function is one narrow LLM task through the provider-agnostic gateway.
The pipeline (pipeline.py) drives these inside the run-state harness so
retries, budget, and shipped/partial/failed semantics apply uniformly.
"""

import json

from . import llm
from .packs import BUILTIN_PACKS

SYSTEM = (
    "You analyze business call transcripts. You only state what the transcript "
    "supports. Every claim must cite verbatim quotes with their line numbers. "
    "If something was not said on the call, it does not appear in your output."
)


def _looks_clean(lines: list[dict]) -> bool:
    """True if the transcript already reads well (capitalized + punctuated),
    so we skip the prettify LLM call for fixtures and re-runs."""
    if not lines:
        return True
    clean = sum(
        1 for l in lines
        if l["text"] and l["text"][0].isupper() and l["text"].rstrip()[-1:] in ".?!"
    )
    return clean / len(lines) >= 0.6


def prettify_transcript(lines: list[dict]) -> tuple[list[dict], float]:
    """Reformat raw STT into readable text: punctuation, capitalization, and
    real speaker names when clearly established. Preserves line count and line
    numbers exactly (evidence anchors stay valid). Returns (lines, cost).

    Skips already-clean transcripts (cost 0). Never invents or rewords content.
    """
    if _looks_clean(lines):
        return lines, 0.0

    n = len(lines)
    schema = {
        "type": "object",
        "properties": {
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "line": {"type": "integer"},
                        "speaker": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["line", "speaker", "text"],
                },
            }
        },
        "required": ["lines"],
    }
    user = (
        "Reformat this raw call transcript to be readable. Rules:\n"
        "- add proper capitalization and punctuation\n"
        "- fix obvious transcription artifacts (spelled-out numbers to digits where natural)\n"
        "- NEVER change wording, add, or remove content — formatting only\n"
        "- keep EXACTLY the same lines and line numbers (return all "
        f"{n} lines in order)\n"
        "- if a speaker's real name is clearly established in the dialogue, use it; "
        "otherwise keep the given speaker label\n\n"
        f"{_transcript_text(lines)}"
    )
    try:
        result, cost = llm.complete_json(SYSTEM, user, schema, max_tokens=4000)
    except Exception:
        return lines, 0.0  # non-fatal: fall back to raw

    new = result.get("lines", [])
    if len(new) != n:
        return lines, cost  # line count must match to keep evidence anchors valid
    # preserve original line numbers positionally; trust only text/speaker
    cleaned = [
        {"line": lines[i]["line"], "speaker": new[i].get("speaker") or lines[i]["speaker"],
         "text": new[i].get("text") or lines[i]["text"]}
        for i in range(n)
    ]
    return cleaned, cost


def _transcript_text(lines: list[dict]) -> str:
    return "\n".join(f"[{l['line']}] {l['speaker']}: {l['text']}" for l in lines)


def detect_intent(lines: list[dict]) -> tuple[dict, float]:
    schema = {
        "type": "object",
        "properties": {
            "value": {"type": "string", "enum": list(BUILTIN_PACKS)},
            "confidence": {"type": "number"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"quote": {"type": "string"}, "line": {"type": "integer"}},
                    "required": ["quote", "line"],
                },
            },
        },
        "required": ["value", "confidence", "evidence"],
    }
    user = (
        "Classify this call's intent: 'sales' (selling, pricing, deals, prospecting) "
        "or 'support' (existing customer issues, billing, cancellations). "
        "Cite the single most indicative line as evidence.\n\n"
        f"{_transcript_text(lines)}"
    )
    return llm.complete_json(SYSTEM, user, schema, max_tokens=400)


def extract(lines: list[dict], pack: dict) -> tuple[dict, float]:
    user = (
        f"Extract the following from this call transcript. Rules:\n"
        f"- every claim needs evidence: verbatim quote + line number\n"
        f"- boolean flags: true ONLY with supporting evidence; if the thing did not "
        f"happen use false with empty evidence; if unclear use null\n"
        f"- never invent, never embellish\n\n"
        f"{_transcript_text(lines)}"
    )
    return llm.complete_json(SYSTEM, user, pack["json_schema"], max_tokens=4000)


def score(lines: list[dict], pack: dict, extraction: dict) -> tuple[dict, float]:
    """Deterministic fields from the validated extraction (no LLM); judgment
    fields via one narrow LLM call over the transcript."""
    fields = []
    for name in pack["scoring_spec"]["deterministic"]:
        flag = extraction.get(name) or {"value": None, "evidence": []}
        fields.append(
            {
                "name": name,
                "kind": "deterministic",
                "value": flag.get("value"),
                "evidence": flag.get("evidence", []),
            }
        )

    judgment_spec = pack["scoring_spec"]["judgment"]
    total_cost = 0.0
    if judgment_spec:
        schema = {
            "type": "object",
            "properties": {
                j["name"]: {
                    "type": "object",
                    "properties": {
                        "score": {"type": "integer", "minimum": 1, "maximum": j["max_score"]},
                        "justification": {"type": "string"},
                        "evidence": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "quote": {"type": "string"},
                                    "line": {"type": "integer"},
                                },
                                "required": ["quote", "line"],
                            },
                        },
                    },
                    "required": ["score", "justification", "evidence"],
                }
                for j in judgment_spec
            },
            "required": [j["name"] for j in judgment_spec],
        }
        rubric = "\n".join(
            f"- {j['name']} (1-{j['max_score']}): {j['instructions']}" for j in judgment_spec
        )
        user = (
            f"Score this call against the rubric. Justify each score in 1-2 sentences "
            f"and cite the most relevant lines as evidence.\n\nRubric:\n{rubric}\n\n"
            f"{_transcript_text(lines)}"
        )
        judged, total_cost = llm.complete_json(SYSTEM, user, schema, max_tokens=1500)
        for j in judgment_spec:
            result = judged.get(j["name"], {})
            fields.append(
                {
                    "name": j["name"],
                    "kind": "judgment",
                    "score": result.get("score"),
                    "max_score": j["max_score"],
                    "justification": result.get("justification", ""),
                    "evidence": result.get("evidence", []),
                }
            )

    return {"pack": pack["name"], "pack_version": pack["version"], "fields": fields}, total_cost


def compose_email(lines: list[dict], insights_so_far: dict) -> tuple[dict, float]:
    schema = {
        "type": "object",
        "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
        "required": ["subject", "body"],
    }
    user = (
        "Draft a short, professional follow-up email from the company rep to the "
        "customer, grounded ONLY in what was agreed on this call (use the extracted "
        "next steps; do not promise anything not discussed). Plain text, no placeholders "
        "like [Name] — use the actual names from the transcript.\n\n"
        f"Extracted insights:\n{json.dumps({k: insights_so_far.get(k) for k in ('summary', 'next_steps', 'objections')}, indent=1)[:3000]}\n\n"
        f"Transcript:\n{_transcript_text(lines)}"
    )
    return llm.complete_json(SYSTEM, user, schema, max_tokens=800)
