"""Lazy client factory (Phase 13).

Reads env vars at call time. Returns an instance when the relevant env
var is set, otherwise None. The runner inspects each return value and
degrades gracefully — no env var is mandatory.

Format validation is deliberately omitted: the factory only checks
presence. Invalid tokens still produce a client; the API call is what
fails. Failure handling lives at the integration boundary, not here.

This module is the core of "drop API keys in env vars and it works."
"""
from __future__ import annotations

from typing import Optional

from src.config import settings
from src.integrations.apollo.client import ApolloContactClient
from src.integrations.hubspot.account_snapshot import HubSpotAccountClient
from src.integrations.hubspot.client import HubSpotContactClient


def get_apollo_client() -> Optional[ApolloContactClient]:
    """Return an Apollo client when `APOLLO_API_KEY` is set, else None."""
    key = settings.APOLLO_API_KEY
    if not key:
        return None
    return ApolloContactClient(api_key=key)


def get_hubspot_contact_client() -> Optional[HubSpotContactClient]:
    """Return a HubSpot Contacts client when `HUBSPOT_ACCESS_TOKEN` is set,
    else None."""
    token = settings.HUBSPOT_ACCESS_TOKEN
    if not token:
        return None
    return HubSpotContactClient(token=token)


def get_hubspot_account_client() -> Optional[HubSpotAccountClient]:
    """Return a HubSpot Companies client when `HUBSPOT_ACCESS_TOKEN` is set,
    else None."""
    token = settings.HUBSPOT_ACCESS_TOKEN
    if not token:
        return None
    return HubSpotAccountClient(token=token)


def get_hubspot_portal_id() -> Optional[str]:
    """Return the HubSpot portal id when `HUBSPOT_PORTAL_ID` is set, else
    None.

    The portal id is used to construct clickable record URLs. When unset,
    contacts are still tagged but not linked.
    """
    portal = settings.HUBSPOT_PORTAL_ID
    return portal or None
