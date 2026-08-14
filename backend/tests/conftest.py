import os

os.environ["OPEN_GONG_NO_WORKER"] = "1"  # tests drive the job queue synchronously
os.environ["PYAI_ADAPTER"] = "mock"  # never hit the real API from tests, whatever .env says

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db as db
import app.llm as llm_mod
import app.integrations.providers as providers_mod
from app.api import ingest as ingest_mod
from app.db import Base

from fakes import fake_llm


@pytest.fixture(autouse=True)
def no_network_llm(monkeypatch):
    """Tests never hit a real LLM provider; individual tests override as needed.

    Patched once at the source (app.llm.complete_json) rather than per-module,
    since insights.py and compliance.py both call it via `llm.complete_json(...)`.
    """
    monkeypatch.setattr(llm_mod, "complete_json", fake_llm({}))


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """Every test gets a fresh in-memory SQLite DB."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    Base.metadata.create_all(engine)
    yield engine


@pytest.fixture
def url_ingest(monkeypatch, tmp_path):
    """Point URL ingestion at a temp uploads dir and an injectable transport."""
    monkeypatch.setattr(ingest_mod, "UPLOADS_DIR", tmp_path)

    def install(transport):
        monkeypatch.setattr(ingest_mod, "_TRANSPORT", transport)

    return install


@pytest.fixture
def crm_http(monkeypatch):
    """Point CRM token verification at a fake transport (mirrors url_ingest).

    Routes are (url_fragment, status_code, json_body). A fresh Response is built
    per request so the same route can serve repeated calls.
    """

    def install(*routes: tuple[str, int, dict]):
        def handler(request: httpx.Request) -> httpx.Response:
            for fragment, status, body in routes:
                if fragment in str(request.url):
                    return httpx.Response(status, json=body)
            return httpx.Response(404, json={"message": "no route registered in test"})

        monkeypatch.setattr(providers_mod, "_TRANSPORT", httpx.MockTransport(handler))

    return install
