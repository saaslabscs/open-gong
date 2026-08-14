"""Which CRMs Open Gong can connect to, and how to prove a token works.

See docs/superpowers/specs/2026-08-14-integrations-design.md §1, §2.

Verification is deliberately a real API call the sync feature will also need
(HubSpot: read a contact) rather than a generic whoami — that way a token
missing the scope fails here, at connect time, instead of at first sync.
"""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

import httpx

# Tests inject an httpx.MockTransport here; None means a real network client.
_TRANSPORT: httpx.BaseTransport | None = None
_TIMEOUT = 15

HUBSPOT_API = "https://api.hubapi.com"
PIPEDRIVE_API = "https://api.pipedrive.com/v1"


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    account_label: str | None = None
    account_ref: str | None = None
    error: str | None = None


VerifyFn = Callable[[str], VerifyResult]


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    blurb: str
    available: bool
    token_label: str | None = None
    docs_url: str | None = None
    # A tuple default is immutable, so it needs no dataclasses.field wrapper.
    setup_steps: tuple[str, ...] = ()
    verify: VerifyFn | None = None


def _client() -> httpx.Client:
    return httpx.Client(transport=_TRANSPORT, timeout=_TIMEOUT)


def _rejection(resp: httpx.Response, label: str) -> str:
    """A message that helps without ever quoting the token back."""
    if resp.status_code in (401, 403):
        return (
            f"{label} rejected that token ({resp.status_code}). Check it was copied "
            f"in full and has read access to contacts."
        )
    return f"{label} returned HTTP {resp.status_code}."


def verify_hubspot(token: str) -> VerifyResult:
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with _client() as client:
            resp = client.get(
                f"{HUBSPOT_API}/crm/v3/objects/contacts",
                params={"limit": 1},
                headers=headers,
            )
            if resp.status_code >= 300:
                return VerifyResult(ok=False, error=_rejection(resp, "HubSpot"))
            # Best-effort label. HubSpot's account-info endpoint needs its own
            # scope and only exposes a portal id, so a private app without that
            # scope still connects — just without a friendly name.
            label = ref = None
            with suppress(httpx.HTTPError):
                info = client.get(f"{HUBSPOT_API}/account-info/v3/details", headers=headers)
                if info.status_code < 300:
                    portal = info.json().get("portalId")
                    if portal:
                        ref = str(portal)
                        label = f"Portal {ref}"
            return VerifyResult(ok=True, account_label=label, account_ref=ref)
    except httpx.HTTPError as e:
        return VerifyResult(ok=False, error=f"couldn’t reach HubSpot: {e}")


def verify_pipedrive(token: str) -> VerifyResult:
    try:
        with _client() as client:
            resp = client.get(f"{PIPEDRIVE_API}/users/me", headers={"x-api-token": token})
    except httpx.HTTPError as e:
        return VerifyResult(ok=False, error=f"couldn’t reach Pipedrive: {e}")
    if resp.status_code >= 300:
        return VerifyResult(ok=False, error=_rejection(resp, "Pipedrive"))
    data = resp.json().get("data") or {}
    return VerifyResult(
        ok=True,
        account_label=data.get("company_name"),
        # Pipedrive's API is per-company-subdomain; sync will need this.
        account_ref=data.get("company_domain"),
    )


PROVIDERS: tuple[Provider, ...] = (
    Provider(
        key="hubspot",
        label="HubSpot",
        blurb="Log call notes and insights against your HubSpot contacts.",
        available=True,
        token_label="Private app access token",
        docs_url="https://app.hubspot.com/private-apps",
        setup_steps=(
            "In HubSpot, open Settings → Integrations → Private Apps.",
            "Create a private app (or open an existing one) and grant it the "
            "crm.objects.contacts.read scope.",
            "Copy the access token from the app’s Auth tab and paste it below.",
        ),
        verify=verify_hubspot,
    ),
    Provider(
        key="pipedrive",
        label="Pipedrive",
        blurb="Log call notes and insights against your Pipedrive people.",
        available=True,
        token_label="Personal API token",
        docs_url="https://app.pipedrive.com/settings/api",
        setup_steps=(
            "In Pipedrive, open Personal preferences → API.",
            "Copy your personal API token and paste it below.",
        ),
        verify=verify_pipedrive,
    ),
    Provider(
        key="salesforce",
        label="Salesforce",
        blurb="Needs an OAuth connected app — not available yet.",
        available=False,
    ),
)

BY_KEY: dict[str, Provider] = {p.key: p for p in PROVIDERS}
