"""The evidence gate: no proof in the transcript, no claim in the notes.

Every claim's quote must match its cited line. Exact substring passes; a
lightly-normalized fuzzy match (whitespace/case/punctuation) passes with the
quote rewritten to the exact line text span; anything else drops the claim
and logs why. Dropped claims mark the run `partial` — never silently omitted.
"""

import re
from difflib import SequenceMatcher

FUZZY_THRESHOLD = 0.90


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower().strip())


def _quote_ok(quote: str, line_text: str) -> bool:
    if quote in line_text:
        return True
    nq, nl = _normalize(quote), _normalize(line_text)
    if not nq:
        return False
    if nq in nl:
        return True
    return SequenceMatcher(None, nq, nl).ratio() >= FUZZY_THRESHOLD


def _check_evidence(evidence: list, lines: dict[int, str]) -> tuple[bool, str | None]:
    """A claim passes only if EVERY evidence entry verifies."""
    if not isinstance(evidence, list):
        return False, "evidence is not a list"
    for ev in evidence:
        if not isinstance(ev, dict) or "quote" not in ev or "line" not in ev:
            return False, f"malformed evidence entry: {ev!r}"
        if ev["line"] not in lines:
            return False, f"cited line {ev['line']} does not exist"
        if not _quote_ok(str(ev["quote"]), lines[ev["line"]]):
            return False, f"quote not found in line {ev['line']}: {ev['quote']!r}"
    return True, None


def validate_claims(claims: list[dict], transcript_lines: list[dict]) -> tuple[list[dict], list[dict]]:
    """Generic list-of-claims gate: keep claims whose evidence verifies, drop
    the rest with a reason. Used for compliance findings and anything else
    shaped like [{..., "evidence": [...]}]."""
    lines = {l["line"]: l["text"] for l in transcript_lines}
    kept, dropped = [], []
    for i, claim in enumerate(claims):
        evidence = claim.get("evidence", []) if isinstance(claim, dict) else []
        ok, reason = _check_evidence(evidence, lines)
        if ok and evidence:
            kept.append(claim)
        else:
            dropped.append({"where": f"[{i}]", "reason": reason or "no evidence provided"})
    return kept, dropped


def validate_extraction(extraction: dict, transcript_lines: list[dict]) -> tuple[dict, list[dict]]:
    """Returns (cleaned_extraction, dropped) where dropped = [{where, reason}].

    - list-of-claims fields (summary, objections, next_steps): claims with
      bad evidence are removed.
    - flag fields ({value, evidence}): value=true REQUIRES verifying evidence
      (an unproven "yes" becomes null); value=false/null may have empty
      evidence (you can't cite a line proving an absence).
    """
    lines = {l["line"]: l["text"] for l in transcript_lines}
    dropped: list[dict] = []
    cleaned: dict = {}

    for field, value in extraction.items():
        if isinstance(value, list):
            kept = []
            for i, claim in enumerate(value):
                evidence = claim.get("evidence", []) if isinstance(claim, dict) else []
                ok, reason = _check_evidence(evidence, lines)
                if ok and evidence:
                    kept.append(claim)
                else:
                    dropped.append(
                        {"where": f"{field}[{i}]", "reason": reason or "no evidence provided"}
                    )
            cleaned[field] = kept
        elif isinstance(value, dict) and "value" in value:
            evidence = value.get("evidence", [])
            if value["value"] is True:
                ok, reason = _check_evidence(evidence, lines)
                if ok and evidence:
                    cleaned[field] = value
                else:
                    dropped.append({"where": field, "reason": reason or "true claim without evidence"})
                    cleaned[field] = {"value": None, "evidence": []}
            else:
                ok, reason = _check_evidence(evidence, lines)
                cleaned[field] = value if ok else {"value": value["value"], "evidence": []}
                if not ok:
                    dropped.append({"where": f"{field}.evidence", "reason": reason})
        else:
            cleaned[field] = value

    return cleaned, dropped


def validate_fields(fields_spec: dict, raw_output: dict, transcript_lines: list[dict]) -> tuple[dict, list[dict]]:
    """Generalized gate over a skill's declared fields: {checks, scores, claims}.

    Same "no proof, no claim" contract as validate_extraction:
    - checks (flags): value=true REQUIRES verifying evidence (unproven "yes"
      becomes null); value=false/null may have empty evidence.
    - scores: always require verifying evidence; a bad score is dropped
      entirely (not nulled — there's no meaningful "unscored" placeholder
      the caller should render).
    - claims (lists): items with bad evidence are removed from the list.

    A field absent from raw_output is treated as absent, not an error — the
    skill's own LLM call is responsible for completeness; this gate only
    verifies what evidence backs whatever WAS returned.
    """
    lines = {l["line"]: l["text"] for l in transcript_lines}
    cleaned: dict = {}
    dropped: list[dict] = []

    for name in fields_spec.get("checks", []):
        if name not in raw_output:
            continue
        value = raw_output[name]
        evidence = value.get("evidence", []) if isinstance(value, dict) else []
        if isinstance(value, dict) and value.get("value") is True:
            ok, reason = _check_evidence(evidence, lines)
            if ok and evidence:
                cleaned[name] = value
            else:
                dropped.append({"where": name, "reason": reason or "true claim without evidence"})
                cleaned[name] = {"value": None, "evidence": []}
        elif isinstance(value, dict):
            ok, reason = _check_evidence(evidence, lines)
            cleaned[name] = value if ok else {"value": value.get("value"), "evidence": []}
            if not ok:
                dropped.append({"where": f"{name}.evidence", "reason": reason})

    for spec in fields_spec.get("scores", []):
        name = spec["name"]
        if name not in raw_output:
            continue
        value = raw_output[name]
        evidence = value.get("evidence", []) if isinstance(value, dict) else []
        ok, reason = _check_evidence(evidence, lines)
        if ok and evidence:
            cleaned[name] = value
        else:
            dropped.append({"where": name, "reason": reason or "score without evidence"})

    for name in fields_spec.get("claims", []):
        if name not in raw_output:
            cleaned[name] = []
            continue
        items = raw_output[name] if isinstance(raw_output[name], list) else []
        kept = []
        for i, claim in enumerate(items):
            evidence = claim.get("evidence", []) if isinstance(claim, dict) else []
            ok, reason = _check_evidence(evidence, lines)
            if ok and evidence:
                kept.append(claim)
            else:
                dropped.append({"where": f"{name}[{i}]", "reason": reason or "no evidence provided"})
        cleaned[name] = kept

    if not fields_spec:
        return dict(raw_output), []

    return cleaned, dropped
