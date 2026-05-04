import pytest


REQUIRED_VARS = [
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "OPENROUTER_API_KEY",
    "APOLLO_API_KEY",
    "EXA_API_KEY",
]


def _clear_all(monkeypatch):
    for v in REQUIRED_VARS:
        monkeypatch.delenv(v, raising=False)


def _set_all(monkeypatch):
    for v in REQUIRED_VARS:
        monkeypatch.setenv(v, "fake_value")


def test_validate_raises_when_all_required_vars_missing(monkeypatch):
    from src.env_validator import MissingEnvVarsError, validate_required_env_vars

    _clear_all(monkeypatch)
    with pytest.raises(MissingEnvVarsError) as exc_info:
        validate_required_env_vars()

    msg = str(exc_info.value)
    for var in REQUIRED_VARS:
        assert var in msg, f"expected '{var}' in error message, got: {msg}"


def test_validate_passes_when_all_required_vars_present(monkeypatch):
    from src.env_validator import validate_required_env_vars

    _set_all(monkeypatch)
    # should not raise
    validate_required_env_vars()


def test_validate_lists_only_missing_vars(monkeypatch):
    from src.env_validator import MissingEnvVarsError, validate_required_env_vars

    _set_all(monkeypatch)
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    with pytest.raises(MissingEnvVarsError) as exc_info:
        validate_required_env_vars()

    msg = str(exc_info.value)
    assert "APOLLO_API_KEY" in msg
    assert "EXA_API_KEY" in msg
    assert "SLACK_BOT_TOKEN" not in msg
    assert "OPENROUTER_API_KEY" not in msg


def test_validate_treats_empty_string_as_missing(monkeypatch):
    from src.env_validator import MissingEnvVarsError, validate_required_env_vars

    _set_all(monkeypatch)
    monkeypatch.setenv("EXA_API_KEY", "")

    with pytest.raises(MissingEnvVarsError) as exc_info:
        validate_required_env_vars()

    assert "EXA_API_KEY" in str(exc_info.value)


def test_validate_accepts_custom_required_list(monkeypatch):
    from src.env_validator import MissingEnvVarsError, validate_required_env_vars

    monkeypatch.delenv("FOO_KEY", raising=False)
    with pytest.raises(MissingEnvVarsError) as exc_info:
        validate_required_env_vars(required=["FOO_KEY"])
    assert "FOO_KEY" in str(exc_info.value)
