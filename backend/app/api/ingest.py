"""Ingestion: upload a call, paste a link, or (later) receive a dialer webhook.

Idempotency: Call.external_id is the SHA-256 of the audio content (uploads)
or the source URL — re-sending the same call returns the existing record
instead of creating a duplicate.
"""

import hashlib
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select

from ..db import DATA_DIR, get_session
from ..jobs import enqueue
from ..models import Call, Run

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

UPLOADS_DIR = DATA_DIR / "uploads"
MAX_AUDIO_BYTES = 200 * 1024 * 1024  # 200 MB
ALLOWED_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".webm"}


def _fresh_stages() -> list[dict]:
    return [{"name": "transcribe", "status": "pending", "attempts": 0, "cost_usd": 0.0, "error": None}]


def _create_call(session, *, title: str, source: str, external_id: str, audio_path: str) -> tuple[Call, bool]:
    existing = session.scalars(select(Call).where(Call.external_id == external_id)).first()
    if existing:
        return existing, False
    call = Call(title=title, source=source, external_id=external_id, audio_path=audio_path)
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
    try:
        with httpx.Client(follow_redirects=True, timeout=60) as client:
            resp = client.get(url)
            resp.raise_for_status()
            content = resp.content
    except httpx.HTTPError as e:
        raise HTTPException(422, f"could not fetch recording: {e}") from e
    if len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "file too large")

    suffix = Path(httpx.URL(url).path).suffix.lower() or ".mp3"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    audio_path = UPLOADS_DIR / f"{digest[:16]}{suffix}"
    audio_path.write_bytes(content)

    with get_session() as session:
        call, created = _create_call(
            session,
            title=Path(httpx.URL(url).path).stem or "Linked call",
            source="url",
            external_id=f"url:{url}",
            audio_path=str(audio_path),
        )
        session.commit()
        call_id = call.id
    if created:
        enqueue("process_call", {"call_id": call_id})
    return {"call_id": call_id, "created": created}
