"""Phase 7 / Spec §1.2.1 — Rate limiting.

HubSpot allows 100 requests/10 seconds. We batch contact lookups in groups of
10 with a >=100ms sleep between batches. Test patches `time.sleep` to confirm
the throttle calls happen, without actually waiting.
"""
from unittest.mock import MagicMock, patch


def test_rate_limit_sleeps_between_batches_of_ten():
    from src.integrations.hubspot.contact_check import tag_contacts

    client = MagicMock()
    # All return None -> all NET NEW; logic still iterates all 25 and batches.
    client.search_contact_by_email.return_value = None
    client.search_contact_by_name_company.return_value = None

    contacts = [
        {"first_name": f"F{i}", "last_name": f"L{i}", "email": f"x{i}@k.com", "company": "K"}
        for i in range(25)
    ]

    with patch("src.integrations.hubspot.contact_check.time.sleep") as mock_sleep:
        result = tag_contacts(contacts, client, portal_id="p")
        # All 25 are processed
        assert len(result["contacts"]) == 25
        # 25 contacts in batches of 10 -> 3 batches -> at least 2 sleeps
        # between the 3 batches. (Implementation may also sleep before the
        # first batch; we just require >=2 sleeps with >=0.1s.)
        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        long_sleeps = [s for s in sleep_calls if s >= 0.1]
        assert len(long_sleeps) >= 2, f"expected >=2 batch sleeps, got {sleep_calls}"


def test_rate_limit_no_sleep_when_under_one_batch():
    """A single batch of <=10 contacts should not trigger any inter-batch sleep."""
    from src.integrations.hubspot.contact_check import tag_contacts

    client = MagicMock()
    client.search_contact_by_email.return_value = None
    client.search_contact_by_name_company.return_value = None

    contacts = [
        {"first_name": f"F{i}", "last_name": f"L{i}", "email": f"x{i}@k.com", "company": "K"}
        for i in range(5)
    ]
    with patch("src.integrations.hubspot.contact_check.time.sleep") as mock_sleep:
        tag_contacts(contacts, client, portal_id="p")
        long_sleeps = [c.args[0] for c in mock_sleep.call_args_list if c.args[0] >= 0.1]
        assert len(long_sleeps) == 0
