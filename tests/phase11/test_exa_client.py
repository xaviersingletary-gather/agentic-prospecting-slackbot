"""Phase 11 — Exa search client.

Thin httpx wrapper around Exa's /search endpoint. Auth via x-api-key
header. Every URL coming back is filtered through the SSRF guard
(`assert_safe_url`); URLs that fail the guard are dropped.

External HTTP is mocked — tests never make real network calls.
"""
from unittest.mock import MagicMock


def _mock_response(status_code=200, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.raise_for_status = MagicMock()
    return resp


def test_search_sets_x_api_key_header(mocker):
    from src.integrations.exa.client import ExaSearchClient

    client = ExaSearchClient(api_key="EXA_TEST_KEY")
    post = mocker.patch.object(
        client, "_post", return_value=_mock_response(200, {"results": []})
    )

    client.search("kroger automation", num_results=5)

    assert post.call_count == 1
    # Inspect the headers passed to httpx
    headers = client.headers
    assert headers.get("x-api-key") == "EXA_TEST_KEY"


def test_search_returns_parsed_results(mocker):
    from src.integrations.exa.client import ExaSearchClient

    body = {
        "results": [
            {
                "title": "Kroger plans automation expansion",
                "url": "https://example.com/news/kroger",
                "publishedDate": "2026-01-15",
                "highlights": ["Kroger announced a new robotics deployment."],
            },
            {
                "title": "Kroger 10-K filing",
                "url": "https://example.com/sec/kroger-10k",
                "publishedDate": "2025-04-01",
                "highlights": ["Distribution network spans 35 facilities."],
            },
        ]
    }

    client = ExaSearchClient(api_key="EXA_TEST_KEY")
    mocker.patch.object(client, "_post", return_value=_mock_response(200, body))

    results = client.search("kroger", num_results=5)
    assert len(results) == 2
    assert results[0]["url"] == "https://example.com/news/kroger"
    assert results[0]["title"] == "Kroger plans automation expansion"
    # Snippet pulled from highlights
    assert "robotics deployment" in results[0]["snippet"]


def test_search_filters_urls_failing_ssrf_guard(mocker):
    from src.integrations.exa.client import ExaSearchClient

    body = {
        "results": [
            {
                "title": "Public news",
                "url": "https://example.com/article",
                "highlights": ["safe"],
            },
            {
                "title": "Metadata service",
                "url": "http://169.254.169.254/latest/meta-data",
                "highlights": ["bad"],
            },
            {
                "title": "Localhost",
                "url": "http://127.0.0.1/internal",
                "highlights": ["bad"],
            },
        ]
    }

    client = ExaSearchClient(api_key="EXA_TEST_KEY")
    mocker.patch.object(client, "_post", return_value=_mock_response(200, body))

    results = client.search("anything")
    urls = [r["url"] for r in results]
    assert "https://example.com/article" in urls
    assert "http://169.254.169.254/latest/meta-data" not in urls
    assert "http://127.0.0.1/internal" not in urls


def test_search_returns_empty_list_on_http_error(mocker):
    """5xx / network error must not crash the runner — return [] gracefully."""
    import httpx
    from src.integrations.exa.client import ExaSearchClient

    client = ExaSearchClient(api_key="EXA_TEST_KEY")
    mocker.patch.object(
        client,
        "_post",
        side_effect=httpx.HTTPError("boom"),
    )

    results = client.search("anything")
    assert results == []


def test_search_returns_empty_when_api_key_missing(mocker):
    """No EXA_API_KEY -> no HTTP call, empty results."""
    from src.integrations.exa.client import ExaSearchClient

    client = ExaSearchClient(api_key="")
    post = mocker.patch.object(client, "_post")

    results = client.search("anything")
    assert results == []
    post.assert_not_called()
