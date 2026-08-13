"""Mock PyAI: returns fixture transcripts, ready immediately. Offline, free,
and the permanent CI fixture. Selects a fixture by the audio filename stem
(e.g. sample-03.wav → sample-03-*.json), else a small canned transcript.
"""

import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures" / "samples"

CANNED = {
    "language": "en",
    "lines": [
        {"line": 1, "speaker": "Agent", "text": "Hi, thanks for calling — this call is recorded for quality. How can I help today?"},
        {"line": 2, "speaker": "Customer", "text": "Hey, I wanted to ask about upgrading our plan before the end of the month."},
        {"line": 3, "speaker": "Agent", "text": "Happy to help. You're on the starter plan at forty dollars a month right now, correct?"},
        {"line": 4, "speaker": "Customer", "text": "That's right. We need at least ten more seats, but the pricing page confused me."},
        {"line": 5, "speaker": "Agent", "text": "I'll send you a side-by-side quote today, and we can walk through it Thursday if that works."},
        {"line": 6, "speaker": "Customer", "text": "Thursday works. Send it over."},
    ],
}


class MockPyAI:
    def submit_recording(self, call_id: str, audio_path: str) -> str:
        # encode the audio stem in the ref so fetch can pick the right fixture
        return f"mock:{Path(audio_path).stem}"

    def fetch_transcript(self, job_ref: str) -> dict | None:
        stem = job_ref.split(":", 1)[1] if ":" in job_ref else ""
        for fixture in FIXTURES_DIR.glob(f"{stem}-*.json"):
            return json.loads(fixture.read_text())["transcript"]
        return CANNED
