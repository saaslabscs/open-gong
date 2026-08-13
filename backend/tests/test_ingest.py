"""M2/M8 ingestion: upload → poll → transcript delivered, fully offline via
the mock adapter. Plus idempotency and the (production) webhook endpoint.
"""

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.jobs import run_due_jobs
from app.main import app
from app.transcription import segments_to_lines

WAV_HEADER = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00" + b"\x00" * 20


def _drain_jobs(max_rounds: int = 10) -> None:
    """Run the queue until quiet (process_call → poll → run_insights chain)."""
    for _ in range(max_rounds):
        if run_due_jobs() == 0:
            return


def test_upload_to_transcribed_end_to_end():
    with TestClient(app) as c:
        resp = c.post("/api/ingest/upload", files={"file": ("team-sync.wav", WAV_HEADER, "audio/wav")})
        assert resp.status_code == 200
        call_id = resp.json()["call_id"]
        assert resp.json()["created"] is True

        _drain_jobs()

        detail = c.get(f"/api/calls/{call_id}").json()
        assert detail["transcript"] is not None
        assert detail["transcript"]["lines"][0]["speaker"]
        transcribe = next(s for s in detail["run"]["stages"] if s["name"] == "transcribe")
        assert transcribe["status"] == "ok"


def test_duplicate_upload_is_idempotent():
    with TestClient(app) as c:
        r1 = c.post("/api/ingest/upload", files={"file": ("a.wav", WAV_HEADER, "audio/wav")})
        r2 = c.post("/api/ingest/upload", files={"file": ("a.wav", WAV_HEADER, "audio/wav")})
        assert r1.json()["call_id"] == r2.json()["call_id"]
        assert r2.json()["created"] is False


def test_unsupported_file_type_rejected():
    with TestClient(app) as c:
        resp = c.post("/api/ingest/upload", files={"file": ("notes.txt", b"hello", "text/plain")})
        assert resp.status_code == 422


# --- production webhook path (public-URL deployments) ----------------------


def test_webhook_rejects_bad_signature():
    with TestClient(app) as c:
        body = json.dumps({"call_id": "x", "transcript": {"lines": []}}).encode()
        resp = c.post(
            "/webhooks/pyai",
            content=body,
            headers={"X-PyAI-Signature": "forged", "Content-Type": "application/json"},
        )
        assert resp.status_code == 401


def test_webhook_accepts_raw_pyai_segments():
    with TestClient(app) as c:
        # create a call first (upload, but don't drain so no transcript yet)
        call_id = c.post("/api/ingest/upload", files={"file": ("w.wav", WAV_HEADER, "audio/wav")}).json()["call_id"]

        payload = {
            "call_id": call_id,
            "result": {"segments": [
                {"speaker": "speaker_1", "text": "hello there"},
                {"speaker": "speaker_2", "text": "hi back"},
            ]},
        }
        body = json.dumps(payload).encode()
        sig = hmac.new(b"mock-secret", body, hashlib.sha256).hexdigest()
        r = c.post("/webhooks/pyai", content=body, headers={"X-PyAI-Signature": sig, "Content-Type": "application/json"})
        assert r.status_code == 200

        detail = c.get(f"/api/calls/{call_id}").json()
        assert detail["transcript"]["lines"] == [
            {"line": 1, "speaker": "Speaker 1", "text": "hello there"},
            {"line": 2, "speaker": "Speaker 2", "text": "hi back"},
        ]


def test_segments_to_lines_prettifies_speakers():
    lines = segments_to_lines([
        {"speaker": "speaker_1", "text": "  a  "},
        {"speaker": "speaker_2", "text": "b"},
    ])
    assert lines == [
        {"line": 1, "speaker": "Speaker 1", "text": "a"},
        {"line": 2, "speaker": "Speaker 2", "text": "b"},
    ]
