"""Agent CRUD API (Task 10 of the agent-skill architecture plan)."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_session
from app.main import app
from app.models import EntryRule

client = TestClient(app)


def test_create_and_list_agent():
    resp = client.post("/api/agents", json={
        "name": "QA Coach", "description": "Assesses rep skill", "system_prompt": "Be strict.",
    })
    assert resp.status_code == 200
    agent_id = resp.json()["id"]

    listed = client.get("/api/agents").json()
    assert any(a["id"] == agent_id and a["name"] == "QA Coach" for a in listed)


def test_get_single_agent():
    created = client.post("/api/agents", json={"name": "a", "description": "d", "system_prompt": "p"}).json()
    fetched = client.get(f"/api/agents/{created['id']}").json()
    assert fetched["name"] == "a"
    assert fetched["skills"] == []


def test_update_agent():
    created = client.post("/api/agents", json={"name": "a", "description": "d", "system_prompt": "p"}).json()
    resp = client.patch(f"/api/agents/{created['id']}", json={"system_prompt": "Be strict and cite examples."})
    assert resp.status_code == 200
    assert resp.json()["system_prompt"] == "Be strict and cite examples."


def test_delete_agent():
    created = client.post("/api/agents", json={"name": "a", "description": "d", "system_prompt": "p"}).json()
    resp = client.delete(f"/api/agents/{created['id']}")
    assert resp.status_code == 200
    assert client.get(f"/api/agents/{created['id']}").status_code == 404


def test_delete_agent_removes_entry_rules():
    """An EntryRule pinned to the agent is a real FK. Nothing creates entry
    rules through the UI yet, so this is inserted directly — deleting the
    agent must clear the rule rather than leaving a row pointing at a missing
    agent (which would 500 on Postgres and mis-route dispatch on SQLite)."""
    agent = client.post("/api/agents", json={"name": "a", "description": "d", "system_prompt": "p"}).json()
    with get_session() as session:
        session.add(EntryRule(match_kind="source", match_value="upload", agent_id=agent["id"]))
        session.commit()

    assert client.delete(f"/api/agents/{agent['id']}").status_code == 200
    with get_session() as session:
        assert session.scalars(select(EntryRule).where(EntryRule.agent_id == agent["id"])).all() == []


def test_attach_and_detach_skill():
    agent = client.post("/api/agents", json={"name": "a", "description": "d", "system_prompt": "p"}).json()
    skill = client.post("/api/skills", json={
        "name": "s", "description": "d", "when_to_use": "w", "body_md": "b", "fields": None,
    }).json()

    attach = client.post(f"/api/agents/{agent['id']}/skills", json={"skill_id": skill["id"]})
    assert attach.status_code == 200
    fetched = client.get(f"/api/agents/{agent['id']}").json()
    assert fetched["skills"][0]["id"] == skill["id"]

    detach = client.delete(f"/api/agents/{agent['id']}/skills/{skill['id']}")
    assert detach.status_code == 200
    fetched = client.get(f"/api/agents/{agent['id']}").json()
    assert fetched["skills"] == []


def test_get_missing_agent_404s():
    assert client.get("/api/agents/does-not-exist").status_code == 404
