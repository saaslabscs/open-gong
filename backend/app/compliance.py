"""Compliance checking.

PyAI's Trace (docs.pyai.com) is the "real" compliance product — deterministic
rule-pack scoring with a sealed audit hash — but it's gated behind a paid
add-on most orgs (including the one this was built against) don't have: a
live spike confirmed `trace=true` on a transcription job returns
`402 trace_not_enabled`. Rather than ship a compliance feature that silently
does nothing for most users, this module is an in-house fallback: one LLM
call checking a small set of consumer-protection-style categories, gated
through the SAME evidence rule as everything else ("no proof, no claim" —
reused via evidence.validate_claims). It never blocks a call, matching
Trace's own "warn mode never blocks" stance.

If PyAI Trace entitlement is available later, swap this module's output for
the real one — the shape (verdict/findings/audit_hash/source) is designed to
match, so nothing downstream (the UI, exports) needs to change.
"""

import hashlib
import json

from . import llm
from .evidence import validate_claims

SYSTEM = (
    "You review business call transcripts for compliance risk. You only flag "
    "what the transcript actually shows, citing the exact line and a verbatim "
    "quote as evidence. If nothing in a category applies, do not include it. "
    "Never invent a quote. The one exception is 'missing_recording_disclosure': "
    "that flags an ABSENCE (nobody ever said it), so it has no quote to cite — "
    "leave its evidence array empty."
)

RULE_CATEGORIES = [
    ("missing_recording_disclosure", "high", "The call was recorded but no one disclosed that to the other party."),
    ("unsubstantiated_claims", "high", "A specific, guaranteed outcome or result was promised without basis (e.g. 'guaranteed to double X')."),
    ("high_pressure_tactics", "medium", "Artificial urgency or pressure was used to force an immediate decision (e.g. 'only good if you sign today')."),
    ("pii_exposure", "high", "Sensitive personal data (SSN, full card number, health details) was spoken aloud unnecessarily."),
]

# Findings about something that never happened have no line to cite — unlike
# every other claim in this codebase, they're allowed through with empty
# evidence (mirrors the false/absent flag fields in evidence.validate_extraction).
ABSENCE_RULES = {"missing_recording_disclosure"}


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rule": {"type": "string", "enum": [c[0] for c in RULE_CATEGORIES]},
                        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                        "detail": {"type": "string"},
                        "evidence": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"quote": {"type": "string"}, "line": {"type": "integer"}},
                                "required": ["quote", "line"],
                            },
                        },
                    },
                    "required": ["rule", "severity", "detail", "evidence"],
                },
            }
        },
        "required": ["findings"],
    }


def _transcript_text(lines: list[dict]) -> str:
    return "\n".join(f"[{l['line']}] {l['speaker']}: {l['text']}" for l in lines)


def run_compliance_check(lines: list[dict]) -> tuple[dict, float]:
    """Returns (compliance dict, cost). Non-fatal by design — callers should
    treat any exception as 'no compliance data', not a run failure."""
    rules_text = "\n".join(f"- {key} ({sev}): {desc}" for key, sev, desc in RULE_CATEGORIES)
    user = (
        f"Check this call for these specific issues only:\n{rules_text}\n\n"
        f"{_transcript_text(lines)}"
    )
    raw, cost = llm.complete_json(SYSTEM, user, _schema(), max_tokens=1200)

    absence_findings = [f for f in raw.get("findings", []) if f.get("rule") in ABSENCE_RULES]
    quotable_findings = [f for f in raw.get("findings", []) if f.get("rule") not in ABSENCE_RULES]
    verified, _dropped = validate_claims(quotable_findings, lines)
    findings = absence_findings + verified
    verdict = "WARN" if findings else "PASS"
    digest = hashlib.sha256(json.dumps(findings, sort_keys=True).encode()).hexdigest()[:16]
    return {
        "verdict": verdict,
        "audit_hash": f"internal_{digest}",
        "source": "internal",
        "findings": findings,
    }, cost
