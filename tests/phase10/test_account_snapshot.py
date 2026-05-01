"""Spec §1.2.2 — get_account_snapshot returns a structured snapshot ready
for the Slack block builder, or None when the account isn't in HubSpot.
"""
from unittest.mock import MagicMock


def _company(*, cid="1", **props):
    return {"id": cid, "properties": props}


def test_known_account_returns_full_snapshot():
    from src.integrations.hubspot.account_snapshot import (
        HubSpotAccountClient, get_account_snapshot,
    )

    client = MagicMock(spec=HubSpotAccountClient)
    client.search_company_by_domain.return_value = _company(
        cid="42",
        name="Kroger",
        domain="kroger.com",
        num_associated_contacts="14",
        num_associated_deals="3",
        notes_last_contacted="2026-04-12T15:00:00Z",
        hs_lead_status="OPEN_DEAL",
        icp_score="74",
        icp_tier="Tier 1",
        buying_signal_score="62",
    )

    snap = get_account_snapshot(client, account_name="Kroger", domain="kroger.com",
                                portal_id="111111")

    assert snap is not None
    assert snap.account_name == "Kroger"
    assert snap.contacts_count == 14
    assert snap.open_deals == 3
    assert "2026-04-12" in snap.last_activity
    assert snap.lead_source  # populated from hs_lead_status fallback chain
    assert snap.icp_tier == "Tier 1"
    assert snap.icp_score == 74
    assert snap.signal_score == 62
    # URL constructed from quoted portal_id + company id
    assert "/contacts/111111/company/42" in snap.hubspot_url


def test_unknown_account_returns_none():
    from src.integrations.hubspot.account_snapshot import (
        HubSpotAccountClient, get_account_snapshot,
    )

    client = MagicMock(spec=HubSpotAccountClient)
    client.search_company_by_domain.return_value = None

    snap = get_account_snapshot(client, account_name="NoSuchCo", domain="nosuch.co",
                                portal_id="111111")
    assert snap is None


def test_blocks_known_account_renders_all_required_fields():
    from src.integrations.hubspot.account_snapshot import (
        AccountSnapshot, build_account_snapshot_blocks,
    )

    snap = AccountSnapshot(
        account_name="Kroger",
        contacts_count=14,
        open_deals=3,
        last_activity="2026-04-12 — Email sent",
        lead_source="Inbound — webform",
        icp_score=74,
        icp_tier="Tier 1",
        signal_score=62,
        hubspot_url="https://app.hubspot.com/contacts/111111/company/42",
    )
    blocks = build_account_snapshot_blocks(snap)
    rendered = " ".join(
        b.get("text", {}).get("text", "") if isinstance(b.get("text"), dict)
        else "" for b in blocks
    )

    assert "Kroger" in rendered
    assert "14" in rendered
    assert "3" in rendered
    assert "2026-04-12" in rendered
    assert "Inbound" in rendered
    assert "Tier 1" in rendered
    assert "74" in rendered
    assert "62" in rendered
    assert "https://app.hubspot.com/contacts/111111/company/42" in rendered


def test_blocks_unknown_account_renders_not_found_message():
    from src.integrations.hubspot.account_snapshot import (
        build_account_not_found_blocks,
    )

    blocks = build_account_not_found_blocks("ObscureCo")
    rendered = " ".join(
        b.get("text", {}).get("text", "") if isinstance(b.get("text"), dict)
        else "" for b in blocks
    )
    assert "not found in HubSpot" in rendered.lower() or \
           "account not found" in rendered.lower()
    assert "ObscureCo" in rendered


def test_icp_score_zero_renders_not_yet_scored():
    """Spec §1.2.3: ICP empty/null/zero → 'Not yet scored', never zero or blank."""
    from src.integrations.hubspot.account_snapshot import (
        AccountSnapshot, build_account_snapshot_blocks,
    )

    snap = AccountSnapshot(
        account_name="Kroger",
        contacts_count=1, open_deals=0,
        last_activity="—", lead_source="Unknown",
        icp_score=0, icp_tier=None, signal_score=0,
        hubspot_url="https://example.com",
    )
    rendered = " ".join(
        b.get("text", {}).get("text", "") for b in build_account_snapshot_blocks(snap)
        if isinstance(b.get("text"), dict)
    )
    assert "Not yet scored" in rendered
    # Never show a zero
    assert "ICP: 0" not in rendered
    assert "Signal score: 0" not in rendered


def test_icp_score_missing_renders_not_yet_scored():
    from src.integrations.hubspot.account_snapshot import (
        AccountSnapshot, build_account_snapshot_blocks,
    )

    snap = AccountSnapshot(
        account_name="Kroger",
        contacts_count=1, open_deals=0,
        last_activity="—", lead_source="Unknown",
        icp_score=None, icp_tier=None, signal_score=None,
        hubspot_url="https://example.com",
    )
    rendered = " ".join(
        b.get("text", {}).get("text", "") for b in build_account_snapshot_blocks(snap)
        if isinstance(b.get("text"), dict)
    )
    assert "Not yet scored" in rendered


def test_lead_source_missing_renders_unknown_not_blank():
    from src.integrations.hubspot.account_snapshot import (
        AccountSnapshot, build_account_snapshot_blocks,
    )

    snap = AccountSnapshot(
        account_name="Kroger",
        contacts_count=1, open_deals=0,
        last_activity="—", lead_source=None,
        icp_score=10, icp_tier="Tier 3", signal_score=5,
        hubspot_url="https://example.com",
    )
    rendered = " ".join(
        b.get("text", {}).get("text", "") for b in build_account_snapshot_blocks(snap)
        if isinstance(b.get("text"), dict)
    )
    assert "Unknown" in rendered


def test_get_account_snapshot_handles_5xx_gracefully():
    """HubSpot 5xx must NOT crash the research — caller treats it as 'no snapshot'."""
    from httpx import HTTPStatusError, Request, Response
    from src.integrations.hubspot.account_snapshot import (
        HubSpotAccountClient, get_account_snapshot,
    )

    client = MagicMock(spec=HubSpotAccountClient)
    client.search_company_by_domain.side_effect = HTTPStatusError(
        "auth fail: Bearer pat-na1-LEAK-XXXX",
        request=Request("POST", "https://api.hubapi.com/x"),
        response=Response(503),
    )

    snap = get_account_snapshot(client, account_name="Kroger", domain="kroger.com",
                                portal_id="111111")

    # Returns None on failure; caller renders the not-found block + a banner
    # downstream if it cares about the difference.
    assert snap is None
