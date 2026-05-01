"""Phase 7 / Spec §1.2.1 — HubSpot contact existence lookup.

Tests the thin HubSpot HTTP wrapper:
- search_contact_by_email(email)
- search_contact_by_name_company(first, last, company)

External API is mocked (httpx.Client) per spec §2.1 rule 4.
"""
from unittest.mock import MagicMock, patch


def _mock_response(status_code: int = 200, json_body: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.raise_for_status = MagicMock()
    return resp


def test_search_contact_by_email_returns_match():
    from src.integrations.hubspot.client import HubSpotContactClient

    found = {
        "results": [
            {
                "id": "12345",
                "properties": {
                    "email": "ceo@kroger.com",
                    "firstname": "Jane",
                    "lastname": "Doe",
                    "company": "Kroger",
                },
            }
        ]
    }

    client = HubSpotContactClient(token="dummy")
    with patch.object(client, "_post", return_value=_mock_response(200, found)) as mock_post:
        result = client.search_contact_by_email("ceo@kroger.com")
        assert result is not None
        assert result["id"] == "12345"
        assert result["properties"]["email"] == "ceo@kroger.com"
        mock_post.assert_called_once()


def test_search_contact_by_email_returns_none_when_no_match():
    from src.integrations.hubspot.client import HubSpotContactClient

    empty = {"results": []}
    client = HubSpotContactClient(token="dummy")
    with patch.object(client, "_post", return_value=_mock_response(200, empty)):
        result = client.search_contact_by_email("nobody@nowhere.com")
        assert result is None


def test_search_contact_by_name_company_returns_match_at_high_confidence():
    from src.integrations.hubspot.client import HubSpotContactClient

    found = {
        "results": [
            {
                "id": "67890",
                "properties": {
                    "firstname": "Jane",
                    "lastname": "Doe",
                    "company": "Kroger",
                },
            }
        ]
    }
    client = HubSpotContactClient(token="dummy")
    with patch.object(client, "_post", return_value=_mock_response(200, found)):
        result = client.search_contact_by_name_company("Jane", "Doe", "Kroger")
        assert result is not None
        assert result["id"] == "67890"


def test_search_contact_by_name_company_rejects_low_confidence_fuzzy_match():
    """If HubSpot returns a contact whose name+company is too far from the query
    (Levenshtein-normalised confidence < 0.9), we do NOT count it as a match."""
    from src.integrations.hubspot.client import HubSpotContactClient

    far = {
        "results": [
            {
                "id": "99999",
                "properties": {
                    "firstname": "Bob",
                    "lastname": "Roberts",
                    "company": "Walmart",
                },
            }
        ]
    }
    client = HubSpotContactClient(token="dummy")
    with patch.object(client, "_post", return_value=_mock_response(200, far)):
        # Query is "Jane Doe @ Kroger" but the result is "Bob Roberts @ Walmart"
        result = client.search_contact_by_name_company("Jane", "Doe", "Kroger")
        assert result is None


def test_client_uses_token_in_authorization_header():
    from src.integrations.hubspot.client import HubSpotContactClient

    client = HubSpotContactClient(token="pat-na1-test-token")
    assert client.headers["Authorization"] == "Bearer pat-na1-test-token"
