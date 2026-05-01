"""Spec §1.2.2 — domain-keyed HubSpot company lookup."""
from unittest.mock import MagicMock


def _make_response(*, status: int = 200, json_payload=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_payload or {}
    resp.raise_for_status = MagicMock()
    if status >= 400:
        from httpx import HTTPStatusError, Request, Response
        resp.raise_for_status.side_effect = HTTPStatusError(
            "boom",
            request=Request("POST", "https://api.hubapi.com/x"),
            response=Response(status),
        )
    return resp


def test_normalize_domain_strips_protocol_and_www():
    from src.integrations.hubspot.account_snapshot import normalize_domain

    assert normalize_domain("https://www.kroger.com/about") == "kroger.com"
    assert normalize_domain("http://kroger.com") == "kroger.com"
    assert normalize_domain("www.kroger.com") == "kroger.com"
    assert normalize_domain("Kroger.COM") == "kroger.com"


def test_normalize_domain_handles_empty():
    from src.integrations.hubspot.account_snapshot import normalize_domain

    assert normalize_domain("") == ""
    assert normalize_domain(None) == ""


def test_search_company_by_domain_uses_root_domain_in_filter():
    from src.integrations.hubspot.account_snapshot import HubSpotAccountClient

    client = HubSpotAccountClient(token="t")
    client._post = MagicMock(return_value=_make_response(json_payload={"results": [{
        "id": "1",
        "properties": {"domain": "kroger.com", "name": "Kroger", "num_associated_contacts": "3"},
    }]}))

    res = client.search_company_by_domain("https://www.kroger.com")

    assert res["id"] == "1"
    payload = client._post.call_args.args[1]
    flat = str(payload)
    # Filter must use the stripped root domain
    assert "kroger.com" in flat
    assert "www" not in payload["filterGroups"][0]["filters"][0]["value"]


def test_search_company_picks_record_with_most_contacts_when_multiple_match():
    from src.integrations.hubspot.account_snapshot import HubSpotAccountClient

    client = HubSpotAccountClient(token="t")
    client._post = MagicMock(return_value=_make_response(json_payload={"results": [
        {"id": "A", "properties": {"name": "Kroger A", "num_associated_contacts": "2"}},
        {"id": "B", "properties": {"name": "Kroger B", "num_associated_contacts": "9"}},
        {"id": "C", "properties": {"name": "Kroger C", "num_associated_contacts": "5"}},
    ]}))

    res = client.search_company_by_domain("kroger.com")

    assert res["id"] == "B", "should pick the record with the most associated contacts"


def test_search_company_returns_none_when_no_results():
    from src.integrations.hubspot.account_snapshot import HubSpotAccountClient

    client = HubSpotAccountClient(token="t")
    client._post = MagicMock(return_value=_make_response(json_payload={"results": []}))

    assert client.search_company_by_domain("kroger.com") is None


def test_search_company_returns_none_for_empty_domain():
    from src.integrations.hubspot.account_snapshot import HubSpotAccountClient

    client = HubSpotAccountClient(token="t")
    client._post = MagicMock()

    assert client.search_company_by_domain("") is None
    client._post.assert_not_called()


def test_search_company_propagates_5xx_for_caller_to_handle():
    from httpx import HTTPStatusError
    from src.integrations.hubspot.account_snapshot import HubSpotAccountClient

    client = HubSpotAccountClient(token="t")
    client._post = MagicMock(return_value=_make_response(status=503))

    try:
        client.search_company_by_domain("kroger.com")
        raised = False
    except HTTPStatusError:
        raised = True
    assert raised, "5xx must surface so get_account_snapshot can fall back"
