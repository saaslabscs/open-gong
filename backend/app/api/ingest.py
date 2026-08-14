"""Ingestion: upload a call, paste a link, or (later) receive a dialer webhook.

Idempotency: Call.external_id is the SHA-256 of the audio content (uploads)
or the source URL — re-sending the same call returns the existing record
instead of creating a duplicate.

Downloads are checked against the length the source declares, in the HTTP
headers *and* in the audio container itself. A partial body used to be accepted
without complaint, so a five-minute recording became a one-line transcript with
the transcribe stage still reporting "ok".
"""

import hashlib
import struct
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select

from ..db import DATA_DIR, get_session
from ..jobs import enqueue
from ..models import Call, Run
from ..run_state import STAGES

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

UPLOADS_DIR = DATA_DIR / "uploads"
MAX_AUDIO_BYTES = 200 * 1024 * 1024  # 200 MB
ALLOWED_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".webm"}

# A recording endpoint that streams progressively for a browser may hand a
# client that never asks for a byte range only the first chunk. Ask explicitly,
# and look like the browser that gets served the whole thing.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
_HEAD_BYTES = 64  # covers a RIFF/WAVE header and every magic number we sniff

# Tests inject an httpx.MockTransport here; None means a real network client.
_TRANSPORT: httpx.BaseTransport | None = None


class IncompleteDownload(Exception):
    """The fetched body was shorter than the source said it would be."""


def _fresh_stages() -> list[dict]:
    return [
        {"name": s, "status": "pending", "attempts": 0, "cost_usd": 0.0, "error": None}
        for s in STAGES
    ]


# --- audio container introspection -----------------------------------------


def _riff_total(head: bytes) -> int | None:
    """Total file size a RIFF header declares, or None if unknown/not RIFF.

    Encoders writing a WAV to a stream cannot know the final length, so they
    leave the size field as 0 or 0xFFFFFFFF. Those are placeholders, not claims —
    treating them as a length would reject perfectly good audio.
    """
    if len(head) < 8 or head[:4] != b"RIFF":
        return None
    declared = struct.unpack("<I", head[4:8])[0]
    if declared in (0, 0xFFFFFFFF):
        return None
    return declared + 8


def _sniff_suffix(head: bytes) -> str | None:
    """Container extension from magic bytes. The dialer's /stream/ URLs carry no
    extension, and guessing from the path stored WAV files as .mp3."""
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    if head[:4] == b"OggS":
        return ".ogg"
    if head[:4] == b"fLaC":
        return ".flac"
    if head[4:8] == b"ftyp":
        return ".m4a"
    if head[:3] == b"ID3":
        return ".mp3"
    if head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    if head[:2] in (b"\xff\xf1", b"\xff\xf9"):
        return ".aac"
    return None


def _wav_duration_s(head: bytes, size: int) -> int | None:
    """Seconds of audio in a RIFF/WAVE file, from its declared byte rate.

    Only WAV — no probe binary, no new dependency. Other containers keep None,
    as they did before. Worth having: a stored duration of 8s on a 5-minute call
    is the signal that would have surfaced the truncation immediately.
    """
    if len(head) < 32 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return None
    byte_rate = struct.unpack("<I", head[28:32])[0]
    if byte_rate <= 0:
        return None
    return max(0, round((size - 44) / byte_rate))


def _assert_container_complete(head: bytes, size: int) -> None:
    declared = _riff_total(head)
    if declared is not None and size < declared:
        raise IncompleteDownload(
            f"audio is truncated: {size:,} bytes received, container declares {declared:,}"
        )


def _resolve_suffix(head: bytes, fallback: str) -> str:
    suffix = _sniff_suffix(head) or fallback
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(422, f"unsupported audio type {suffix!r}")
    return suffix


# --- fetching ---------------------------------------------------------------


def _expected_total(headers: httpx.Headers) -> int | None:
    """Full object size: from Content-Range on a 206, else Content-Length."""
    content_range = headers.get("content-range")
    if content_range and "/" in content_range:
        total = content_range.rsplit("/", 1)[1].strip()
        if total.isdigit():
            return int(total)
    length = headers.get("content-length")
    if length and length.isdigit():
        return int(length)
    return None


