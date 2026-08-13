"""Open Gong API.

DB-backed from M2 on. First boot auto-seeds the five sample calls so
`make demo` shows a populated app with zero keys and zero manual steps.
"""

import asyncio
import os
from contextlib import asynccontextmanager

from . import config  # noqa: F401 — loads backend/.env before anything reads env vars

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from . import pipeline  # noqa: F401 — registers job handlers
from .api.ingest import router as ingest_router
from .api.packs import router as packs_router
from .api.review import router as review_router
from .api.share import router as share_router
from .api.webhooks import router as webhooks_router
from .db import Base, engine, get_session
from .jobs import worker_loop
from .models import Call, Run


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    with get_session() as session:
        empty = session.scalars(select(Call).limit(1)).first() is None
    if empty:
        from scripts.seed import seed

        seed()
    # Tests drive the queue synchronously via run_due_jobs(); a background
    # worker there would race across the shared in-memory DB.
    worker = None
    if os.environ.get("OPEN_GONG_NO_WORKER") != "1":
        worker = asyncio.create_task(worker_loop())
    yield
    if worker is not None:
        worker.cancel()


app = FastAPI(title="Open Gong", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest_router)
app.include_router(webhooks_router)
app.include_router(review_router)
app.include_router(share_router)
app.include_router(packs_router)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/api/status")
def api_status() -> dict:
    from .setup_env import status

    return status()


def _latest_run(session, call_id: str) -> Run | None:
    return session.scalars(
        select(Run).where(Run.call_id == call_id).order_by(Run.created_at.desc())
    ).first()


@app.get("/api/calls")
def list_calls() -> list[dict]:
    with get_session() as session:
        calls = session.scalars(select(Call).order_by(Call.created_at)).all()
        out = []
        for c in calls:
            run = _latest_run(session, c.id)
            intent = None
            if run and run.insights:
                intent = run.insights.get("intent", {}).get("value")
            out.append(
                {
                    "id": c.id,
                    "title": c.title,
                    "source": c.source,
                    "duration_s": c.duration_s,
                    "recorded_at": c.recorded_at.isoformat(),
                    "intent": intent,
                    "run_status": run.status if run else "pending",
                }
            )
        return out


@app.get("/api/calls/{call_id}")
def get_call(call_id: str) -> dict:
    with get_session() as session:
        call = session.get(Call, call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="call not found")
        run = _latest_run(session, call_id)
        return {
            "call": {
                "id": call.id,
                "title": call.title,
                "source": call.source,
                "duration_s": call.duration_s,
                "recorded_at": call.recorded_at.isoformat(),
                "participants": _participants(call),
            },
            "run": {
                "status": run.status if run else "pending",
                "stages": run.stages if run else [],
                "edited": bool(run.edited_insights) if run else False,
            },
            "transcript": (
                {"language": call.transcript.language, "lines": call.transcript.lines}
                if call.transcript
                else None
            ),
            # effective insights: human edits win over AI output for display
            "insights": (run.edited_insights or run.insights) if run else None,
            "compliance": run.compliance if run else None,
        }


def _participants(call: Call) -> list[str]:
    if not call.transcript:
        return []
    return list(dict.fromkeys(l["speaker"] for l in call.transcript.lines))
