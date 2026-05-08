"""Phase 7 / Spec §1.2.1 — Rate limiting.

HubSpot's search endpoint caps at ~5 req/sec per token (stricter than the
general 100/10s limit). We throttle to one request every 250ms (=4 req/sec)
to stay safely under, and the client itself retries on 429 as a second line.
Tests patch `time.sleep` so they don't actually wait.
"""
from unittest.mock import MagicMock, patch


def test_inter_request_sleep_between_lookups():
    from src.integrations.hubspot.contact_check import (
        INTER_REQUEST_SLEEP_SECONDS,
        tag_contacts,
    )

    client = MagicMock()
    client.search_contact_by_email.return_value = None
    client.search_contact_by_name_company.return_value = None

    n = 5
    contacts = [
        {"first_name": f"F{i}", "last_name": f"L{i}", "email": f"x{i}@k.com", "company": "K"}
        for i in range(n)
    ]

    with patch("src.integrations.hubspot.contact_check.time.sleep") as mock_sleep:
        result = tag_contacts(contacts, client, portal_id="p")
        assert len(result["contacts"]) == n
        # n contacts → n-1 inter-request sleeps at >=INTER_REQUEST_SLEEP_SECONDS.
        throttle_sleeps = [
            c.args[0] for c in mock_sleep.call_args_list
            if c.args[0] >= INTER_REQUEST_SLEEP_SECONDS
        ]
        assert len(throttle_sleeps) == n - 1, (
            f"expected {n-1} throttle sleeps, got {[c.args[0] for c in mock_sleep.call_args_list]}"
        )


def test_no_sleep_for_single_contact():
    """One contact → no throttle (nothing to throttle against)."""
    from src.integrations.hubspot.contact_check import (
        INTER_REQUEST_SLEEP_SECONDS,
        tag_contacts,
    )

    client = MagicMock()
    client.search_contact_by_email.return_value = None
    client.search_contact_by_name_company.return_value = None

    contacts = [{"first_name": "F", "last_name": "L", "email": "x@k.com", "company": "K"}]
    with patch("src.integrations.hubspot.contact_check.time.sleep") as mock_sleep:
        tag_contacts(contacts, client, portal_id="p")
        throttle_sleeps = [
            c.args[0] for c in mock_sleep.call_args_list
            if c.args[0] >= INTER_REQUEST_SLEEP_SECONDS
        ]
        assert len(throttle_sleeps) == 0
