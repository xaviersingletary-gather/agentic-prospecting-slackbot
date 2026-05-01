"""Exa integration package.

Public surface:
- `ExaSearchClient` (Phase 11) — minimal sync httpx wrapper used by
  `src.research.findings_builder` to power the v1 research pipeline.
- `ExaClient` (legacy) — the older deep-research client wired into the
  legacy agent stack (`src/agents/researcher.py`,
  `src/agents/contact_researcher.py`). Kept for back-compat only; do not
  import this in new code.
"""
from src.integrations.exa.client import ExaSearchClient
from src.integrations.exa.legacy import ExaClient

__all__ = ["ExaSearchClient", "ExaClient"]
