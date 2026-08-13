import os

os.environ["OPEN_GONG_NO_WORKER"] = "1"  # tests drive the job queue synchronously
os.environ["PYAI_ADAPTER"] = "mock"  # never hit the real API from tests, whatever .env says

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db as db
import app.llm as llm_mod
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
