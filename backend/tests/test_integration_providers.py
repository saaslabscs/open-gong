"""Token verification for each CRM provider (integrations design §1, §2)."""

import httpx
import pytest

import app.integrations.providers as providers
from app.integrations.providers import BY_KEY, PROVIDERS, verify_hubspot, verify_pipedrive

CONTACTS = "crm/v3/objects/contacts"
ACCOUNT = "account-info/v3/details"
PIPEDRIVE_ME = "users/me"


def test_registry_lists_three_providers_in_order():
    assert [p.key for p in PROVIDERS] == ["hubspot", "pipedrive", "salesforce"]
    assert BY_KEY["hubspot"].available is True
    # Salesforce is a placeholder: nothing to connect with, so nothing to verify.
    assert BY_KEY["salesforce"].available is False
    assert BY_KEY["salesforce"].verify is None


def test_hubspot_verify_ok_uses_portal_id_as_label(crm_http):
    crm_http(
        (CONTACTS, 200, {"results": []}),
        (ACCOUNT, 200, {"portalId": 12345678}),
    )
    result = verify_hubspot("pat-na1-good")
    assert result.ok is True
    assert result.account_ref == "12345678"
    assert result.account_label == "Portal 12345678"
    assert result.error is None


def test_hubspot_verify_ok_without_account_info_scope(crm_http):
    """A private app may lack the account-info scope. A missing cosmetic label
    must not fail an otherwise-working connection."""
    crm_http(
        (CONTACTS, 200, {"results": []}),
        (ACCOUNT, 403, {"message": "missing scope"}),
    )
    result = verify_hubspot("pat-na1-good")
    assert result.ok is True
    assert result.account_label is None
    assert result.account_ref is None


def test_hubspot_verify_ok_despite_account_info_network_error(monkeypatch):
    """A transport-level error on account-info must not fail the connection.
    The label is best-effort; a network blip fetching it doesn't invalidate
    a token that proved good on the contacts endpoint."""
    def handler(request: httpx.Request) -> httpx.Response:
        if CONTACTS in str(request.url):
            return httpx.Response(200, json={"results": []})
        if ACCOUNT in str(request.url):
            raise httpx.ConnectError("connection reset by peer")
        return httpx.Response(404, json={"message": "no route registered in test"})

    monkeypatch.setattr(providers, "_TRANSPORT", httpx.MockTransport(handler))
    result = verify_hubspot("pat-na1-good")
    assert result.ok is True
    assert result.account_label is None
    assert result.account_ref is None


def test_hubspot_verify_rejects_bad_token(crm_http):
    crm_http((CONTACTS, 401, {"message": "invalid token"}))
    result = verify_hubspot("nope")
    assert result.ok is False
    assert "HubSpot" in result.error
    assert "401" in result.error


def test_pipedrive_verify_ok_captures_company(crm_http):
    crm_http(
        (PIPEDRIVE_ME, 200, {"data": {"company_name": "Acme Inc", "company_domain": "acme"}}),
    )
    result = verify_pipedrive("token")
    assert result.ok is True
    assert result.account_label == "Acme Inc"
    assert result.account_ref == "acme"


def test_pipedrive_verify_rejects_bad_token(crm_http):
    crm_http((PIPEDRIVE_ME, 401, {"error": "unauthorized"}))
    result = verify_pipedrive("nope")
    assert result.ok is False
    assert "Pipedrive" in result.error


@pytest.mark.parametrize("verify", [verify_hubspot, verify_pipedrive])
def test_verify_reports_unreachable_provider(monkeypatch, verify):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(providers, "_TRANSPORT", httpx.MockTransport(boom))
    result = verify("token")
    assert result.ok is False
    assert "couldn't reach" in result.error


def test_verify_never_echoes_the_token(crm_http):
    crm_http((CONTACTS, 401, {"message": "invalid token"}))
    secret = "pat-na1-supersecret"
    result = verify_hubspot(secret)
    assert secret not in (result.error or "")
