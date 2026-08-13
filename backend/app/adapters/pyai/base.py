"""The PyAI adapter seam: one interface, two implementations (mock/real).

Delivery is poll-based so it works without a public URL (PyAI can't reach
localhost). `submit_recording` starts a job; `fetch_transcript` returns the
normalized transcript once ready, or None while it's still running. The
production webhook path (PyAI POSTs job completion to us) reuses the same
`deliver_transcript` sink but is only usable behind a public URL.
"""

import os
from typing import Protocol


class PyAIAdapter(Protocol):
    def submit_recording(self, call_id: str, audio_path: str) -> str:
        """Start transcription. Returns a job ref to poll with fetch_transcript."""
        ...

    def fetch_transcript(self, job_ref: str) -> dict | None:
        """Return {language, lines:[{line,speaker,text}]} when ready, else None
        (still running). Raise on job failure."""
        ...


def webhook_secret() -> str:
    return os.environ.get("PYAI_WEBHOOK_SECRET", "mock-secret")


def get_adapter() -> "PyAIAdapter":
    kind = os.environ.get("PYAI_ADAPTER", "mock")
    if kind == "mock":
        from .mock import MockPyAI

        return MockPyAI()
    if kind == "real":
        from .real import RealPyAI

        return RealPyAI()
    raise ValueError(f"unknown PYAI_ADAPTER {kind!r}")
