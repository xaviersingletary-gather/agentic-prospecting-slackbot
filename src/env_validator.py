import os
from typing import Iterable, List, Optional


REQUIRED_ENV_VARS: List[str] = [
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "ANTHROPIC_API_KEY",
    "APOLLO_API_KEY",
    "EXA_API_KEY",
]


class MissingEnvVarsError(RuntimeError):
    pass


def validate_required_env_vars(required: Optional[Iterable[str]] = None) -> None:
    names = list(required) if required is not None else REQUIRED_ENV_VARS
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        raise MissingEnvVarsError(
            "Missing required environment variables: " + ", ".join(missing)
        )
