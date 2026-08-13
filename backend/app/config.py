"""Environment loading: reads backend/.env once at import time.

Values stay in os.environ so existing os.environ.get() call sites
(run_state, adapters) keep working unchanged.
"""

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env() -> None:
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and value and key not in os.environ:  # real env vars win over .env
            os.environ[key] = value


load_env()
