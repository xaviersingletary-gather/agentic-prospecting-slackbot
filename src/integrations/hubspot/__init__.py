"""HubSpot integration subpackage (Phase 7+).

The legacy `HubSpotClient` (used by `src/agents/normalizer.py`) is preserved
as `legacy.HubSpotClient` and re-exported here for backward compatibility.
New code (Account Research Bot v1) should use `HubSpotContactClient`.
"""
from src.integrations.hubspot.client import HubSpotContactClient
from src.integrations.hubspot.contact_check import (
    tag_contacts,
    build_contact_url,
    render_contact_for_slack,
)
from src.integrations.hubspot.legacy import HubSpotClient

__all__ = [
    "HubSpotClient",  # legacy
    "HubSpotContactClient",
    "tag_contacts",
    "build_contact_url",
    "render_contact_for_slack",
]
