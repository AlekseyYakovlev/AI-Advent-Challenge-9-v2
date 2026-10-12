"""Tests for the limit settings: defaults, bounds and fail-fast exit."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.config import LIMIT_SETTING_MINIMUMS, Settings, limit_setting_error_message

REPO_ROOT = Path(__file__).resolve().parents[1]
NAMES = list(LIMIT_SETTING_MINIMUMS)
OFF_NAMES = [n for n, m in LIMIT_SETTING_MINIMUMS.items() if m == 0]


def _run_python(code: str, env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run a python -c snippet in the repo root with extra environment variables."""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env={**os.environ, **env_overrides},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """With nothing set the limits are 30, 5, 300 and 2."""
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    cfg = Settings(_env_file=None)
    assert cfg.RATE_LIMIT_MESSAGES_PER_MINUTE == 30
    assert cfg.LOGIN_MAX_FAILED_ATTEMPTS == 5
    assert cfg.LOGIN_LOCKOUT_SECONDS == 300
    assert cfg.MAX_CONCURRENT_GENERATIONS == 2


@pytest.mark.parametrize("name", OFF_NAMES)
def test_zero_switches_limit_off(name: str) -> None:
    """Zero is accepted for the three settings where it means off."""
    cfg = Settings(_env_file=None, **{name: "0"})
    assert getattr(cfg, name) == 0


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("value", ["-1", "abc", "1.5", ""])
def test_bad_values_are_refused(name: str, value: str) -> None:
    """Negative, non-numeric, fractional and empty values fail validation."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: value})


def test_lockout_seconds_zero_is_refused() -> None:
    """The lockout duration has a minimum of 1 second."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LOGIN_LOCKOUT_SECONDS="0")


def test_error_message_is_ascii_and_names_setting() -> None:
    """The message is ASCII even for a Cyrillic raw value and states the minimum."""
    text = limit_setting_error_message("LOGIN_LOCKOUT_SECONDS", "пять")
    assert text.isascii()
    assert "LOGIN_LOCKOUT_SECONDS" in text
    assert ">= 1" in text
    assert "0 switches this limit off" not in text
    for name in OFF_NAMES:
        msg = limit_setting_error_message(name, "x")
        assert name in msg
        assert ">= 0" in msg
        assert "0 switches this limit off" in msg


@pytest.mark.parametrize(
    "name,value",
    [
        ("RATE_LIMIT_MESSAGES_PER_MINUTE", "abc"),
        ("MAX_CONCURRENT_GENERATIONS", "-1"),
        ("LOGIN_LOCKOUT_SECONDS", "0"),
    ],
)
def test_bad_env_value_exits_with_code_2(name: str, value: str) -> None:
    """A bad value stops the import with exit code 2 and no traceback."""
    result = _run_python("import shared.config", {name: value})
    assert result.returncode == 2
    assert name in result.stderr
    assert "Traceback" not in result.stderr


def test_valid_env_values_are_read() -> None:
    """Valid values are accepted and visible through the settings object."""
    code = (
        "from shared.config import settings as s;"
        "print(s.RATE_LIMIT_MESSAGES_PER_MINUTE, s.LOGIN_MAX_FAILED_ATTEMPTS,"
        " s.LOGIN_LOCKOUT_SECONDS, s.MAX_CONCURRENT_GENERATIONS)"
    )
    env = {
        "RATE_LIMIT_MESSAGES_PER_MINUTE": "5",
        "LOGIN_MAX_FAILED_ATTEMPTS": "0",
        "LOGIN_LOCKOUT_SECONDS": "60",
        "MAX_CONCURRENT_GENERATIONS": "1",
    }
    result = _run_python(code, env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["5", "0", "60", "1"]


def test_bind_host_failure_still_exits_2() -> None:
    """The BIND_HOST error path keeps its own message when limit values are valid."""
    result = _run_python(
        "import shared.config", {"BIND_HOST": "192.168.1.5", "LOGIN_LOCKOUT_SECONDS": "60"}
    )
    assert result.returncode == 2
    assert "BIND_HOST" in result.stderr
    assert "Traceback" not in result.stderr
