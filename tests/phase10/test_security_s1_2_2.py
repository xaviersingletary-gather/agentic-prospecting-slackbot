"""Security gates for Phase 8.

S1.2.2 — HubSpot URL is built from the hardcoded base + URL-encoded
          portal_id and company_id only. A malicious id ("../foo")
          must not escape the `/contacts/{portal}/company/{id}` path.

S1.2.1b inheritance — every external HubSpot string interpolated into
          a Slack mrkdwn block passes through `safe_mrkdwn`.

S1.2.1a inheritance — HubSpot exception messages logged via
          `safe_log_exception`; token-shaped substrings never leak.
"""
import logging
from unittest.mock import MagicMock


# ---------- S1.2.2 — URL construction ----------

def test_build_company_url_quotes_company_id():
    from src.integrations.hubspot.account_snapshot import build_company_url

    url = build_company_url(portal_id="111111", company_id="../malicious")
    # The dangerous id must not produce a relative-path escape.
    assert "/contacts/111111/company/" in url
    assert "/contacts/111111/company/../malicious" not in url
    # Quoted form: %2E%2E%2F
    assert "%2F" in url or "%2f" in url


def test_build_company_url_uses_hubspot_app_base():
    from src.integrations.hubspot.account_snapshot import build_company_url

    assert build_company_url(portal_id="42", company_id="99").startswith(
        "https://app.hubspot.com/contacts/"
    )


def test_build_company_url_quotes_portal_id_too():
    from src.integrations.hubspot.account_snapshot import build_company_url

    # If portal_id ever came from user input, slashes must be quoted.
    url = build_company_url(portal_id="111/../x", company_id="42")
    assert "/contacts/111/../x/" not in url


# ---------- S1.2.1b inheritance — safe_mrkdwn on rendered output ----------

def test_account_snapshot_blocks_strip_phishing_payload_in_account_name():
    from src.integrations.hubspot.account_snapshot import (
        AccountSnapshot, build_account_snapshot_blocks,
    )

    snap = AccountSnapshot(
        account_name="<https://attacker.com|click here>",
        contacts_count=1, open_deals=0,
        last_activity="—", lead_source="Inbound",
        icp_score=10, icp_tier="Tier 3", signal_score=5,
        hubspot_url="https://example.com",
    )
    rendered = " ".join(
        b.get("text", {}).get("text", "") for b in build_account_snapshot_blocks(snap)
        if isinstance(b.get("text"), dict)
    )
    for ch in "<>|":
        assert ch not in rendered, f"raw {ch!r} leaked into snapshot output"


def test_account_snapshot_blocks_sanitise_lead_source():
    from src.integrations.hubspot.account_snapshot import (
        AccountSnapshot, build_account_snapshot_blocks,
    )

    snap = AccountSnapshot(
        account_name="Kroger",
        contacts_count=1, open_deals=0,
        last_activity="—",
        lead_source="<https://attacker.com|inbound>",
        icp_score=10, icp_tier="Tier 3", signal_score=5,
        hubspot_url="https://example.com",
    )
    rendered = " ".join(
        b.get("text", {}).get("text", "") for b in build_account_snapshot_blocks(snap)
        if isinstance(b.get("text"), dict)
    )
    for ch in "<>|":
        assert ch not in rendered


def test_not_found_blocks_sanitise_account_name():
    from src.integrations.hubspot.account_snapshot import (
        build_account_not_found_blocks,
    )

    blocks = build_account_not_found_blocks("Evil<https://attacker.com|click>Co")
    rendered = " ".join(
        b.get("text", {}).get("text", "") for b in blocks
        if isinstance(b.get("text"), dict)
    )
    for ch in "<>|":
        assert ch not in rendered


# ---------- S1.2.1a inheritance — token-shaped exception messages ----------

def test_get_account_snapshot_does_not_leak_exception_message_in_logs(caplog):
    from httpx import HTTPStatusError, Request, Response
    from src.integrations.hubspot.account_snapshot import (
        HubSpotAccountClient, get_account_snapshot,
    )

    client = MagicMock(spec=HubSpotAccountClient)
    client.search_company_by_domain.side_effect = HTTPStatusError(
        "auth fail: Bearer pat-na1-LEAK-DO-NOT-LOG",
        request=Request("POST", "https://api.hubapi.com/x"),
        response=Response(503),
    )

    caplog.set_level(logging.ERROR)
    get_account_snapshot(client, account_name="Kroger", domain="kroger.com",
                        portal_id="111111")

    for rec in caplog.records:
        msg = rec.getMessage()
        assert "pat-na1" not in msg
        assert "Bearer" not in msg
        assert "auth fail" not in msg
