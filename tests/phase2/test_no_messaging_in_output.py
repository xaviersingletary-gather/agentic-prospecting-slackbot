"""Spec §1.2 — V1 strips all outreach/messaging content.

The formatter must not produce any AE game-plan, proposed message, opening
hook, or subject line text. These are V2 features.
"""


BANNED_PHRASES = [
    "outreach brief",
    "proposed message",
    "ae game plan",
    "game plan",
    "opening hook",
    "subject line",
    "suggested message",
    "draft email",
    "draft message",
]


def _findings_with_realistic_data():
    return {
        "account_name": "Kroger",
        "trigger_events": [
            {"claim": "Hiring Director of Continuous Improvement",
             "source_url": "https://example.com/ci"},
        ],
        "competitor_signals": [
            {"claim": "Symbotic case study mentions Kroger",
             "source_url": "https://example.com/symb"},
        ],
        "dc_intel": [
            {"claim": "Operates 35 DCs", "source_url": "https://example.com/dcs"},
        ],
        "board_initiatives": [
            {"claim": "Inventory accuracy mandate", "source_url": "https://example.com/inv"},
        ],
        "research_gaps": ["WMS vendor unconfirmed"],
    }


def test_no_messaging_phrases_in_full_output():
    from src.research.output_formatter import format_research_output

    out = format_research_output(_findings_with_realistic_data()).lower()
    offenders = [p for p in BANNED_PHRASES if p in out]
    assert not offenders, (
        f"output contains messaging phrases (V1 must be research-only): {offenders}"
    )


def test_no_messaging_phrases_in_empty_output():
    from src.research.output_formatter import format_research_output

    out = format_research_output({
        "account_name": "X",
        "trigger_events": [],
        "competitor_signals": [],
        "dc_intel": [],
        "board_initiatives": [],
        "research_gaps": [],
    }).lower()
    offenders = [p for p in BANNED_PHRASES if p in out]
    assert not offenders, (
        f"empty output contains messaging phrases: {offenders}"
    )


def test_block_kit_blocks_contain_no_messaging_phrases():
    from src.research.output_formatter import build_research_blocks

    blocks = build_research_blocks(_findings_with_realistic_data())
    rendered = " ".join(
        b.get("text", {}).get("text", "") if isinstance(b.get("text"), dict)
        else str(b.get("text", ""))
        for b in blocks
    ).lower()
    offenders = [p for p in BANNED_PHRASES if p in rendered]
    assert not offenders, (
        f"Block Kit output contains messaging phrases: {offenders}"
    )
