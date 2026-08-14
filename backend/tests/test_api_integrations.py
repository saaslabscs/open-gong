"""CRM connection management API (integrations design §4)."""

import httpx
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


CONTACTS = "crm/v3/objects/contacts"
ACCOUNT = "account-info/v3/details"


def _rows() -> list:
    from sqlalchemy import select

    from app.db import get_session
    from app.models import Integration

    with get_session() as session:
        return list(session.scalars(select(Integration)).all())


def test_connect_stores_a_verified_token(crm_http):
    crm_http((CONTACTS, 200, {"results": []}), (ACCOUNT, 200, {"portalId": 42}))

    resp = client.put("/api/integrations/hubspot", json={"token": "pat-na1-goodtoken"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["status"] == "connected"
    assert body["account_label"] == "Portal 42"
    assert body["token_hint"] == "••••oken"
    assert body["last_error"] is None
    assert "goodtoken" not in resp.text

    rows = _rows()
    assert len(rows) == 1
    assert rows[0].access_token == "pat-na1-goodtoken"
    assert rows[0].account_ref == "42"


def test_connect_with_a_rejected_token_persists_nothing(crm_http):
    crm_http((CONTACTS, 401, {"message": "invalid"}))

    resp = client.put("/api/integrations/hubspot", json={"token": "bad"})
    assert resp.status_code == 400
    assert "rejected that token" in resp.json()["detail"]
    assert _rows() == []


def test_connect_rejects_a_blank_token_without_calling_the_provider(monkeypatch):
    import app.integrations.providers as providers

    def explode(request):
        raise AssertionError("a blank token must never reach the provider")

    monkeypatch.setattr(providers, "_TRANSPORT", httpx.MockTransport(explode))

    resp = client.put("/api/integrations/hubspot", json={"token": "   "})
    assert resp.status_code == 400
    assert "required" in resp.json()["detail"]
    assert _rows() == []


def test_reconnecting_replaces_the_token_in_place(crm_http):
    crm_http((CONTACTS, 200, {"results": []}), (ACCOUNT, 200, {"portalId": 42}))
    client.put("/api/integrations/hubspot", json={"token": "pat-na1-first"})
    client.put("/api/integrations/hubspot", json={"token": "pat-na1-second"})

    rows = _rows()
    assert len(rows) == 1
    assert rows[0].access_token == "pat-na1-second"


def test_connect_clears_a_previous_error(crm_http):
    from app.db import get_session
    from app.models import Integration

    with get_session() as session:
        session.add(Integration(provider="pipedrive", access_token="stale", status="error",
                                last_error="Pipedrive rejected that token (401)."))
        session.commit()

    crm_http(("users/me", 200, {"data": {"company_name": "Acme Inc", "company_domain": "acme"}}))
    body = client.put("/api/integrations/pipedrive", json={"token": "fresh-token"}).json()
    assert body["status"] == "connected"
    assert body["last_error"] is None
    assert body["account_label"] == "Acme Inc"


def test_connect_to_an_unavailable_provider_is_refused():
    resp = client.put("/api/integrations/salesforce", json={"token": "anything"})
    assert resp.status_code == 400
    assert "Salesforce" in resp.json()["detail"]
    assert _rows() == []


def test_unknown_provider_is_404():
    assert client.put("/api/integrations/zoho", json={"token": "x"}).status_code == 404
    assert client.delete("/api/integrations/zoho").status_code == 404


def test_disconnect_removes_the_connection(crm_http):
    crm_http((CONTACTS, 200, {"results": []}), (ACCOUNT, 200, {"portalId": 42}))
    client.put("/api/integrations/hubspot", json={"token": "pat-na1-goodtoken"})

    assert client.delete("/api/integrations/hubspot").status_code == 200
    assert _rows() == []
    assert client.get("/api/integrations").json()[0]["connected"] is False
    # Idempotent: disconnecting an absent connection is not an error.
    assert client.delete("/api/integrations/hubspot").status_code == 200


def test_test_marks_a_revoked_token_as_error_but_keeps_the_row(crm_http):
    crm_http((CONTACTS, 200, {"results": []}), (ACCOUNT, 200, {"portalId": 42}))
    client.put("/api/integrations/hubspot", json={"token": "pat-na1-goodtoken"})

    # The token has since been revoked in HubSpot.
    crm_http((CONTACTS, 401, {"message": "invalid"}))
    resp = client.post("/api/integrations/hubspot/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["status"] == "error"
    assert "rejected that token" in body["last_error"]
    # The row survives so the user can see what broke and re-paste a token.
    assert len(_rows()) == 1


def test_test_clears_the_error_once_the_token_works_again(crm_http):
    from app.db import get_session
    from app.models import Integration

    with get_session() as session:
        session.add(Integration(provider="pipedrive", access_token="token", status="error",
                                last_error="Pipedrive rejected that token (401)."))
        session.commit()

    crm_http(("users/me", 200, {"data": {"company_name": "Acme Inc", "company_domain": "acme"}}))
    body = client.post("/api/integrations/pipedrive/test").json()
    assert body["status"] == "connected"
    assert body["last_error"] is None
    assert body["account_label"] == "Acme Inc"


def test_test_on_a_provider_that_is_not_connected_is_404():
    resp = client.post("/api/integrations/hubspot/test")
    assert resp.status_code == 404
    assert "isn’t connected" in resp.json()["detail"]
