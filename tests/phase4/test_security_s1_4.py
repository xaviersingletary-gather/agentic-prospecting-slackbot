"""Security gate S1.4 (spec §1.4): SSRF guard.

Any URL fetched server-side must pass through `is_safe_url` (or
`assert_safe_url` for the fail-fast variant) BEFORE the HTTP call.
Private IP ranges, loopback, link-local, and the bare hostname
`localhost` are rejected. Public URLs pass.

Reused by every phase that fetches external URLs (Exa, citation
verifiers, document fetchers in later phases).
"""
import pytest


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://127.0.0.1:8080",
    "https://10.0.0.5/secret",
    "http://172.16.5.10",
    "http://192.168.1.1",
    "http://169.254.169.254/latest/meta-data/",   # AWS IMDS
    "http://[::1]/",                              # IPv6 loopback
    "http://localhost/",
    "http://LOCALHOST/",
])
def test_blocks_private_and_loopback_urls(url):
    from src.security.url_guard import is_safe_url

    assert not is_safe_url(url), f"expected {url} to be blocked"


@pytest.mark.parametrize("url", [
    "https://example.com/page",
    "http://example.com",
    "https://kroger.com/about/dc-network",
    "https://www.bls.gov/news.release/jolts.htm",
])
def test_allows_public_urls(url):
    from src.security.url_guard import is_safe_url

    assert is_safe_url(url), f"expected {url} to be allowed"


@pytest.mark.parametrize("url", [
    "ftp://example.com/file",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "",
    "not-a-url",
    "gopher://example.com",
])
def test_blocks_non_http_schemes_and_garbage(url):
    from src.security.url_guard import is_safe_url

    assert not is_safe_url(url)


def test_blocks_metadata_service_hostnames():
    from src.security.url_guard import is_safe_url

    assert not is_safe_url("http://metadata.google.internal/v1/")
    assert not is_safe_url("http://metadata.amazonaws.com/")


def test_assert_safe_url_raises_on_blocked():
    from src.security.url_guard import BlockedUrlError, assert_safe_url

    with pytest.raises(BlockedUrlError):
        assert_safe_url("http://127.0.0.1/")


def test_assert_safe_url_passes_on_public():
    from src.security.url_guard import assert_safe_url

    # No exception
    assert_safe_url("https://example.com/page")
