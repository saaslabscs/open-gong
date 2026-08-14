"""ensure_columns: the stand-in for migration tooling.

`Run.insights` and `AgentRun.routing_reasoning` were both added to models that
already existed in shipped databases. create_all() cannot add a column to an
existing table, so without this every read path on an upgraded install dies
with "no such column".
"""

from sqlalchemy import create_engine

from app.db import ensure_columns

# The runs/agent_runs schema as it stood before the two columns were added.
OLD_SCHEMA = """
CREATE TABLE runs (id VARCHAR PRIMARY KEY, call_id VARCHAR, status VARCHAR, stages JSON);
CREATE TABLE agent_runs (id VARCHAR PRIMARY KEY, run_id VARCHAR, status VARCHAR, steps JSON);
"""


def _columns(engine, table: str) -> set[str]:
    with engine.begin() as con:
        return {r[1] for r in con.exec_driver_sql(f"PRAGMA table_info({table})")}


def _old_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.sqlite3'}")
    with engine.begin() as con:
        for stmt in OLD_SCHEMA.strip().split(";\n"):
            if stmt.strip():
                con.exec_driver_sql(stmt)
    return engine


def test_adds_the_missing_columns_to_an_existing_database(tmp_path):
    engine = _old_db(tmp_path)
    assert "insights" not in _columns(engine, "runs")
    assert "routing_reasoning" not in _columns(engine, "agent_runs")

    ensure_columns(engine)

    assert "insights" in _columns(engine, "runs")
    assert "routing_reasoning" in _columns(engine, "agent_runs")


def test_is_idempotent(tmp_path):
    engine = _old_db(tmp_path)
    ensure_columns(engine)
    ensure_columns(engine)  # a second boot must not raise "duplicate column name"
    assert "insights" in _columns(engine, "runs")


def test_runs_on_startup(tmp_path, monkeypatch):
    """The lifespan must call it, or the fix ships without ever executing."""
    import app.main as main_mod

    called = []
    monkeypatch.setattr(main_mod, "ensure_columns", lambda engine: called.append(engine))
    from fastapi.testclient import TestClient

    with TestClient(main_mod.app):
        pass
    assert called, "lifespan did not call ensure_columns"


def test_absent_table_is_skipped(tmp_path):
    """A brand-new database has no tables yet when this runs standalone;
    create_all makes them with the columns already present, so there is
    nothing to alter and nothing to fail on."""
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.sqlite3'}")
    ensure_columns(engine)  # must not raise
