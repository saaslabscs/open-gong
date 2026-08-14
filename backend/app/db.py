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


# (table, column, type) for every column added after that table already existed
# in a shipped database. Append here whenever a model gains a column.
_ADDED_COLUMNS = [("runs", "insights", "JSON"), ("agent_runs", "routing_reasoning", "TEXT")]


def ensure_columns(engine) -> None:
    """No migration tooling here: add columns create_all() cannot add to an existing table.

    Columns introduced after a database was first created are invisible to
    create_all, so an upgrade would otherwise fail with "no such column" on
    every read path. Idempotent; skips non-SQLite backends.
    """
    if not engine.url.get_backend_name().startswith("sqlite"):
        return
    with engine.begin() as con:
        for table, col, type_ in _ADDED_COLUMNS:
            existing = {r[1] for r in con.exec_driver_sql(f"PRAGMA table_info({table})")}
            if existing and col not in existing:
                con.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {type_}")
