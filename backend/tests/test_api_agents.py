"""Agent CRUD API (Task 10 of the agent-skill architecture plan)."""

from fastapi.testclient import TestClient

from app.main import app

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
