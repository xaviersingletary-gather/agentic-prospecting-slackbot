"""Spec §1.2 — Research output: structured format only.

The formatter takes a `findings` dict and produces a 5-section text block.
Every section must always appear, even when empty (with "No public data found").
The DC intel section uses a distinct fallback string.
"""

import pytest


FULL_FINDINGS = {
    "account_name": "Kroger",
    "trigger_events": [
        {"claim": "Opened new DC in Aurora, CO", "source_url": "https://example.com/aurora"},
    ],
    "competitor_signals": [
        {"claim": "Symbotic deployed at 5 facilities", "source_url": "https://example.com/symbotic"},
    ],
    "dc_intel": [
        {"claim": "Operates 35 distribution centers", "source_url": "https://example.com/kroger-dcs"},
    ],
    "board_initiatives": [
        {"claim": "Inventory accuracy mandate from CEO", "source_url": "https://example.com/inv"},
    ],
    "research_gaps": [
        "Could not confirm WMS vendor",
    ],
}

EMPTY_FINDINGS = {
    "account_name": "ObscureCo",
    "trigger_events": [],
    "competitor_signals": [],
    "dc_intel": [],
    "board_initiatives": [],
    "research_gaps": [],
}


REQUIRED_SECTION_HEADERS = [
    "📌 TRIGGER EVENTS",
    "🏭 COMPETITOR SIGNALS",
    "📦 DISTRIBUTION / FACILITY INTEL",
    "🎯 BOARD INITIATIVES",
    "🔍 RESEARCH GAPS",
]


def test_account_name_appears_at_top():
    from src.research.output_formatter import format_research_output

    out = format_research_output(FULL_FINDINGS)
    first_line = out.strip().splitlines()[0]
    assert "Kroger" in first_line
    assert "🏢" in first_line


def test_all_five_section_headers_present_with_full_data():
    from src.research.output_formatter import format_research_output

    out = format_research_output(FULL_FINDINGS)
    for header in REQUIRED_SECTION_HEADERS:
        assert header in out, f"missing section header: {header!r}\n\n{out}"


def test_all_five_section_headers_present_with_empty_data():
    from src.research.output_formatter import format_research_output

    out = format_research_output(EMPTY_FINDINGS)
    for header in REQUIRED_SECTION_HEADERS:
        assert header in out, f"missing section header for empty input: {header!r}\n\n{out}"


def test_empty_section_shows_no_public_data_found():
    from src.research.output_formatter import format_research_output

    out = format_research_output(EMPTY_FINDINGS)
    # Every section except DC intel uses "No public data found"
    # Count: trigger events, competitor signals, board initiatives, research gaps = 4 sections
    assert out.count("No public data found") >= 4, (
        f"expected at least 4 'No public data found' lines (one per non-DC empty section)\n\n{out}"
    )


def test_dc_intel_empty_shows_could_not_confirm_not_no_public_data():
    from src.research.output_formatter import format_research_output

    out = format_research_output(EMPTY_FINDINGS)
    # Spec §1.2: DC intel must say "Could not confirm DC count from public sources"
    # specifically — not the generic "No public data found"
    sections = out.split("📦 DISTRIBUTION / FACILITY INTEL")
    assert len(sections) == 2, "DC intel section header missing"
    after = sections[1]
    next_header_idx = min(
        (after.find(h) for h in ("🎯 BOARD INITIATIVES",) if after.find(h) != -1),
        default=len(after),
    )
    dc_block = after[:next_header_idx]
    assert "Could not confirm" in dc_block, (
        f"DC intel empty must say 'Could not confirm', got:\n{dc_block!r}"
    )


def test_dc_intel_with_data_renders_claim_and_source():
    from src.research.output_formatter import format_research_output

    out = format_research_output(FULL_FINDINGS)
    assert "Operates 35 distribution centers" in out
    assert "https://example.com/kroger-dcs" in out


def test_factual_bullets_include_source_urls():
    from src.research.output_formatter import format_research_output

    out = format_research_output(FULL_FINDINGS)
    # every claim from FULL_FINDINGS should appear with its URL
    for section in ("trigger_events", "competitor_signals", "board_initiatives"):
        for finding in FULL_FINDINGS[section]:
            assert finding["claim"] in out
            assert finding["source_url"] in out


def test_research_gaps_renders_plain_strings():
    from src.research.output_formatter import format_research_output

    out = format_research_output(FULL_FINDINGS)
    assert "Could not confirm WMS vendor" in out
