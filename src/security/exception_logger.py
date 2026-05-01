"""Exception-name-only logger (spec gate S1.2.1a, Phase 7).

Stringified exceptions from third-party SDKs (HubSpot, Apollo, Salesforce,
Google) frequently contain partial credential material — request bodies,
auth headers, or tokens echoed back from a 401. We therefore NEVER call
`str(e)` or pass the exception object through `%s` / f-string interpolation.

Cross-phase primitive: any integration phase that catches SDK errors must
route them through this helper.

See `CLAUDE.md` → Input → log hygiene, spec §2.5 cross-phase primitives.
"""
import logging
from typing import Optional


def safe_log_exception(
    logger: logging.Logger,
    exc: BaseException,
    static_message: str,
    *,
    level: int = logging.ERROR,
) -> None:
    """Log an exception by type name only.

    Records `<static_message> [<ExceptionTypeName>]`. Never includes
    `str(exc)`, `repr(exc)`, exc.args, or any traceback frame text.
    """
    type_name = type(exc).__name__
    # Build the message ourselves — do not pass exc into the formatting args.
    logger.log(level, "%s [%s]", static_message, type_name)


def safe_format_exception(exc: BaseException, static_message: Optional[str] = None) -> str:
    """Return a token-safe single-line summary of an exception."""
    type_name = type(exc).__name__
    if static_message:
        return f"{static_message} [{type_name}]"
    return f"[{type_name}]"
