"""SSRF guard (spec gate S1.4, CLAUDE.md → SSRF).

Any URL fetched server-side (Exa results, citation verifiers, document
fetchers) must pass through this guard BEFORE the HTTP call. Railway
runs in a shared network, so private IP ranges and link-local addresses
(including the AWS/GCP metadata services) must be rejected.

This is a literal-string guard. It does not perform DNS resolution —
that is the HTTP client's job at fetch time. The pattern: validate the
URL with `assert_safe_url` AND configure the HTTP client to refuse to
follow redirects to private addresses. Both layers are required.
"""
import ipaddress
from typing import Set
from urllib.parse import urlparse


class BlockedUrlError(ValueError):
    pass


_BLOCKED_HOSTNAMES: Set[str] = {
    "localhost",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "metadata.amazonaws.com",
    "metadata",
}


def is_safe_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").strip()
    if not host:
        return False
    if host.lower() in _BLOCKED_HOSTNAMES:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # hostname literal — allow; HTTP client must re-check post-DNS
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_safe_url(url: str) -> None:
    if not is_safe_url(url):
        raise BlockedUrlError(f"URL blocked by SSRF guard: {url!r}")
