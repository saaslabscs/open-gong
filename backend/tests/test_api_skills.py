"""Skill CRUD + upload API (Task 10 of the agent-skill architecture plan)."""

from fastapi.testclient import TestClient

from app.main import app

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
