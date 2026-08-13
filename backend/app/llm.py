"""Provider-agnostic LLM gateway.

Call sites never touch a vendor SDK: they ask for a JSON completion against a
schema and get back (parsed_json, cost_usd). Provider is chosen by env:

  LLM_PROVIDER=anthropic   — Anthropic API directly (default)
  LLM_PROVIDER=openrouter  — OpenAI-compatible; switch models on the fly via
                             LLM_MODEL and set LLM_FALLBACK_MODELS for
                             automatic failover when a model is down

Bad JSON is repaired once (re-prompt with the parse error); if it still fails,
LLMBadJson is raised — the caller's stage retry/partial logic takes over.
Nothing malformed ever leaves this module.
"""

import json
import os

import httpx
import jsonschema

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# $/1M tokens (input, output) for cost accounting; unknown models fall back
# to a conservative default so the budget cap still bites.
PRICES = {
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "_default": (5.00, 25.00),
}


class LLMBadJson(Exception):
    """The model returned unparseable/schema-violating JSON even after repair."""


class LLMError(Exception):
    """Transport or provider error (after fallbacks, if any)."""


def complete_json(system: str, user: str, schema: dict, max_tokens: int = 2000) -> tuple[dict, float]:
    """One JSON completion. Returns (parsed, cost_usd). Raises LLMBadJson/LLMError."""
    provider = os.environ.get("LLM_PROVIDER", "anthropic")
    fn = _anthropic if provider == "anthropic" else _openrouter

    prompt = (
        f"{user}\n\nRespond with ONLY a JSON object matching this schema "
        f"(no prose, no markdown fences):\n{json.dumps(schema)}"
    )
    text, cost = fn(system, prompt, max_tokens)
    try:
        return _parse(text, schema), cost
    except ValueError as first_err:
        repair = (
            f"Your previous response failed validation: {first_err}\n"
            f"Previous response:\n{text[:2000]}\n\n"
            f"Return ONLY the corrected JSON object — the DATA matching the schema, "
            f"not the schema itself. No prose, no markdown fences."
        )
        text2, cost2 = fn(system, repair, max_tokens)
        try:
            return _parse(text2, schema), cost + cost2
        except ValueError as second_err:
            raise LLMBadJson(f"unrepairable JSON: {second_err}") from second_err


def _parse(text: str, schema: dict) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in response")
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(str(e)) from e
    if not isinstance(parsed, dict):
        raise ValueError("top-level JSON is not an object")

    # A common model failure: echoing the schema envelope with data inside
    # "properties" — unwrap before validating.
    if set(parsed) <= {"type", "properties", "required"} and isinstance(
        parsed.get("properties"), dict
    ):
        parsed = parsed["properties"]

    try:
        jsonschema.validate(parsed, schema)
    except jsonschema.ValidationError as e:
        raise ValueError(f"schema violation at {list(e.absolute_path)}: {e.message[:200]}") from e
    return parsed


def _cost(model: str, in_tokens: int, out_tokens: int) -> float:
    p_in, p_out = PRICES.get(model, PRICES["_default"])
    return (in_tokens * p_in + out_tokens * p_out) / 1_000_000


def _anthropic(system: str, user: str, max_tokens: int) -> tuple[str, float]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMError("ANTHROPIC_API_KEY not set")
    model = os.environ.get("LLM_MODEL", "claude-sonnet-5")
    resp = httpx.post(
        ANTHROPIC_URL,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        json={
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise LLMError(f"anthropic HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    text = "".join(b.get("text", "") for b in data.get("content", []))
    usage = data.get("usage", {})
    return text, _cost(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0))


def _openrouter(system: str, user: str, max_tokens: int) -> tuple[str, float]:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise LLMError("OPENROUTER_API_KEY not set")
    model = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-5")
    fallbacks = [m.strip() for m in os.environ.get("LLM_FALLBACK_MODELS", "").split(",") if m.strip()]

    body: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "usage": {"include": True},
    }
    if fallbacks:
        # OpenRouter's native failover: tries each in order if the previous errors
        body["models"] = [model, *fallbacks]

    resp = httpx.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {key}"},
        json=body,
        timeout=120,
    )
    if resp.status_code != 200:
        raise LLMError(f"openrouter HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    text = data["choices"][0]["message"]["content"] or ""
    usage = data.get("usage", {})
    # OpenRouter reports actual cost directly when usage.include is set
    cost = usage.get("cost") or _cost(
        data.get("model", model), usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    )
    return text, float(cost)
