"""Security gate S1.2 (spec §1.2):

Every external string interpolated into a Block Kit `mrkdwn` field must pass
through `safe_mrkdwn()` which strips `<`, `>`, `|`, and `&`. This prevents
phishing payloads like `<https://attacker.com|click here>` (which Slack
renders as a clickable link) from reaching workspace members.
"""


def test_safe_mrkdwn_strips_angle_brackets():
    from src.security.safe_mrkdwn import safe_mrkdwn

    out = safe_mrkdwn("hello <world>")
    assert "<" not in out
    assert ">" not in out
    assert "hello world" in out


def test_safe_mrkdwn_strips_pipe():
    from src.security.safe_mrkdwn import safe_mrkdwn

    out = safe_mrkdwn("a|b")
    assert "|" not in out


def test_safe_mrkdwn_strips_ampersand():
    from src.security.safe_mrkdwn import safe_mrkdwn

    out = safe_mrkdwn("Tom & Jerry")
    assert "&" not in out


def test_safe_mrkdwn_neutralises_phishing_link():
    from src.security.safe_mrkdwn import safe_mrkdwn

    payload = "<https://attacker.com|click here>"
    out = safe_mrkdwn(payload)
    # No clickable-link metacharacters left
    for c in "<>|":
        assert c not in out
    # The literal text should still be visible (just inert)
    assert "attacker.com" in out


def test_safe_mrkdwn_handles_none_and_empty():
    from src.security.safe_mrkdwn import safe_mrkdwn

    assert safe_mrkdwn("") == ""
    assert safe_mrkdwn(None) == ""


def test_account_name_is_passed_through_safe_mrkdwn_in_text_output():
    from src.research.output_formatter import format_research_output

    findings = {
        "account_name": "Evil<https://attacker.com|click>Co",
        "trigger_events": [],
        "competitor_signals": [],
        "dc_intel": [],
        "board_initiatives": [],
        "research_gaps": [],
    }
    out = format_research_output(findings)
    for c in "<>|":
        assert c not in out, f"raw {c!r} leaked into output: {out!r}"


def test_finding_claim_is_passed_through_safe_mrkdwn_in_text_output():
    from src.research.output_formatter import format_research_output

    findings = {
        "account_name": "Kroger",
        "trigger_events": [
            {
                "claim": "Hiring <https://attacker.com|click here> Director",
                "source_url": "https://example.com/x",
            },
        ],
        "competitor_signals": [],
        "dc_intel": [],
        "board_initiatives": [],
        "research_gaps": [],
    }
    out = format_research_output(findings)
    # Pipe + angle brackets must be stripped from interpolated claim text.
    # We allow `<` and `>` to appear inside the source URL only if the
    # formatter wraps it in a Slack <url> link — but the spec phrasing
    # ("strips < > | &") is global, so we keep this strict.
    assert "<https://attacker.com|click here>" not in out
    for c in "<>|":
        assert c not in out, f"raw {c!r} leaked into output: {out!r}"


def test_block_kit_text_fields_are_safe_for_poisoned_input():
    from src.research.output_formatter import build_research_blocks

    findings = {
        "account_name": "Kroger",
        "trigger_events": [
            {
                "claim": "<https://attacker.com|free money>",
                "source_url": "https://example.com/x",
            },
        ],
        "competitor_signals": [],
        "dc_intel": [],
        "board_initiatives": [],
        "research_gaps": [],
    }
    blocks = build_research_blocks(findings)
    rendered = " ".join(
        b.get("text", {}).get("text", "") if isinstance(b.get("text"), dict)
        else str(b.get("text", ""))
        for b in blocks
    )
    assert "<https://attacker.com|free money>" not in rendered
    for c in "<>|":
        assert c not in rendered, f"raw {c!r} leaked into Block Kit: {rendered!r}"
