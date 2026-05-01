"""Phase 7 security gates.

S1.2.1a — HubSpot SDK exceptions logged as `type(e).__name__` only.
            Token-shaped strings in exception messages must NEVER appear in
            log output or return values.

S1.2.1b — Contact strings rendered to Slack mrkdwn pass through
            `safe_mrkdwn` (strips `<`, `>`, `|`, `&`).

S1.2.2  — URL construction uses urllib.parse.quote on contact_id and
            portal_id. A `contact_id = "../malicious"` does not escape the
            `/contacts/{portal}/contact/` path.
"""
import logging
from unittest.mock import MagicMock


# -----------------------------
# S1.2.1a — exception name only
# -----------------------------

def test_safe_log_exception_logs_type_name_only(caplog):
    from src.security.exception_logger import safe_log_exception

    logger = logging.getLogger("phase7.security")
    caplog.set_level(logging.ERROR, logger="phase7.security")

    try:
        raise ValueError("auth failed: Bearer pat-na1-XXXX-very-secret")
    except ValueError as e:
        safe_log_exception(logger, e, "hubspot lookup failed")

    # Type name appears
    assert any("ValueError" in rec.getMessage() for rec in caplog.records)
    # Token-shaped string does NOT appear in log
    for rec in caplog.records:
        assert "pat-na1" not in rec.getMessage()
        assert "auth failed" not in rec.getMessage()
        assert "Bearer" not in rec.getMessage()


def test_tag_contacts_does_not_leak_token_in_logs_or_return(caplog):
    from src.integrations.hubspot.contact_check import tag_contacts

    caplog.set_level(logging.ERROR)
    client = MagicMock()
    client.search_contact_by_email.side_effect = RuntimeError(
        "HTTP 401: auth failed: Bearer pat-na1-LEAK-XXXX"
    )
    client.search_contact_by_name_company.side_effect = RuntimeError(
        "HTTP 401: auth failed: Bearer pat-na1-LEAK-XXXX"
    )

    contacts = [{"first_name": "A", "last_name": "B", "email": "a@b.com", "company": "C"}]
    result = tag_contacts(contacts, client, portal_id="p")

    # Result should not contain the token string anywhere
    flat = repr(result)
    assert "pat-na1" not in flat
    assert "Bearer" not in flat

    # Log records should not contain the token
    for rec in caplog.records:
        assert "pat-na1" not in rec.getMessage()
        assert "Bearer" not in rec.getMessage()


# ----------------------------------------
# S1.2.1b — safe_mrkdwn applied to HubSpot
# ----------------------------------------

def test_hubspot_strings_pass_through_safe_mrkdwn():
    """When HubSpot returns a poisoned firstname, the rendered Slack output
    must not contain `<`, `>`, or `|` characters."""
    from src.integrations.hubspot.contact_check import render_contact_for_slack

    poisoned_contact = {
        "first_name": "<https://attacker.com|click>",
        "last_name": "Doe",
        "title": "VP of <evil|things>",
        "company": "K&Co",
        "email": "x@y.com",
        "status": "EXISTS IN HUBSPOT",
        "hubspot_url": "https://app.hubspot.com/contacts/123/contact/456",
    }
    rendered = render_contact_for_slack(poisoned_contact)
    assert "<" not in rendered
    assert ">" not in rendered
    assert "|" not in rendered
    # The ampersand from "K&Co" is also stripped by safe_mrkdwn
    assert "&" not in rendered


# -----------------------------
# S1.2.2 — URL encoding of IDs
# -----------------------------

def test_hubspot_url_encodes_contact_id_to_prevent_path_traversal():
    from src.integrations.hubspot.contact_check import build_contact_url

    url = build_contact_url(portal_id="12345678", contact_id="../malicious")
    # Must remain anchored under /contacts/12345678/contact/
    assert url.startswith("https://app.hubspot.com/contacts/12345678/contact/")
    # The `..` and `/` in contact_id must be percent-encoded
    assert "../malicious" not in url
    # quote() encodes "/" as "%2F" and "." passes through, so "../" -> "..%2F"
    assert "%2F" in url or "%2E%2E" in url or "%2f" in url


def test_hubspot_url_encodes_portal_id_too():
    from src.integrations.hubspot.contact_check import build_contact_url

    url = build_contact_url(portal_id="weird/portal", contact_id="999")
    assert url.startswith("https://app.hubspot.com/contacts/")
    # Slash in portal_id should not introduce a new path segment
    assert "weird/portal" not in url


def test_hubspot_url_normal_ids_pass_through_clean():
    from src.integrations.hubspot.contact_check import build_contact_url

    url = build_contact_url(portal_id="12345678", contact_id="999888")
    assert url == "https://app.hubspot.com/contacts/12345678/contact/999888"
