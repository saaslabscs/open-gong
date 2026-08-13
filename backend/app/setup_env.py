"""Env helpers for onboarding: read/patch backend/.env and check readiness.

Shared by the `open-gong` CLI (init/doctor) and the /api/status endpoint so the
UI and the terminal agree on what's configured.
"""

import os
from pathlib import Path

import httpx

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"
PYAI_BASE = os.environ.get("PYAI_API_BASE", "https://api.pyai.com/v1")

KEYS = ["LLM_PROVIDER", "LLM_MODEL", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "PYAI_API_KEY", "PYAI_ADAPTER"]


def read_env() -> dict[str, str]:
    """Merge backend/.env with process env (process env wins, as config.py does)."""
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                values[k.strip()] = v.strip()
    for k in KEYS:
        if os.environ.get(k):
            values[k] = os.environ[k]
    return values


def set_env(updates: dict[str, str]) -> None:
    """Idempotently upsert keys in backend/.env, preserving comments/order."""
    if not ENV_FILE.exists():
        ENV_FILE.write_text(ENV_EXAMPLE.read_text() if ENV_EXAMPLE.exists() else "")
    lines = ENV_FILE.read_text().splitlines()
    remaining = dict(updates)
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in remaining:
                out.append(f"{k}={remaining.pop(k)}")
                continue
        out.append(line)
    for k, v in remaining.items():
        out.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(out) + "\n")


def mint_sandbox_key(label: str = "open-gong") -> dict:
    """Try PyAI's no-auth sandbox mint. Returns {ok, key?, reason?}."""
    try:
        r = httpx.post(f"{PYAI_BASE}/sandbox/keys", json={"label": label}, timeout=30)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"network error: {e}"}
    if r.status_code < 300:
        return {"ok": True, "key": r.json()["api_key"]}
    if r.status_code == 429:
        return {"ok": False, "reason": "sandbox quota reached for this network — paste a key instead"}
    return {"ok": False, "reason": f"HTTP {r.status_code}: {r.text[:150]}"}


def _llm_ok(env: dict) -> tuple[bool, str]:
    provider = env.get("LLM_PROVIDER", "anthropic")
    key = "OPENROUTER_API_KEY" if provider == "openrouter" else "ANTHROPIC_API_KEY"
    if env.get(key):
        return True, f"{provider} key set"
    return False, f"{key} missing (needed for insights)"


def status() -> dict:
    """Readiness snapshot for `doctor` and the UI banner."""
    env = read_env()
    llm_ok, llm_msg = _llm_ok(env)
    adapter = env.get("PYAI_ADAPTER", "mock")
    pyai_key = bool(env.get("PYAI_API_KEY"))
    return {
        "llm": {"ok": llm_ok, "detail": llm_msg, "provider": env.get("LLM_PROVIDER", "anthropic")},
        "transcription": {
            "adapter": adapter,
            # mock works with zero keys; real needs a PyAI key
            "ok": adapter == "mock" or pyai_key,
            "detail": "offline mock (samples only)" if adapter == "mock"
            else ("real PyAI" if pyai_key else "PYAI_ADAPTER=real but PYAI_API_KEY missing"),
        },
        # can the user process a NEW upload (not just browse samples)?
        "can_process_uploads": llm_ok and (adapter == "mock" or pyai_key),
    }
