"""Built-in insight packs: what to extract and how to score it, per intent.

A pack = extraction JSON schema (every claim field demands {value, quote, line}
receipts) + scoring spec (deterministic fields scored in code, judgment fields
scored by one narrow LLM call). Custom packs compiled from prose land in M9
and produce this same shape.
"""

_EVIDENCE = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "quote": {"type": "string", "description": "verbatim quote from the transcript"},
            "line": {"type": "integer", "description": "transcript line number containing the quote"},
        },
        "required": ["quote", "line"],
    },
}

_COMMON_PROPERTIES = {
    "summary": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "evidence": _EVIDENCE},
            "required": ["text", "evidence"],
        },
        "description": "3-5 factual bullets covering what happened on the call",
    },
    "objections": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "short label, e.g. 'price'"},
                "detail": {"type": "string"},
                "status": {"type": "string", "description": "addressed | open | unresolved"},
                "evidence": _EVIDENCE,
            },
            "required": ["label", "detail", "evidence"],
        },
        "description": "objections, concerns, or complaints raised by the customer",
    },
    "next_steps": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "owner": {"type": "string"},
                "evidence": _EVIDENCE,
            },
            "required": ["text", "evidence"],
        },
        "description": "concrete commitments made on the call, with who owns them",
    },
}


def _flag(description: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "value": {"type": ["boolean", "null"]},
            "evidence": _EVIDENCE,
        },
        "required": ["value", "evidence"],
        "description": description,
    }


SALES_PACK = {
    "name": "sales-default",
    "version": 1,
    "json_schema": {
        "type": "object",
        "properties": {
            **_COMMON_PROPERTIES,
            "recording_disclosure": _flag("did anyone disclose the call is recorded?"),
            "budget_discussed": _flag("was budget or price expectation discussed?"),
            "decision_process_identified": _flag("were other decision makers / approval steps identified?"),
            "timeline_identified": _flag("was a concrete timeline or deadline established?"),
            "next_step_secured": _flag("was a concrete next step agreed (meeting, deliverable, date)?"),
        },
        "required": list(_COMMON_PROPERTIES) + [
            "recording_disclosure",
            "budget_discussed",
            "decision_process_identified",
            "timeline_identified",
            "next_step_secured",
        ],
    },
    "scoring_spec": {
        "deterministic": [
            "recording_disclosure",
            "budget_discussed",
            "decision_process_identified",
            "timeline_identified",
            "next_step_secured",
        ],
        "judgment": [
            {
                "name": "discovery_quality",
                "max_score": 5,
                "instructions": (
                    "How well did the rep uncover the prospect's pain, urgency, buying "
                    "process, and budget through open questions rather than pitching? "
                    "5 = quantified pain + urgency + committee + budget all surfaced."
                ),
            },
            {
                "name": "objection_handling",
                "max_score": 5,
                "instructions": (
                    "How directly and credibly were the customer's objections answered? "
                    "5 = every objection acknowledged and answered with specifics or a "
                    "concrete follow-up commitment."
                ),
            },
        ],
    },
}

SUPPORT_PACK = {
    "name": "support-default",
    "version": 1,
    "json_schema": {
        "type": "object",
        "properties": {
            **_COMMON_PROPERTIES,
            "recording_disclosure": _flag("did anyone disclose the call is recorded?"),
            "issue_identified": _flag("was the customer's issue clearly identified?"),
            "resolution_provided": _flag("was a resolution provided or concretely promised?"),
            "timeline_communicated": _flag("was a timeline for resolution communicated?"),
            "churn_risk_flagged": _flag("did the customer signal cancellation or churn risk?"),
        },
        "required": list(_COMMON_PROPERTIES) + [
            "recording_disclosure",
            "issue_identified",
            "resolution_provided",
            "timeline_communicated",
            "churn_risk_flagged",
        ],
    },
    "scoring_spec": {
        "deterministic": [
            "recording_disclosure",
            "issue_identified",
            "resolution_provided",
            "timeline_communicated",
            "churn_risk_flagged",
        ],
        "judgment": [
            {
                "name": "empathy_and_tone",
                "max_score": 5,
                "instructions": (
                    "Did the agent acknowledge frustration, avoid blame, and keep a "
                    "helpful tone throughout? 5 = consistently empathetic, including "
                    "under pressure."
                ),
            },
            {
                "name": "resolution_quality",
                "max_score": 5,
                "instructions": (
                    "Was the root cause found and fully addressed (not just patched), "
                    "with clear communication of what happens next? 5 = root cause "
                    "fixed on-call with timeline and preventive step."
                ),
            },
        ],
    },
}

BUILTIN_PACKS = {"sales": SALES_PACK, "support": SUPPORT_PACK}


def assemble_pack(
    name: str,
    deterministic: list[dict],
    judgment: list[dict],
    *,
    applies_to: str = "any",
    version: int = 1,
) -> dict:
    """Build a valid pack from field lists — the compiler decides the fields,
    this guarantees the structure (common props, {value,quote,line} receipts,
    scoring spec). A compiled pack can never be malformed.

    deterministic: [{"name","description"}]
    judgment:      [{"name","max_score","instructions"}]
    """
    props = dict(_COMMON_PROPERTIES)
    for f in deterministic:
        props[f["name"]] = _flag(f.get("description", f["name"]))
    schema = {
        "type": "object",
        "properties": props,
        "required": list(_COMMON_PROPERTIES) + [f["name"] for f in deterministic],
    }
    return {
        "name": name,
        "version": version,
        "applies_to": applies_to,
        "json_schema": schema,
        "scoring_spec": {
            "deterministic": [f["name"] for f in deterministic],
            "judgment": [
                {
                    "name": j["name"],
                    "max_score": int(j.get("max_score", 5)),
                    "instructions": j.get("instructions", ""),
                }
                for j in judgment
            ],
        },
    }
