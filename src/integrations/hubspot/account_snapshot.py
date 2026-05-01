"""HubSpot account snapshot (Phase 8 / spec §1.2.2 + §1.2.3).

Domain-keyed company lookup → snapshot dataclass → Slack Block Kit.
Read-only. No writes to HubSpot in v1.

Public surface
--------------
- HubSpotAccountClient.search_company_by_domain(domain) → dict | None
- get_account_snapshot(client, account_name, domain, portal_id) → AccountSnapshot | None
- build_account_snapshot_blocks(snapshot) → list[block]
- build_account_not_found_blocks(account_name) → list[block]
- build_company_url(portal_id, company_id) → str
- normalize_domain(value) → str

Caller fallback contract
------------------------
- 5xx / network failure → returns None and logs `type(e).__name__` only
  via `safe_log_exception` (S1.2.1a). The research run continues with
  the not-found block plus an upstream banner if the caller wants one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, urlparse

import httpx

from src.security.exception_logger import safe_log_exception
from src.security.safe_mrkdwn import safe_mrkdwn

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hubapi.com"
SEARCH_PATH = "/crm/v3/objects/companies/search"

HUBSPOT_APP_BASE = "https://app.hubspot.com"

# Custom HubSpot properties read for the snapshot. Strings here must match
# property internal names in HubSpot.
_COMPANY_PROPERTIES = [
    "name",
    "domain",
    "num_associated_contacts",
    "num_associated_deals",
    "notes_last_contacted",
    "notes_last_updated",
    "hs_lastmodifieddate",
    "hs_lead_status",
    "lead_source",
    "icp_score",
    "icp_tier",
    "buying_signal_score",
]


# ---------------------------------------------------------------------------
# Dataclass + URL builder
# ---------------------------------------------------------------------------

@dataclass
class AccountSnapshot:
    account_name: str
    contacts_count: int
    open_deals: int
    last_activity: str           # already formatted for display
    lead_source: Optional[str]   # None → renders "Unknown"
    icp_score: Optional[int]     # None or 0 → "Not yet scored"
    icp_tier: Optional[str]
    signal_score: Optional[int]  # None or 0 → "Not yet scored"
    hubspot_url: str


def build_company_url(portal_id: str, company_id: str) -> str:
    """Build the HubSpot record URL.

    `quote(safe="")` ensures slashes in either id are percent-encoded so
    a malicious id like "../malicious" cannot escape the path.
    """
    qp = quote(str(portal_id), safe="")
    qc = quote(str(company_id), safe="")
    return f"{HUBSPOT_APP_BASE}/contacts/{qp}/company/{qc}"


# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------

def normalize_domain(value: Optional[str]) -> str:
    if not value:
        return ""
    raw = value.strip().lower()
    if "://" in raw:
        parsed = urlparse(raw)
        host = (parsed.netloc or parsed.path or "").split("/")[0]
    else:
        host = raw.split("/")[0]
    if host.startswith("www."):
        host = host[len("www."):]
    return host


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class HubSpotAccountClient:
    """Thin HubSpot Companies search client. Mockable: tests patch `_post`."""

    def __init__(self, token: str, *, timeout: float = 10.0):
        self.token = token
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict) -> httpx.Response:
        url = f"{BASE_URL}{path}"
        with httpx.Client(timeout=self.timeout) as client:
            return client.post(url, headers=self.headers, json=payload)

    def search_company_by_domain(self, domain_or_url: str) -> Optional[dict]:
        domain = normalize_domain(domain_or_url)
        if not domain:
            return None

        payload = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "domain",
                    "operator": "EQ",
                    "value": domain,
                }]
            }],
            "properties": _COMPANY_PROPERTIES,
            "limit": 25,
        }
        response = self._post(SEARCH_PATH, payload)
        response.raise_for_status()
        results = (response.json() or {}).get("results", [])
        if not results:
            return None
        if len(results) == 1:
            return results[0]
        # Multiple matches → pick the one with the most associated contacts.
        return max(results, key=_associated_contacts_count)


def _associated_contacts_count(company: dict) -> int:
    val = (company.get("properties") or {}).get("num_associated_contacts")
    try:
        return int(val) if val not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Snapshot orchestration
# ---------------------------------------------------------------------------

def _coerce_int(val) -> int:
    try:
        return int(val) if val not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


def _coerce_optional_int(val) -> Optional[int]:
    if val in (None, ""):
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _format_last_activity(props: dict) -> str:
    # Spec wants "most recent engagement across all contacts"; for v1 we use
    # the company-level last-contacted/last-modified properties as a simpler,
    # API-cheap proxy. Engagements query lands in a future phase.
    candidates = [
        props.get("notes_last_contacted"),
        props.get("notes_last_updated"),
        props.get("hs_lastmodifieddate"),
    ]
    for c in candidates:
        if c:
            # Strip time portion for display compactness
            return str(c).split("T")[0]
    return "—"


def get_account_snapshot(
    client: HubSpotAccountClient,
    account_name: str,
    domain: str,
    portal_id: str,
) -> Optional[AccountSnapshot]:
    try:
        company = client.search_company_by_domain(domain)
    except Exception as e:
        safe_log_exception(logger, e, "hubspot account snapshot lookup failed")
        return None

    if not company:
        return None

    props = company.get("properties") or {}
    return AccountSnapshot(
        account_name=props.get("name") or account_name,
        contacts_count=_coerce_int(props.get("num_associated_contacts")),
        open_deals=_coerce_int(props.get("num_associated_deals")),
        last_activity=_format_last_activity(props),
        lead_source=props.get("lead_source") or props.get("hs_lead_status"),
        icp_score=_coerce_optional_int(props.get("icp_score")),
        icp_tier=props.get("icp_tier") or None,
        signal_score=_coerce_optional_int(props.get("buying_signal_score")),
        hubspot_url=build_company_url(portal_id, company.get("id", "")),
    )


# ---------------------------------------------------------------------------
# Slack Block Kit builders
# ---------------------------------------------------------------------------

def _icp_line(snap: AccountSnapshot) -> str:
    score = snap.icp_score
    sig = snap.signal_score
    if not score and not sig:
        return "ICP: Not yet scored"
    score_text = (
        f"{score} ({safe_mrkdwn(snap.icp_tier or '—')})" if score
        else "Not yet scored"
    )
    sig_text = f"Signal score: {sig}" if sig else "Signal score: Not yet scored"
    # Em-dash separator (not `|`) so the rendered output stays clean of the
    # mrkdwn metacharacters stripped by S1.2.1b's safe_mrkdwn primitive.
    return f"ICP: {score_text} — {sig_text}"


def build_account_snapshot_blocks(snap: AccountSnapshot) -> list:
    name = safe_mrkdwn(snap.account_name)
    lead_source = safe_mrkdwn(snap.lead_source) if snap.lead_source else "Unknown"
    last_activity = safe_mrkdwn(snap.last_activity)
    url = safe_mrkdwn(snap.hubspot_url)

    text = (
        f"*📊 HUBSPOT ACCOUNT SNAPSHOT — {name}*\n"
        f"• Contacts in HubSpot: *{snap.contacts_count}*\n"
        f"• Open deals: *{snap.open_deals}*\n"
        f"• Last activity: {last_activity}\n"
        f"• Lead source: {lead_source}\n"
        f"• {_icp_line(snap)}\n"
        f"• → View in HubSpot: {url}"
    )
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {"type": "divider"},
    ]


def build_account_not_found_blocks(account_name: str) -> list:
    name = safe_mrkdwn(account_name)
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*📊 HUBSPOT ACCOUNT SNAPSHOT — {name}*\n"
                    "Account not found in HubSpot — this may be a new account."
                ),
            },
        },
        {"type": "divider"},
    ]
