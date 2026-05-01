"""Phase 7 / Spec §1.2.1 — Fallback when HubSpot is down.

If HubSpot returns 5xx (or raises), the bot must:
- Return the contact list (untagged or marked unverified)
- Attach a warning banner: "HubSpot check unavailable — showing unverified contacts"
- NOT raise / fail the whole research run
"""
from unittest.mock import MagicMock

import httpx


def test_returns_warning_banner_when_client_raises():
    from src.integrations.hubspot.contact_check import tag_contacts

    client = MagicMock()
    # Simulate HubSpot raising (network down, 5xx, etc.)
    client.search_contact_by_email.side_effect = httpx.HTTPStatusError(
        "500 server error",
        request=MagicMock(),
        response=MagicMock(status_code=503),
    )
    client.search_contact_by_name_company.side_effect = httpx.HTTPStatusError(
        "500 server error",
        request=MagicMock(),
        response=MagicMock(status_code=503),
    )

    contacts = [
        {"first_name": "Jane", "last_name": "Doe", "email": "j@k.com", "company": "K"},
        {"first_name": "Pat", "last_name": "Smith", "email": "p@k.com", "company": "K"},
    ]

    result = tag_contacts(contacts, client, portal_id="p")
    # Did not raise — returned a result
    assert result is not None
    # Contacts still surfaced
    assert len(result["contacts"]) == 2
    # Warning banner present
    assert result["warning"] is not None
    assert "HubSpot" in result["warning"]
    assert "unavailable" in result["warning"].lower()


def test_returns_warning_when_client_returns_5xx_via_generic_exception():
    from src.integrations.hubspot.contact_check import tag_contacts

    client = MagicMock()
    client.search_contact_by_email.side_effect = RuntimeError("HubSpot 503 Service Unavailable")
    client.search_contact_by_name_company.side_effect = RuntimeError("HubSpot 503 Service Unavailable")

    contacts = [
        {"first_name": "Jane", "last_name": "Doe", "email": "j@k.com", "company": "K"},
    ]
    result = tag_contacts(contacts, client, portal_id="p")
    assert result["warning"] is not None
    assert len(result["contacts"]) == 1
