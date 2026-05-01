"""Domain resolution for HubSpot account snapshot lookup (Phase 13).

Heuristic — just enough to feed into `get_account_snapshot`:
1. First non-personal contact email → root domain (last 2 dot-segments)
2. Otherwise: lowercased account name with non-alphanumerics stripped + ".com"

We deliberately do not handle multi-part TLDs (`.co.uk` etc) — keeping the
last 2 dot-segments is good enough for the Fortune-500 ICP. If snapshot
lookup misses, the runner just shows the "not found" block.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional

# Domains we never treat as a person's corporate domain.
_PERSONAL_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "icloud.com",
    "me.com",
    "aol.com",
    "proton.me",
    "pm.me",
    "protonmail.com",
}


def resolve_domain(account_name: str, contacts: Iterable[dict]) -> str:
    """Best-effort root domain for `account_name`.

    Picks the first contact whose email is on a non-personal domain;
    falls back to a name-based guess.
    """
    domain = _domain_from_contacts(contacts)
    if domain:
        return domain
    return _fallback_domain_from_name(account_name)


def _domain_from_contacts(contacts: Iterable[dict]) -> Optional[str]:
    for c in contacts or []:
        email = (c.get("email") or "").strip().lower() if isinstance(c, dict) else ""
        if not email or "@" not in email:
            continue
        host = email.split("@", 1)[1].strip()
        if not host:
            continue
        if host in _PERSONAL_EMAIL_DOMAINS:
            continue
        # Strip subdomains down to the last 2 dot-segments.
        return _root_domain(host)
    return None


def _root_domain(host: str) -> str:
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    return ".".join(parts[-2:])


def _fallback_domain_from_name(account_name: str) -> str:
    name = (account_name or "").lower()
    # Strip everything that isn't ASCII alphanumeric.
    cleaned = re.sub(r"[^a-z0-9]", "", name)
    if not cleaned:
        # Nothing salvageable — return ".com" as a degenerate but not-crashing
        # value. The snapshot lookup will return None and the runner will fall
        # through to the "not found" block.
        return ".com"
    return f"{cleaned}.com"
