"""Phase 21 — Slack renderer + chunker tests."""
from __future__ import annotations

from datetime import datetime, timezone

from src.research.agents.aggregator import assemble_research_blob
from src.research.agents.contract import (
    AGENT_SECTIONS,
    AgentResult,
    Claim,
    SourceTag,
)
from src.research.agents.renderer import (
    _MAX_CHARS_PER_MESSAGE,
    chunk_blocks_for_slack,
    render_research_blocks,
)


def _result(name, *claims):
    return AgentResult(
        agent_name=name, section_title=AGENT_SECTIONS[name], claims=list(claims)
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_renderer_emits_timestamp_header_and_eight_section_headers():
    blob = assemble_research_blob(
        account_name="Walmart",
        intent="prospecting",
        agent_results=[
            _result(
                "agent_1_network_footprint",
                Claim(text="47 DCs", source_tag=SourceTag.PUBLIC, source_url="https://w"),
            )
        ],
        timestamp=datetime(2026, 5, 26, 18, 14, tzinfo=timezone.utc),
    )
    blocks = render_research_blocks(blob)
    text_blob = "\n".join(
        b.get("text", {}).get("text", "")
        for b in blocks
        if isinstance(b.get("text"), dict)
    )
    assert "Walmart" in text_blob
    assert "Research pulled" in text_blob
    assert "Prospecting" in text_blob

    # All 8 section titles present.
    for title in AGENT_SECTIONS.values():
        assert title in text_blob


def test_renderer_includes_source_tag_chips_inline():
    blob = assemble_research_blob(
        account_name="X",
        intent=None,
        agent_results=[
            _result(
                "agent_1_network_footprint",
                Claim(text="P claim", source_tag=SourceTag.PUBLIC, source_url="https://p"),
                Claim(
                    text="I claim",
                    source_tag=SourceTag.INFERRED,
                    inference_logic="basis math",
                ),
                Claim(
                    text="In claim",
                    source_tag=SourceTag.INTERNAL,
                    source="HubSpot deal 1",
                ),
                Claim(text="N claim", source_tag=SourceTag.NOT_FOUND),
                Claim(text="boom", source_tag=SourceTag.ERROR),
            ),
        ],
    )
    blocks = render_research_blocks(blob)
    text = "\n".join(
        b.get("text", {}).get("text", "")
        for b in blocks
        if isinstance(b.get("text"), dict)
    )
    assert "public" in text
    assert "inferred" in text
    assert "internal" in text
    assert "not found" in text
    assert "error" in text
    assert "<https://p|source>" in text
    assert "basis math" in text


def test_renderer_sanitizes_text_via_safe_mrkdwn():
    """Attacker-controlled text must not be able to plant mrkdwn links."""
    blob = assemble_research_blob(
        account_name="Evil<script>Co",
        intent=None,
        agent_results=[
            _result(
                "agent_1_network_footprint",
                Claim(
                    text="<http://evil|click here>",
                    source_tag=SourceTag.PUBLIC,
                    source_url="https://safe.example.com",
                ),
            )
        ],
    )
    blocks = render_research_blocks(blob)
    text = "\n".join(
        b.get("text", {}).get("text", "")
        for b in blocks
        if isinstance(b.get("text"), dict)
    )
    # safe_mrkdwn strips <, >, | so the planted link doesn't survive.
    assert "<http://evil" not in text
    assert "|click here>" not in text
    # But the validated source_url survives (as a real <url|label>).
    assert "<https://safe.example.com|source>" in text


def test_renderer_handles_empty_claims_list_gracefully():
    blob = assemble_research_blob(
        account_name="X",
        intent=None,
        agent_results=[_result("agent_1_network_footprint")],
    )
    blocks = render_research_blocks(blob)
    text = "\n".join(
        b.get("text", {}).get("text", "")
        for b in blocks
        if isinstance(b.get("text"), dict)
    )
    assert "No findings produced" in text


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


def test_chunk_returns_single_chunk_when_under_limit():
    blocks = render_research_blocks(
        assemble_research_blob(
            account_name="X",
            intent=None,
            agent_results=[
                _result(
                    "agent_1_network_footprint",
                    Claim(text="short claim", source_tag=SourceTag.NOT_FOUND),
                )
            ],
        )
    )
    chunks = chunk_blocks_for_slack(blocks)
    assert len(chunks) == 1


def test_chunk_splits_on_section_boundary_when_oversize():
    # Build a blob with many claims so the total text comfortably blows
    # past _MAX_CHARS_PER_MESSAGE.
    long_claim_text = "x" * 500
    claims_per_section = 6
    agent_results = []
    for name in AGENT_SECTIONS:
        cls = [
            Claim(text=long_claim_text, source_tag=SourceTag.NOT_FOUND)
            for _ in range(claims_per_section)
        ]
        agent_results.append(
            AgentResult(
                agent_name=name,
                section_title=AGENT_SECTIONS[name],
                claims=cls,
            )
        )

    blob = assemble_research_blob(
        account_name="X", intent=None, agent_results=agent_results
    )
    blocks = render_research_blocks(blob)
    chunks = chunk_blocks_for_slack(blocks)

    # More than one chunk; total payload exceeds the message cap.
    assert len(chunks) >= 2

    # Continuation markers on chunks 2..N.
    for i, c in enumerate(chunks[1:], start=2):
        first_text = c[0].get("text", {}).get("text", "")
        assert "continued" in first_text.lower()
        assert f"{i}/" in first_text


def test_chunk_never_returns_empty_list():
    assert chunk_blocks_for_slack([]) == [[]]


def test_chunk_per_chunk_size_under_limit():
    long_text = "y" * 400
    blocks = []
    for _ in range(20):
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": long_text},
            }
        )
    chunks = chunk_blocks_for_slack(blocks)
    for c in chunks:
        size = sum(
            len(b.get("text", {}).get("text", ""))
            for b in c
            if isinstance(b.get("text"), dict)
        )
        assert size <= _MAX_CHARS_PER_MESSAGE + 100  # allow continuation marker
