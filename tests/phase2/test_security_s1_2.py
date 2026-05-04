"""Security gate S1.2 (spec §1.2):

Every external string interpolated into a Block Kit `mrkdwn` field must pass
through `safe_mrkdwn()` which strips `<`, `>`, `|`, and `&`. This prevents
phishing payloads like `<https://attacker.com|click here>` (which Slack
renders as a clickable link) from reaching workspace members.

Exception — our own validated source-URL links: source URLs have already
been through `assert_safe_url` (SSRF + scheme allowlist) upstream in
`findings_builder`, and the display text is the URL's parsed `netloc` —
not attacker-controlled. The tests below strip those known-safe
`<scheme://host/...|host>` link patterns before asserting no `<>|`
remain, so a poisoned *claim* string can't smuggle metacharacters but a
properly formed source link is allowed.
"""
import re

# Match the exact pattern our formatter emits: `<scheme://host/...|host>`
# where the display text is a domain-shaped token. Anything else with
# `<...|...>` would be either attacker-controlled or malformed and must
# still trigger the assertion.
_SAFE_LINK_RE = re.compile(
    r"<https?://[^>|\s]+\|[A-Za-z0-9.\-]+>"
)


def _strip_safe_links(text: str) -> str:
    return _SAFE_LINK_RE.sub("", text)


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
    # The formatter is allowed to emit Slack-link syntax for the
    # post-SSRF-validated source URL (`<https://example.com/x|example.com>`)
    # — strip those before asserting no `<>|` slipped through from the
    # poisoned claim.
    assert "<https://attacker.com|click here>" not in out
    stripped = _strip_safe_links(out)
    for c in "<>|":
        assert c not in stripped, f"raw {c!r} leaked into output: {stripped!r}"


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
    # Allow our validated source-URL link patterns; reject everything else.
    stripped = _strip_safe_links(rendered)
    for c in "<>|":
        assert c not in stripped, f"raw {c!r} leaked into Block Kit: {stripped!r}"
