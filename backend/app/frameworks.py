"""Grounded definitions of common sales frameworks.

Fed to the pack compiler so that when a user says "score against MEDDIC" the
field set comes from a real definition we control, not the model's memory.
Free-form asks ("flag churn risk") fall through to pure generation.
"""

FRAMEWORKS = {
    "meddic": {
        "label": "MEDDIC",
        "fields": [
            ("metrics_identified", "Did the rep quantify the economic impact / metrics the buyer cares about?"),
            ("economic_buyer_identified", "Was the economic buyer (who controls budget) identified?"),
            ("decision_criteria_understood", "Did the rep learn the buyer's decision criteria?"),
            ("decision_process_understood", "Was the buyer's decision/approval process mapped?"),
            ("pain_identified", "Was a concrete business pain / need identified?"),
            ("champion_identified", "Was an internal champion identified?"),
        ],
    },
    "bant": {
        "label": "BANT",
        "fields": [
            ("budget_identified", "Was budget discussed or established?"),
            ("authority_identified", "Was the decision-making authority identified?"),
            ("need_identified", "Was a concrete need established?"),
            ("timeline_identified", "Was a purchase timeline established?"),
        ],
    },
    "spiced": {
        "label": "SPICED",
        "fields": [
            ("situation_understood", "Was the buyer's current situation understood?"),
            ("pain_identified", "Was the core pain identified?"),
            ("impact_quantified", "Was the impact of the pain quantified?"),
            ("critical_event_identified", "Was a compelling/critical event or deadline surfaced?"),
            ("decision_process_understood", "Was the decision process understood?"),
        ],
    },
}


def framework_reference() -> str:
    """A compact reference block for the compiler prompt."""
    out = []
    for key, fw in FRAMEWORKS.items():
        out.append(f"{fw['label']} ({key}):")
        for name, desc in fw["fields"]:
            out.append(f"  - {name}: {desc}")
    return "\n".join(out)
