"""M2/M8 ingestion: upload → poll → transcript delivered, fully offline via
the mock adapter. Plus idempotency and the (production) webhook endpoint.
"""

import hashlib
import hmac
import json
import struct

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import ingest as ingest_mod
from app.db import get_session
from app.jobs import run_due_jobs
from app.main import app
from app.models import Call
from app.transcription import segments_to_lines

WAV_HEADER = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00" + b"\x00" * 20

# A recording link with no file extension, like the dialer /stream/ URLs that
# exposed the truncation bug.
STREAM_URL = "https://recordings.example.com/voice/stream/CA123/RE456"
WAV_BYTE_RATE = 8000 * 2 * 2  # 8 kHz, stereo, 16-bit — matches _wav() below


def _wav(n_data: int, *, declared_total: int | None = None) -> bytes:
    """A valid RIFF/WAVE file with `n_data` bytes of silence.

    `declared_total` forges the header's size field so a test can simulate a
    body that is shorter than the container says it should be.
    """
    body = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 2, 8000, WAV_BYTE_RATE, 4, 16)
        + b"data"
        + struct.pack("<I", n_data)
        + bytes(n_data)
    )
    total = declared_total if declared_total is not None else len(body) + 8
    return b"RIFF" + struct.pack("<I", total - 8) + body


def _serve(payload: bytes, *, cap: int | None = None, honor_range: bool = True):
    """MockTransport handler serving `payload`, at most `cap` bytes per request.

    With honor_range=False it ignores Range and always restarts from byte 0 —
    the behaviour that silently truncated real downloads.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        rng = request.headers.get("range")
        start = 0
        if honor_range and rng:
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
        chunk = payload[start : start + cap] if cap else payload[start:]
        if honor_range and rng:
            end = start + len(chunk) - 1
            return httpx.Response(
                206,
                content=chunk,
                headers={"content-range": f"bytes {start}-{end}/{len(payload)}"},
            )
        return httpx.Response(200, content=chunk)

    return httpx.MockTransport(handler)


@pytest.fixture
def url_ingest(monkeypatch, tmp_path):
    """Point URL ingestion at a temp uploads dir and an injectable transport."""
    monkeypatch.setattr(ingest_mod, "UPLOADS_DIR", tmp_path)

    def install(transport):
        monkeypatch.setattr(ingest_mod, "_TRANSPORT", transport)

    return install


def _audio_path_of(call_id: str) -> str:
    with get_session() as session:
        return session.scalars(select(Call).where(Call.id == call_id)).one().audio_path


# --- URL ingestion: a partial download must never become a call -------------


def test_truncated_download_is_rejected_when_server_ignores_range(url_ingest):
    """The real bug: 250 KB of a 5-minute recording transcribed as "ok"."""
    payload = _wav(WAV_BYTE_RATE * 250)  # ~250 s of audio, ~8 MB
    url_ingest(_serve(payload, cap=249_999, honor_range=False))

    with TestClient(app) as c:
        before = len(c.get("/api/calls").json())
        resp = c.post("/api/ingest/url", json={"url": STREAM_URL})
        assert resp.status_code == 502, resp.text
        assert "range" in resp.text.lower() or "incomplete" in resp.text.lower()
        # and crucially: no half-transcribed call left behind
        assert len(c.get("/api/calls").json()) == before


def test_range_resume_downloads_the_whole_recording(url_ingest):
    payload = _wav(WAV_BYTE_RATE * 120)
    url_ingest(_serve(payload, cap=249_999, honor_range=True))

    with TestClient(app) as c:
        resp = c.post("/api/ingest/url", json={"url": STREAM_URL})
        assert resp.status_code == 200, resp.text
        saved = _audio_path_of(resp.json()["call_id"])

    from pathlib import Path

    assert Path(saved).read_bytes() == payload


def test_body_shorter_than_its_own_container_header_is_rejected(url_ingest):
    """Server is honest about what it sends, but the WAV says it should be bigger."""
    short = _wav(200_000, declared_total=8_000_000)
    url_ingest(_serve(short, honor_range=True))

    with TestClient(app) as c:
        resp = c.post("/api/ingest/url", json={"url": STREAM_URL})
        assert resp.status_code == 502, resp.text


def test_extension_is_sniffed_from_content_not_the_url(url_ingest):
    """These URLs have no suffix; a WAV must not be stored as .mp3."""
    url_ingest(_serve(_wav(WAV_BYTE_RATE * 3), honor_range=True))

    with TestClient(app) as c:
        resp = c.post("/api/ingest/url", json={"url": STREAM_URL})
        assert resp.status_code == 200, resp.text
        assert _audio_path_of(resp.json()["call_id"]).endswith(".wav")


def test_duration_is_recorded_for_wav(url_ingest):
    url_ingest(_serve(_wav(WAV_BYTE_RATE * 42), honor_range=True))

    with TestClient(app) as c:
        call_id = c.post("/api/ingest/url", json={"url": STREAM_URL}).json()["call_id"]
        assert c.get(f"/api/calls/{call_id}").json()["call"]["duration_s"] == 42


def test_complete_single_response_still_works(url_ingest):
    """The happy path: one response, whole file, no Range dance needed."""
    payload = _wav(WAV_BYTE_RATE * 5)
    url_ingest(_serve(payload, honor_range=False))

    with TestClient(app) as c:
        r1 = c.post("/api/ingest/url", json={"url": STREAM_URL})
        assert r1.status_code == 200, r1.text
        assert r1.json()["created"] is True
        # same link again dedupes rather than re-downloading
        r2 = c.post("/api/ingest/url", json={"url": STREAM_URL})
        assert r2.json()["created"] is False
        assert r2.json()["call_id"] == r1.json()["call_id"]


def test_oversized_download_is_rejected_midstream(url_ingest, monkeypatch):
    monkeypatch.setattr(ingest_mod, "MAX_AUDIO_BYTES", 50_000)
    url_ingest(_serve(_wav(400_000), honor_range=True))

    with TestClient(app) as c:
        assert c.post("/api/ingest/url", json={"url": STREAM_URL}).status_code == 413


def test_truncated_upload_is_rejected(url_ingest):
    """A partial upload is as silent as a partial download was."""
    with TestClient(app) as c:
        resp = c.post(
            "/api/ingest/upload",
            files={"file": ("half.wav", _wav(1000, declared_total=9_000_000), "audio/wav")},
        )
        assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("placeholder", [0, 0xFFFFFFFF])
def test_streaming_wav_with_placeholder_size_is_accepted(url_ingest, placeholder):
    """A WAV written to a stream can't know its length and leaves the size field
    as 0 or 0xFFFFFFFF. That's a placeholder, not a short-file claim."""
    payload = _wav(WAV_BYTE_RATE * 4)
    forged = payload[:4] + struct.pack("<I", placeholder) + payload[8:]
    url_ingest(_serve(forged, honor_range=True))

    with TestClient(app) as c:
        resp = c.post("/api/ingest/url", json={"url": STREAM_URL})
        assert resp.status_code == 200, resp.text


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
