"""Log-redaction primitive (spec gate S1.5a, CLAUDE.md → Input → log hygiene).

Reps paste tokens by accident. Raw slash-command text must NEVER end up in
a log line, a Sentry breadcrumb, or a JSONL entry. Anywhere we want to
record that a user *said* something, we record length + sha256 hash.
"""
import hashlib


def redact_user_text(text: str) -> str:
    if not text:
        return "len=0 sha256=empty"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"len={len(text)} sha256={digest[:16]}"
