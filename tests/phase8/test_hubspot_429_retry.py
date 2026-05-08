"""HubSpotContactClient._post retries transparently on 429.

HubSpot's search endpoint caps at ~5 req/sec. When we burst slightly past
that, the response is 429 with a Retry-After header. The client should
honor the header (or back off exponentially) and retry, returning the
eventual 200 to the caller. Tests patch httpx.Client and time.sleep so
no real network or wall-clock time is spent.
"""
from unittest.mock import MagicMock, patch

import httpx

from src.integrations.hubspot import client as hs_client


def _resp(status: int, headers: dict | None = None, json_body: dict | None = None):
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.headers = headers or {}
    r.json.return_value = json_body or {}
    return r


def test_post_retries_on_429_and_returns_eventual_200():
    c = hs_client.HubSpotContactClient(token="pat-test")

    responses = [_resp(429, {"Retry-After": "1"}), _resp(200, {}, {"results": []})]
    mock_http_client = MagicMock()
    mock_http_client.post.side_effect = responses
    mock_http_client.__enter__.return_value = mock_http_client
    mock_http_client.__exit__.return_value = False

    with patch("src.integrations.hubspot.client.httpx.Client", return_value=mock_http_client), \
         patch("src.integrations.hubspot.client.time.sleep") as mock_sleep:
        out = c._post("/x", {"a": 1})

    assert out.status_code == 200
    assert mock_http_client.post.call_count == 2
    # Honored Retry-After (1.0s)
    assert any(call.args[0] == 1.0 for call in mock_sleep.call_args_list)


def test_post_caps_retry_after_at_max_wait():
    c = hs_client.HubSpotContactClient(token="pat-test")

    responses = [_resp(429, {"Retry-After": "999"}), _resp(200, {}, {"results": []})]
    mock_http_client = MagicMock()
    mock_http_client.post.side_effect = responses
    mock_http_client.__enter__.return_value = mock_http_client
    mock_http_client.__exit__.return_value = False

    with patch("src.integrations.hubspot.client.httpx.Client", return_value=mock_http_client), \
         patch("src.integrations.hubspot.client.time.sleep") as mock_sleep:
        c._post("/x", {})

    # Cap kicks in at RATE_LIMIT_MAX_WAIT_SECONDS (5.0)
    waits = [call.args[0] for call in mock_sleep.call_args_list]
    assert all(w <= hs_client.RATE_LIMIT_MAX_WAIT_SECONDS for w in waits)


def test_post_falls_back_to_exp_backoff_when_no_retry_after():
    c = hs_client.HubSpotContactClient(token="pat-test")

    responses = [_resp(429), _resp(429), _resp(200, {}, {"results": []})]
    mock_http_client = MagicMock()
    mock_http_client.post.side_effect = responses
    mock_http_client.__enter__.return_value = mock_http_client
    mock_http_client.__exit__.return_value = False

    with patch("src.integrations.hubspot.client.httpx.Client", return_value=mock_http_client), \
         patch("src.integrations.hubspot.client.time.sleep") as mock_sleep:
        out = c._post("/x", {})

    assert out.status_code == 200
    waits = [call.args[0] for call in mock_sleep.call_args_list]
    # Exponential: 0.5, then 1.0
    assert waits == [
        hs_client.RATE_LIMIT_INITIAL_BACKOFF_SECONDS,
        hs_client.RATE_LIMIT_INITIAL_BACKOFF_SECONDS * 2,
    ]


def test_post_returns_429_after_exhausting_attempts():
    """If all attempts return 429, the final 429 is returned to the caller
    (which will then `raise_for_status()` and trip the graceful fallback)."""
    c = hs_client.HubSpotContactClient(token="pat-test")

    responses = [_resp(429), _resp(429), _resp(429)]
    mock_http_client = MagicMock()
    mock_http_client.post.side_effect = responses
    mock_http_client.__enter__.return_value = mock_http_client
    mock_http_client.__exit__.return_value = False

    with patch("src.integrations.hubspot.client.httpx.Client", return_value=mock_http_client), \
         patch("src.integrations.hubspot.client.time.sleep"):
        out = c._post("/x", {})

    assert out.status_code == 429
    assert mock_http_client.post.call_count == hs_client.RATE_LIMIT_MAX_ATTEMPTS


def test_post_does_not_retry_on_non_429():
    c = hs_client.HubSpotContactClient(token="pat-test")

    responses = [_resp(500)]
    mock_http_client = MagicMock()
    mock_http_client.post.side_effect = responses
    mock_http_client.__enter__.return_value = mock_http_client
    mock_http_client.__exit__.return_value = False

    with patch("src.integrations.hubspot.client.httpx.Client", return_value=mock_http_client), \
         patch("src.integrations.hubspot.client.time.sleep") as mock_sleep:
        out = c._post("/x", {})

    assert out.status_code == 500
    assert mock_http_client.post.call_count == 1
    assert mock_sleep.call_count == 0
