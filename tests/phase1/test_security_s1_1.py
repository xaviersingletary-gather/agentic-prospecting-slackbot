"""Security gate S1.1 (spec §1.1):

- .env is gitignored and not present in `git ls-files`
- requirements.txt dependencies use pinned exact versions (==)
- boot fails fast if required env vars are missing instead of starting empty
"""
import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_env_file_is_listed_in_gitignore():
    gitignore_text = (REPO_ROOT / ".gitignore").read_text()
    assert re.search(r"^\.env\s*$", gitignore_text, re.MULTILINE), (
        ".gitignore must contain a literal `.env` line"
    )


def test_env_file_is_not_tracked_by_git():
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # exit code 0 = file is tracked. We want non-zero: .env not tracked.
    assert result.returncode != 0, (
        ".env appears in `git ls-files`. It must not be committed."
    )


def test_requirements_use_pinned_exact_versions():
    requirements = (REPO_ROOT / "requirements.txt").read_text()
    offenders = []
    for raw in requirements.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if "==" not in line:
            offenders.append(line)
            continue
        # disallow other range operators that could ride alongside ==
        for op in [">=", "<=", "~=", "!=", ">", "<"]:
            # tolerate the inner '=' of '==' by stripping it first
            stripped = line.replace("==", "")
            if op in stripped:
                offenders.append(line)
                break
    assert not offenders, (
        "requirements.txt must pin every dependency with `==`. "
        f"Offenders: {offenders}"
    )


def test_boot_validation_runs_before_heavy_imports(monkeypatch):
    """The entrypoint must validate env vars before importing the legacy app.

    We assert the entrypoint module exposes a `main()` callable that raises
    MissingEnvVarsError when required vars are absent — proving validation
    fails fast rather than letting the Slack/agent stack boot with empties.
    """
    from src.env_validator import MissingEnvVarsError

    for var in [
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "OPENROUTER_API_KEY",
        "APOLLO_API_KEY",
        "EXA_API_KEY",
    ]:
        monkeypatch.delenv(var, raising=False)

    import src.entrypoint as entrypoint
    import importlib

    importlib.reload(entrypoint)

    import pytest

    with pytest.raises(MissingEnvVarsError):
        entrypoint.main()
