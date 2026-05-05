"""Phase 12 / Spec §1.3 — Apollo v1 contact search client.

Thin httpx wrapper around Apollo's `/v1/mixed_people/search` endpoint.
Mockable by patching `_post` (same pattern as `HubSpotContactClient._post`).

Security gates re-asserted:
- S1.2.1a — non-2xx + transport errors → swallowed, logged via
  safe_log_exception (token never appears in stringified exception).
"""
from unittest.mock import MagicMock, patch

import logging


def _mock_response(status_code: int = 200, json_body: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    if 200 <= status_code < 300:
        resp.raise_for_status = MagicMock()
    else:
        resp.raise_for_status = MagicMock(
            side_effect=Exception(f"HTTP {status_code}")
        )
    return resp


def test_search_sends_company_and_titles_in_payload():
    from src.integrations.apollo.client import ApolloContactClient

    client = ApolloContactClient(api_key="test-key")
    search_body = {
        "people": [
            {
                "id": "p1",
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "jane.doe@kroger.com",
                "title": "VP Warehouse Operations",
                "organization": {"name": "Kroger"},
            }
        ]
    }
    # The client now follows up `mixed_people/api_search` with
    # `/people/bulk_match` to enrich last_name/email/linkedin. Returns
    # an empty matches list to keep the test focused on search payload.
    enrich_body = {"matches": []}

    def _fake_post(path, payload):
        if "bulk_match" in path:
            return _mock_response(200, enrich_body)
        return _mock_response(200, search_body)

    with patch.object(client, "_post", side_effect=_fake_post) as mock_post:
        result = client.search_contacts_by_company_and_titles(
            "Kroger", ["VP Warehouse", "Head of Warehouse"]
        )
        # 1 search + 1 enrich call expected
        assert mock_post.call_count == 2
        # First call is the search — assert its shape
        search_call = mock_post.call_args_list[0]
        args, kwargs = search_call.args, search_call.kwargs
        path = args[0] if args else kwargs.get("path", "")
        payload = args[1] if len(args) > 1 else kwargs.get("payload", {})

        # Legacy-aligned endpoint; documented `/v1/mixed_people/search` 422s.
        assert "/mixed_people/api_search" in path
        # Without an explicit `domain`, the client derives one from the
        # company name and searches by `q_organization_domains_list`.
        # The keyword fallback only kicks in when domain search yields 0.
        assert payload.get("q_organization_domains_list") == ["kroger.com"]
        assert "VP Warehouse" in payload.get("person_titles", [])
        assert "Head of Warehouse" in payload.get("person_titles", [])
        # Second call is the enrichment — assert it hit bulk_match
        enrich_call = mock_post.call_args_list[1]
        assert "bulk_match" in (
            enrich_call.args[0] if enrich_call.args
            else enrich_call.kwargs.get("path", "")
        )
        # Result list normalized
        assert isinstance(result, list) and len(result) == 1


def test_search_passes_x_api_key_header_not_bearer():
    """Apollo authentication uses X-Api-Key, not Bearer."""
    from src.integrations.apollo.client import ApolloContactClient

    client = ApolloContactClient(api_key="my-secret-apollo-key")
    headers = client.headers
    assert headers.get("X-Api-Key") == "my-secret-apollo-key"
    # No Authorization: Bearer header
    assert "Authorization" not in headers
    assert "Bearer" not in str(headers.values())


def test_search_returns_empty_on_non_2xx_and_logs_exception_name_only(caplog):
    """On 5xx the client must return [] (graceful fallback) and never
    leak the API token through stringified exceptions."""
    from src.integrations.apollo.client import ApolloContactClient

    client = ApolloContactClient(api_key="apollo-secret-XYZ")

    # Patch _post to raise an exception that contains the token in its message
    fake_exc = RuntimeError("Apollo 500: X-Api-Key=apollo-secret-XYZ leaked")
    with patch.object(client, "_post", side_effect=fake_exc):
        with caplog.at_level(logging.ERROR):
            result = client.search_contacts_by_company_and_titles(
                "Kroger", ["VP Warehouse"]
            )

    assert result == []
    # Token must never appear in any log line (S1.2.1a)
    for record in caplog.records:
        assert "apollo-secret-XYZ" not in record.getMessage()


def test_search_returns_empty_when_apollo_returns_no_people():
    from src.integrations.apollo.client import ApolloContactClient

    client = ApolloContactClient(api_key="test-key")
    with patch.object(
        client, "_post", return_value=_mock_response(200, {"people": []})
    ):
        result = client.search_contacts_by_company_and_titles(
            "TinyCorp", ["VP Warehouse"]
        )
    assert result == []


def test_search_normalizes_apollo_person_into_contact_dict():
    """Each returned dict must carry the keys downstream tag_contacts expects:
    first_name, last_name, email, company, title."""
    from src.integrations.apollo.client import ApolloContactClient

    client = ApolloContactClient(api_key="test-key")
    body = {
        "people": [
            {
                "id": "p1",
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "jane.doe@kroger.com",
                "title": "VP Warehouse Operations",
                "organization": {"name": "Kroger"},
            },
            {
                # Older Apollo shape — title at top, organization name flat
                "id": "p2",
                "first_name": "John",
                "last_name": "Smith",
                "email": None,
                "title": "Head of Warehouse",
                "organization_name": "Kroger Stores",
            },
        ]
    }
    with patch.object(client, "_post", return_value=_mock_response(200, body)):
        result = client.search_contacts_by_company_and_titles(
            "Kroger", ["VP Warehouse"]
        )

    assert len(result) == 2
    for c in result:
        assert "first_name" in c
        assert "last_name" in c
        assert "title" in c
        assert "company" in c
        # email key present even if value missing
        assert "email" in c

    assert result[0]["first_name"] == "Jane"
    assert result[0]["email"] == "jane.doe@kroger.com"
    assert result[0]["company"] == "Kroger"
    assert result[1]["company"] == "Kroger Stores"


def test_search_uses_per_page_from_limit():
    from src.integrations.apollo.client import ApolloContactClient

    client = ApolloContactClient(api_key="test-key")
    with patch.object(
        client, "_post", return_value=_mock_response(200, {"people": []})
    ) as mock_post:
        client.search_contacts_by_company_and_titles(
            "Kroger", ["VP Warehouse"], limit=10
        )
        args, kwargs = mock_post.call_args
        payload = args[1] if len(args) > 1 else kwargs.get("payload", {})
        assert payload.get("per_page") == 10
        assert payload.get("page") == 1
