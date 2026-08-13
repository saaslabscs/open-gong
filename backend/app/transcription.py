"""One place transcripts enter the system, whether delivered by polling
(local dev) or by PyAI's signed webhook (production public URL).

Stores the numbered transcript, marks the transcribe stage ok, and enqueues
the insight chain. Idempotent: a replayed delivery overwrites identical
content and does not double-enqueue insights.
"""

from sqlalchemy import select

from .db import get_session
from .jobs import enqueue
from .models import Call, Run, Transcript


def segments_to_lines(segments: list[dict]) -> list[dict]:
    """PyAI segments → our numbered {line, speaker, text}.

    Segment speaker labels are `speaker_1`, `speaker_2`; prettify to
    `Speaker 1`, `Speaker 2` (name assignment is a later, optional step).
    """
    lines = []
    for i, seg in enumerate(segments, start=1):
        raw = seg.get("speaker") or "speaker_1"
        speaker = raw.replace("speaker_", "Speaker ").replace("_", " ").strip()
        lines.append({"line": i, "speaker": speaker, "text": (seg.get("text") or "").strip()})
    return lines


def update_transcript_lines(call_id: str, lines: list[dict]) -> None:
    """Replace the stored transcript lines (e.g. after prettifying)."""
    with get_session() as session:
        call = session.get(Call, call_id)
        if call and call.transcript:
            call.transcript.lines = lines
            session.commit()


def deliver_transcript(call_id: str, transcript: dict) -> bool:
    """Store a normalized transcript ({language, lines}). Returns True if this
    was the first delivery (i.e. it enqueued the insight chain)."""
    with get_session() as session:
        call = session.get(Call, call_id)
        if call is None:
            raise ValueError(f"unknown call {call_id}")

        first = call.transcript is None
        if first:
            session.add(
                Transcript(
                    call_id=call.id,
                    language=transcript.get("language", "en"),
                    lines=transcript["lines"],
                )
            )
        else:
            call.transcript.language = transcript.get("language", "en")
            call.transcript.lines = transcript["lines"]

        run = session.scalars(
            select(Run).where(Run.call_id == call.id).order_by(Run.created_at.desc())
        ).first()
        if run:
            stages = [dict(s) for s in run.stages]
            for s in stages:
                if s["name"] == "transcribe":
                    s["status"] = "ok"
                    s["attempts"] = max(1, s["attempts"])
                    s["error"] = None
            run.stages = stages
        session.commit()

    if first:
        enqueue("run_insights", {"call_id": call_id})
    return first
