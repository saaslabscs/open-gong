"""Skill CRUD + upload API (Task 10 of the agent-skill architecture plan)."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_session
from app.main import app
from app.models import AgentSkill

client = TestClient(app)

SKILL_MD = """---
name: uploaded-skill
description: A skill uploaded from disk
when_to_use: When testing uploads
fields:
  claims:
    - summary
---

Summarize the call.
"""


def test_create_and_list_skill():
    resp = client.post("/api/skills", json={
        "name": "s", "description": "d", "when_to_use": "w", "body_md": "b", "fields": None,
    })
    assert resp.status_code == 200
    skill_id = resp.json()["id"]
    listed = client.get("/api/skills").json()
    assert any(s["id"] == skill_id for s in listed)


def test_update_skill():
    created = client.post("/api/skills", json={
        "name": "s", "description": "d", "when_to_use": "w", "body_md": "b", "fields": None,
    }).json()
    resp = client.patch(f"/api/skills/{created['id']}", json={"body_md": "New instructions."})
    assert resp.status_code == 200
    assert resp.json()["body_md"] == "New instructions."


def test_delete_skill():
    created = client.post("/api/skills", json={
        "name": "s", "description": "d", "when_to_use": "w", "body_md": "b", "fields": None,
    }).json()
    assert client.delete(f"/api/skills/{created['id']}").status_code == 200
    assert client.get(f"/api/skills/{created['id']}").status_code == 404


def test_delete_skill_removes_agent_attachments():
    """Deleting a skill must take its AgentSkill links with it. Left behind
    they are dangling FK rows — invisible on SQLite, an IntegrityError on
    Postgres — and the owning agent would still count a skill it can't
    resolve."""
    agent = client.post("/api/agents", json={"name": "a", "description": "d", "system_prompt": "p"}).json()
    skill = client.post("/api/skills", json={
        "name": "s", "description": "d", "when_to_use": "w", "body_md": "b", "fields": None,
    }).json()
    client.post(f"/api/agents/{agent['id']}/skills", json={"skill_id": skill["id"]})
    assert client.get(f"/api/agents/{agent['id']}").json()["skills"][0]["id"] == skill["id"]

    assert client.delete(f"/api/skills/{skill['id']}").status_code == 200

    fetched = client.get(f"/api/agents/{agent['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["skills"] == []
    with get_session() as session:  # no orphan link left behind
        assert session.scalars(select(AgentSkill).where(AgentSkill.skill_id == skill["id"])).all() == []


def test_upload_skill_from_md_file():
    resp = client.post(
        "/api/skills/upload",
        files={"file": ("uploaded-skill.md", SKILL_MD, "text/markdown")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "uploaded-skill"
    assert body["source"] == "upload"
    assert body["fields"]["claims"] == ["summary"]


def test_upload_rejects_malformed_skill_md():
    resp = client.post(
        "/api/skills/upload",
        files={"file": ("bad.md", "no frontmatter here", "text/markdown")},
    )
    assert resp.status_code == 422