def _fetch_recording(url: str, dest: Path) -> tuple[int, bytes]:
    """Download `url` into `dest`, resuming until the declared length is met.

    Returns (bytes written, leading bytes). Streams to disk rather than holding
    the whole body in memory, and enforces MAX_AUDIO_BYTES as it goes instead of
    after buffering up to 200 MB.
    """
    written = 0
    expected: int | None = None
    head = bytearray()

    with httpx.Client(
        transport=_TRANSPORT, follow_redirects=True, timeout=60
    ) as client, dest.open("wb") as fh:
        while True:
            headers = {"User-Agent": _BROWSER_UA, "Range": f"bytes={written}-"}
            with client.stream("GET", url, headers=headers) as resp:
                if written and resp.status_code == 200:
                    # Range ignored: the body restarts at byte 0, so there is
                    # nothing safe to append. This is the truncation case.
                    got = f"{written:,} of {expected:,}" if expected else f"{written:,}"
                    raise IncompleteDownload(
                        f"source ignores range requests; stopped at {got} bytes"
                    )
                resp.raise_for_status()
                if expected is None:
                    expected = _expected_total(resp.headers)

                chunk_bytes = 0
                for chunk in resp.iter_bytes():
                    if len(head) < _HEAD_BYTES:
                        head.extend(chunk[: _HEAD_BYTES - len(head)])
                    fh.write(chunk)
                    chunk_bytes += len(chunk)
                    written += len(chunk)
                    if written > MAX_AUDIO_BYTES:
                        raise HTTPException(413, "file too large")

            # The container is more trustworthy than the headers — a source that
            # under-reports Content-Length is exactly how this went unnoticed.
            declared = _riff_total(bytes(head))
            target = max(t for t in (expected, declared) if t is not None) if (
                expected is not None or declared is not None
            ) else None

            if target is None or written >= target:
                return written, bytes(head)
            if chunk_bytes == 0:
                raise IncompleteDownload(
                    f"download stalled at {written:,} of {target:,} bytes"
                )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# --- call creation ----------------------------------------------------------


def _create_call(
    session,
    *,
    title: str,
    source: str,
    external_id: str,
    audio_path: str,
    duration_s: int | None = None,
) -> tuple[Call, bool]:
    existing = session.scalars(select(Call).where(Call.external_id == external_id)).first()
    if existing:
        return existing, False
    call = Call(
        title=title,
        source=source,
        external_id=external_id,
        audio_path=audio_path,
        duration_s=duration_s,
    )
    session.add(call)
    session.flush()
    session.add(Run(call_id=call.id, status="running", stages=_fresh_stages()))
    return call, True


@router.post("/upload")
async def upload(file: UploadFile) -> dict:
    suffix = Path(file.filename or "audio.wav").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(422, f"unsupported audio type {suffix!r}")
    content = await file.read()
    if len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "file too large")

    head = content[:_HEAD_BYTES]
    try:
        _assert_container_complete(head, len(content))
    except IncompleteDownload as e:
        raise HTTPException(422, str(e)) from e

    digest = hashlib.sha256(content).hexdigest()
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    audio_path = UPLOADS_DIR / f"{digest[:16]}{suffix}"
    audio_path.write_bytes(content)

    with get_session() as session:
        call, created = _create_call(
            session,
            title=Path(file.filename or "Uploaded call").stem,
            source="upload",
            external_id=f"sha256:{digest}",
            audio_path=str(audio_path),
            duration_s=_wav_duration_s(head, len(content)),
        )
        session.commit()
        call_id = call.id
    if created:
        enqueue("process_call", {"call_id": call_id})
    return {"call_id": call_id, "created": created}


class UrlIngest(BaseModel):
    url: HttpUrl


@router.post("/url")
def ingest_url(body: UrlIngest) -> dict:
    url = str(body.url)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    staging = UPLOADS_DIR / f".incoming-{uuid.uuid4().hex}"

    try:
        size, head = _fetch_recording(url, staging)
        _assert_container_complete(head, size)  # belt and braces after the loop
        suffix = _resolve_suffix(head, Path(httpx.URL(url).path).suffix.lower() or ".mp3")
        audio_path = UPLOADS_DIR / f"{_sha256_file(staging)[:16]}{suffix}"
        staging.replace(audio_path)
    except httpx.HTTPError as e:
        raise HTTPException(422, f"could not fetch recording: {e}") from e
    except IncompleteDownload as e:
        raise HTTPException(502, f"could not fetch the whole recording: {e}") from e
    finally:
        staging.unlink(missing_ok=True)

    with get_session() as session:
        call, created = _create_call(
            session,
            title=Path(httpx.URL(url).path).stem or "Linked call",
            source="url",
            external_id=f"url:{url}",
            audio_path=str(audio_path),
            duration_s=_wav_duration_s(head, size),
        )
        session.commit()
        call_id = call.id
    if created:
        enqueue("process_call", {"call_id": call_id})
    return {"call_id": call_id, "created": created}
