"""Shared fake LLM for tests — no test ever hits a real provider."""

GOOD_INTENT = {"value": "support", "confidence": 0.9, "evidence": []}
GOOD_EXTRACTION: dict = {"summary": [], "objections": [], "next_steps": []}
GOOD_SCORE: dict = {}
GOOD_COMPLIANCE: dict = {"findings": []}
GOOD_EMAIL = {"subject": "Follow-up", "body": "Hi — recapping our call."}
GOOD_SKILL_OUTPUT: dict = {}
GOOD_DISPATCH: dict = {"agent_ids": [], "reasoning": "No agents matched this call."}
GOOD_SKILL_ROUTE: dict = {"skill_ids": [], "reasoning": "No skills matched."}


def fake_llm(responses: dict):
    """complete_json replacement keyed by a marker found in the user prompt."""

    def fake(system, user, schema, max_tokens=2000):
        if "Classify this call's intent" in user:
            return responses.get("intent", GOOD_INTENT), 0.002
        if "Extract the following" in user:
            r = responses.get("extract", GOOD_EXTRACTION)
            if isinstance(r, Exception):
                raise r
            return r, 0.02
        if "Score this call" in user:
            return responses.get("score", GOOD_SCORE), 0.008
        if "Check this call for these specific issues" in user:
            r = responses.get("compliance", GOOD_COMPLIANCE)
            if isinstance(r, Exception):
                raise r
            return r, 0.003
        if "follow-up email" in user:
            r = responses.get("email", GOOD_EMAIL)
            if isinstance(r, Exception):
                raise r
            return r, 0.004
        if "Run this skill:" in user:
            r = responses.get("skill", GOOD_SKILL_OUTPUT)
            if isinstance(r, Exception):
                raise r
            return r, 0.01
        if "Decide which of these agents" in user:
            r = responses.get("dispatch", GOOD_DISPATCH)
            if isinstance(r, Exception):
                raise r
            return r, 0.002
        if "Decide which of these skills" in user:
            r = responses.get("skill_route", GOOD_SKILL_ROUTE)
            if isinstance(r, Exception):
                raise r
            return r, 0.002
        raise AssertionError(f"unexpected prompt: {user[:80]}")

    return fake
