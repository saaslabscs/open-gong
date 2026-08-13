"""Seed the database from the sample fixtures.

Idempotent: re-running updates in place (keyed on Call.external_id).
Usage: uv run python scripts/seed.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db import Base, engine, get_session
from app.models import Call, Run, Transcript

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "samples"


def seed() -> int:
    Base.metadata.create_all(engine)
    count = 0
    with get_session() as session:
        for path in sorted(SAMPLES_DIR.glob("*.json")):
            data = json.loads(path.read_text())
            c = data["call"]
            external_id = f"sample:{c['id']}"

            call = session.scalars(select(Call).where(Call.external_id == external_id)).first()
            if call is None:
                call = Call(id=c["id"], external_id=external_id)
                session.add(call)
            call.title = c["title"]
            call.source = c["source"]
            call.duration_s = c["duration_s"]
            audio = SAMPLES_DIR / "audio" / f"{c['id']}.wav"
            call.audio_path = str(audio) if audio.exists() else None

            if call.transcript is None:
                call.transcript = Transcript(call_id=call.id, lines=[])
            call.transcript.language = data["transcript"]["language"]
            call.transcript.lines = data["transcript"]["lines"]

            run = session.scalars(select(Run).where(Run.call_id == call.id)).first()
            if run is None:
                run = Run(call_id=call.id)
                session.add(run)
            run.status = data["run"]["status"]
            run.stages = data["run"]["stages"]
            run.insights = data["insights"]
            run.compliance = data.get("compliance")
            run.cost_usd = round(sum(s.get("cost_usd", 0) for s in data["run"]["stages"]), 4)

            count += 1
        session.commit()
    return count


if __name__ == "__main__":
    n = seed()
    print(f"seeded {n} sample call(s)")
