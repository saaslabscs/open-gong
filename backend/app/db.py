"""Database setup. SQLite by default (zero external deps); Postgres via DATABASE_URL."""

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _default_url() -> str:
    DATA_DIR.mkdir(exist_ok=True)
    return f"sqlite:///{DATA_DIR / 'opengong.sqlite3'}"


DATABASE_URL = os.environ.get("DATABASE_URL") or _default_url()

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_session() -> Session:
    return SessionLocal()
