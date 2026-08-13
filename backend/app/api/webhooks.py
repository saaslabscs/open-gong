"""Signed webhook receiver for PyAI job completion (production public-URL path).

Local dev uses polling instead (PyAI can't reach localhost); this endpoint is
for deployments with a public URL configured as the job's webhook_url. Both
paths funnel through transcription.deliver_transcript.

Accepts either our normalized transcript shape or PyAI's raw job payload
(segments), normalizing the latter.
"""

import hashlib
import hmac

from fastapi import APIRouter, HTTPException, Request

from ..adapters.pyai.base import webhook_secret
from ..transcription import deliver_transcript, segments_to_lines

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/pyai")
async def pyai_webhook(request: Request) -> dict:
    body = await request.body()
    signature = request.headers.get("X-PyAI-Signature", "")
    expected = hmac.new(webhook_secret().encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "bad signature")

    payload = await request.json()
    call_id = payload.get("call_id")
    if not call_id:
        raise HTTPException(422, "missing call_id")

    transcript = payload.get("transcript")
    if transcript is None:
        # PyAI raw job shape: {result: {segments: [...]}}
        segments = (payload.get("result") or {}).get("segments")
        if segments is None:
            raise HTTPException(422, "missing transcript/segments")
        transcript = {"language": "en", "lines": segments_to_lines(segments)}

    try:
        deliver_transcript(call_id, transcript)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return {"ok": True}
