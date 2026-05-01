"""Phase 7 / Spec §1.2.1 — Contact tagging end-to-end.

Tests `tag_contacts(contacts, client)`:
- Tags `[EXISTS IN HUBSPOT]` with URL when a contact matches by email
- Tags `[EXISTS IN HUBSPOT]` when name+company match (no email)
- Tags `[NET NEW]` when no match
- Returns existing contacts BEFORE net-new contacts (group order)
- URL format `https://app.hubspot.com/contacts/{portal_id}/contact/{contact_id}`
"""
from unittest.mock import MagicMock


def _client_with_email_hits(email_to_id: dict[str, str]):
    """Build a fake client whose email lookup returns matches for given emails."""
    client = MagicMock()

    def search_email(email):
        cid = email_to_id.get(email)
        if cid is None:
            return None
        return {"id": cid, "properties": {"email": email, "firstname": "X", "lastname": "Y"}}

    def search_name(first, last, company):
        return None

    client.search_contact_by_email.side_effect = search_email
    client.search_contact_by_name_company.side_effect = search_name
    return client


def test_tag_contacts_marks_email_match_as_exists_with_url(monkeypatch):
    monkeypatch.setenv("HUBSPOT_PORTAL_ID", "12345678")
    from src.integrations.hubspot.contact_check import tag_contacts

    client = _client_with_email_hits({"jane@kroger.com": "999"})
    contacts = [
        {"first_name": "Jane", "last_name": "Doe", "email": "jane@kroger.com", "company": "Kroger"},
    ]
    result = tag_contacts(contacts, client, portal_id="12345678")
    assert result["warning"] is None
    out = result["contacts"]
    assert len(out) == 1
    assert out[0]["status"] == "EXISTS IN HUBSPOT"
    assert out[0]["hubspot_url"] == "https://app.hubspot.com/contacts/12345678/contact/999"


def test_tag_contacts_marks_no_match_as_net_new():
    from src.integrations.hubspot.contact_check import tag_contacts

    client = _client_with_email_hits({})
    contacts = [
        {"first_name": "Pat", "last_name": "Smith", "email": "pat@unknown.com", "company": "Unknown"},
    ]
    result = tag_contacts(contacts, client, portal_id="abc")
    out = result["contacts"]
    assert len(out) == 1
    assert out[0]["status"] == "NET NEW"
    assert out[0].get("hubspot_url") is None


def test_tag_contacts_falls_back_to_name_company_when_no_email():
    from src.integrations.hubspot.contact_check import tag_contacts

    client = MagicMock()
    client.search_contact_by_email.return_value = None
    client.search_contact_by_name_company.return_value = {
        "id": "555",
        "properties": {"firstname": "Pat", "lastname": "Smith", "company": "Kroger"},
    }
    contacts = [
        {"first_name": "Pat", "last_name": "Smith", "email": "", "company": "Kroger"},
    ]
    result = tag_contacts(contacts, client, portal_id="abc")
    out = result["contacts"]
    assert out[0]["status"] == "EXISTS IN HUBSPOT"
    assert out[0]["hubspot_url"] == "https://app.hubspot.com/contacts/abc/contact/555"
    # Falls through to name+company because email lookup returned None
    client.search_contact_by_name_company.assert_called_once()


def test_tag_contacts_groups_existing_before_net_new():
    """Spec §1.2.1: existing contacts shown first with their HubSpot link,
    net new contacts shown below."""
    from src.integrations.hubspot.contact_check import tag_contacts

    client = _client_with_email_hits({
        "exists1@kroger.com": "1",
        "exists2@kroger.com": "2",
    })
    contacts = [
        {"first_name": "A", "last_name": "A", "email": "new1@k.com", "company": "K"},
        {"first_name": "B", "last_name": "B", "email": "exists1@kroger.com", "company": "K"},
        {"first_name": "C", "last_name": "C", "email": "new2@k.com", "company": "K"},
        {"first_name": "D", "last_name": "D", "email": "exists2@kroger.com", "company": "K"},
    ]
    result = tag_contacts(contacts, client, portal_id="p")
    out = result["contacts"]
    # First two should be EXISTS, last two NET NEW
    assert [c["status"] for c in out] == [
        "EXISTS IN HUBSPOT",
        "EXISTS IN HUBSPOT",
        "NET NEW",
        "NET NEW",
    ]
