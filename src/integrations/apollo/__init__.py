"""Apollo integration package (Phase 12).

The legacy `ApolloClient` (used by `src/agents/discovery.py` with its own
TDM/ODM/FS/IT/Safety persona taxonomy) is preserved in `legacy.py` and
re-exported here unchanged for backward compatibility.

New code (Account Research Bot v1) should import `ApolloContactClient`
from `src.integrations.apollo.client`.
"""
from src.integrations.apollo.client import ApolloContactClient
from src.integrations.apollo.legacy import (
    ApolloClient,
    PERSONA_TITLE_KEYWORDS,
)

__all__ = [
    "ApolloClient",          # legacy
    "PERSONA_TITLE_KEYWORDS",  # legacy
    "ApolloContactClient",   # new (Phase 12)
]
