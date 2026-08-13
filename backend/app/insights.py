"""Transcript prettify: reformat raw STT into readable text before any
skill runs. The old intent → pack → extraction → scoring → email chain this
module used to define is retired — that work is now done generically by
seeded skills executed through skills/executor.py. See
docs/superpowers/specs/2026-08-13-agent-skill-architecture-design.md §3.
"""

from . import llm

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
