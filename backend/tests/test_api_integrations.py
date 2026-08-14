"""CRM connection management API (integrations design §4)."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_catalog_lists_every_provider_unconnected():
    resp = client.get("/api/integrations")
    assert resp.status_code == 200
    items = resp.json()
    assert [i["key"] for i in items] == ["hubspot", "pipedrive", "salesforce"]

    hubspot = items[0]
    assert hubspot["label"] == "HubSpot"
    assert hubspot["available"] is True
    assert hubspot["connected"] is False
    assert hubspot["status"] is None
    assert hubspot["token_hint"] is None
    # The setup steps are the point of the card — they must reach the client.
    assert len(hubspot["setup_steps"]) == 3
    assert hubspot["docs_url"].startswith("https://")

    assert items[2]["available"] is False
    assert items[2]["setup_steps"] == []


def test_catalog_never_exposes_a_stored_token():
    from app.db import get_session
    from app.models import Integration

    with get_session() as session:
        session.add(Integration(provider="hubspot", access_token="pat-na1-supersecret"))
        session.commit()

    resp = client.get("/api/integrations")
    assert "supersecret" not in resp.text
    assert "access_token" not in resp.text
    hubspot = resp.json()[0]
    assert hubspot["connected"] is True
    assert hubspot["token_hint"] == "••••cret"
