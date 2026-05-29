"""Phase 4 runner — wires the 8-agent dispatcher into Slack output.

End-to-end flow for one research request:

    DispatcherInput (account, intent, session, clients)
      ↓ dispatch()
    List[AgentResult]
      ↓ assemble_research_blob()
    blob (dict)
      ↓ upsert_account_research()      [persistence]
      ↓ render_research_blocks()
    List[block]
      ↓ chunk_blocks_for_slack()
    List[List[block]]
      ↓ post each chunk

Never raises. Each external call is wrapped; failures degrade
gracefully and the rep still sees whatever sections completed.

This module is the "v1" path. It coexists with the legacy
`runner.run_account_research` until Phase 5 swaps it over.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from src.research.account_research_store import upsert_account_research
from src.research.agents.aggregator import assemble_research_blob
from src.research.agents.contract import SourceTag
from src.research.agents.dispatcher import DispatcherInput, dispatch
from src.research.agents.renderer import (
    chunk_blocks_for_slack,
    render_research_blocks,
)
from src.research.clients_factory import (
    get_apollo_client,
    get_hubspot_contact_client,
    get_hubspot_portal_id,
)
from src.research.sessions import ResearchSession
from src.security.exception_logger import safe_log_exception
from src.usage.v1_events import log_agent_failure, log_new_query

logger = logging.getLogger(__name__)


async def run_v1_research(
    *,
    session: ResearchSession,
    thread_ts: str,
    channel_id: Optional[str],
    rep_id: Optional[str],
    intent: Optional[str],
    post: Callable[..., Any],
    industry: Optional[str] = None,
    # Client injections — production leaves these None and the dispatcher
    # builds clients from the factory; tests pass mocks directly.
    exa_client: Any = None,
    edgar_client: Any = None,
    apollo_client: Any = None,
    hubspot_contact_client: Any = None,
    hubspot_portal_id: Optional[str] = None,
    timeout_sec: float = 45.0,
) -> Optional[str]:
    """Run all 8 agents, persist the blob, post to Slack.

    Returns the AccountResearch row id on success, None on persistence
    failure (the Slack post still happens). `post` is the threaded
    `say(**kwargs)` callable from the Slack handler.
    """
    # Lazy-fill clients from env vars when the caller didn't inject. Tests
    # always inject; production leaves these None and the factories read
    # the relevant *_API_KEY / *_ACCESS_TOKEN settings.
    if apollo_client is None:
        try:
            apollo_client = get_apollo_client()
        except Exception as e:  # noqa: BLE001
            safe_log_exception(logger, e, "[runner_v1] apollo factory failed")
    if hubspot_contact_client is None:
        try:
            hubspot_contact_client = get_hubspot_contact_client()
        except Exception as e:  # noqa: BLE001
            safe_log_exception(logger, e, "[runner_v1] hubspot factory failed")
    if hubspot_portal_id is None:
        try:
            hubspot_portal_id = get_hubspot_portal_id()
        except Exception:  # noqa: BLE001 — best-effort, no log noise
            pass

    inp = DispatcherInput(
        account_name=session.account_name,
        intent=intent,
        industry=industry,
        session=session,
        exa_client=exa_client,
        edgar_client=edgar_client,
        apollo_client=apollo_client,
        hubspot_contact_client=hubspot_contact_client,
        hubspot_portal_id=hubspot_portal_id,
    )

    run_start = time.monotonic()
    try:
        agent_results = await dispatch(inp, timeout_sec=timeout_sec)
    except Exception as e:  # noqa: BLE001 — defense in depth
        safe_log_exception(logger, e, "[runner_v1] dispatch failed")
        agent_results = []
    total_ms = int((time.monotonic() - run_start) * 1000)

    # Per-agent failures → one JSONL row per failed agent so the admin
    # log captures recovery decisions per-slot. ERROR is emitted by the
    # dispatcher when an agent times out or raises.
    for r in agent_results:
        if any(c.source_tag is SourceTag.ERROR for c in r.claims):
            error_text = next(
                (c.text for c in r.claims if c.source_tag is SourceTag.ERROR),
                "Unknown error",
            )
            log_agent_failure(
                agent_name=r.agent_name,
                account_name=session.account_name,
                error_type=error_text[:120],
                recovery="rendered section as ERROR; other agents continued",
            )

    blob = assemble_research_blob(
        account_name=session.account_name,
        intent=intent,
        agent_results=agent_results,
    )

    row_id = upsert_account_research(
        thread_ts=thread_ts,
        account_name=session.account_name,
        rep_id=rep_id,
        channel_id=channel_id,
        intent=intent,
        research_blob=blob,
    )

    try:
        blocks = render_research_blocks(blob)
        chunks = chunk_blocks_for_slack(blocks)
        for chunk_idx, chunk in enumerate(chunks):
            try:
                post(
                    blocks=chunk,
                    text=f"Research for {session.account_name}",
                )
            except Exception as e:  # noqa: BLE001
                safe_log_exception(logger, e, "[runner_v1] Slack post failed")
                # Surface the SlackApiError's response error code AND the
                # chunk shape so we can tell whether it was invalid_blocks,
                # too_long, or something else. Token-safe: response.error is
                # a short Slack-defined string, never user content.
                slack_error_code = None
                response_meta = None
                try:
                    resp = getattr(e, "response", None)
                    if resp is not None:
                        # SlackApiError.response can be dict-like or
                        # SlackResponse with .get().
                        slack_error_code = (
                            resp.get("error") if hasattr(resp, "get") else None
                        )
                        response_meta = (
                            resp.get("response_metadata")
                            if hasattr(resp, "get")
                            else None
                        )
                except Exception:  # noqa: BLE001
                    pass
                logger.warning(
                    "[runner_v1] Slack post chunk %d/%d failed: "
                    "error_code=%s block_count=%d total_text_chars=%d "
                    "max_block_chars=%d response_meta=%s",
                    chunk_idx + 1,
                    len(chunks),
                    slack_error_code,
                    len(chunk),
                    sum(
                        len(b.get("text", {}).get("text", ""))
                        for b in chunk
                        if isinstance(b.get("text"), dict)
                    ),
                    max(
                        (
                            len(b.get("text", {}).get("text", ""))
                            for b in chunk
                            if isinstance(b.get("text"), dict)
                        ),
                        default=0,
                    ),
                    response_meta,
                )
                # Don't keep retrying additional chunks if Slack is down.
                break
    except Exception as e:  # noqa: BLE001 — render bug → swallow
        safe_log_exception(logger, e, "[runner_v1] render failed")

    # One JSONL row per run, regardless of success/failure shape.
    log_new_query(
        account_name=session.account_name,
        rep_id=rep_id or "",
        intent=intent,
        thread_ts=thread_ts,
        total_ms=total_ms,
        agent_durations_ms={
            r.agent_name: r.duration_ms or 0 for r in agent_results
        },
        errored_agents=[
            r.agent_name
            for r in agent_results
            if any(c.source_tag is SourceTag.ERROR for c in r.claims)
        ],
        total_claims=blob.get("stats", {}).get("total_claims", 0),
    )

    return row_id


def run_v1_research_sync(**kwargs: Any) -> Optional[str]:
    """Thin sync wrapper for handlers that aren't async-aware.

    Slack Bolt action callbacks are sync by default; this lets the
    `intent_type` handler in `main.py` call us without restructuring.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an event loop (rare in Bolt sync handlers, but
            # tests sometimes call us this way). Schedule and wait.
            return asyncio.run_coroutine_threadsafe(
                run_v1_research(**kwargs), loop
            ).result()
    except RuntimeError:
        # No event loop on this thread — fall through to asyncio.run.
        pass
    return asyncio.run(run_v1_research(**kwargs))
