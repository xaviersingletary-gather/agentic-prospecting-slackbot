"""Spec §1.6 — /about slash command surfaces version + roadmap."""
from unittest.mock import MagicMock


def _flatten_blocks(blocks):
    """Concatenate all renderable text from a Block Kit blocks list."""
    out = []
    for b in blocks:
        text = b.get("text")
        if isinstance(text, dict):
            out.append(text.get("text", ""))
        elif isinstance(text, str):
            out.append(text)
        for el in b.get("elements", []) or []:
            t = el.get("text") if isinstance(el, dict) else None
            if isinstance(t, dict):
                out.append(t.get("text", ""))
            elif isinstance(t, str):
                out.append(t)
    return "\n".join(out)


def test_about_blocks_contain_version_from_config():
    from src.handlers.about import build_about_blocks
    from src.config import VERSION

    blocks = build_about_blocks()
    text = _flatten_blocks(blocks)
    assert VERSION in text


def test_about_blocks_mention_upcoming_v1_2_v1_3_v2_0():
    from src.handlers.about import build_about_blocks

    text = _flatten_blocks(build_about_blocks())
    assert "V1.2" in text
    assert "V1.3" in text
    assert "V2.0" in text


def test_about_blocks_describe_current_capabilities():
    from src.handlers.about import build_about_blocks

    text = _flatten_blocks(build_about_blocks()).lower()
    assert "research" in text


def test_about_blocks_include_contact_line():
    from src.handlers.about import build_about_blocks

    text = _flatten_blocks(build_about_blocks())
    # spec example: "Questions? Ping Xavier in #gtm-engineering"
    assert "Xavier" in text or "xavier" in text.lower()


def test_handle_about_responds_ephemerally():
    from src.handlers.about import handle_about_command

    ack = MagicMock()
    respond = MagicMock()
    handle_about_command(
        payload={"user_id": "U1", "command": "/about"},
        ack=ack,
        respond=respond,
    )
    ack.assert_called()
    respond.assert_called()
    kwargs = respond.call_args.kwargs
    assert kwargs.get("response_type") == "ephemeral"


def test_handle_about_includes_blocks_in_response():
    from src.handlers.about import handle_about_command

    ack = MagicMock()
    respond = MagicMock()
    handle_about_command(
        payload={"user_id": "U1", "command": "/about"},
        ack=ack,
        respond=respond,
    )
    kwargs = respond.call_args.kwargs
    assert "blocks" in kwargs
    assert isinstance(kwargs["blocks"], list)
    assert len(kwargs["blocks"]) > 0
