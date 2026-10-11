"""Tests for the BIND_HOST setting: validation, normalisation and fail-fast exit."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.config import Settings, bind_host_error_message

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_python(code: str, bind_host: str) -> subprocess.CompletedProcess[str]:
    """Run a python -c snippet in the repo root with BIND_HOST set."""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env={**os.environ, "BIND_HOST": bind_host},
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize(
    "value",
    ["", "   ", "127.0.0.1", " 127.0.0.1 ", "localhost", " LocalHost "],
)
def test_loopback_values_normalise(value: str) -> None:
    """Loopback synonyms and empty values normalise to 127.0.0.1."""
    cfg = Settings(_env_file=None, BIND_HOST=value)
    assert cfg.BIND_HOST == "127.0.0.1"
    assert cfg.is_lan_mode is False


@pytest.mark.parametrize("value", ["0.0.0.0", " 0.0.0.0\t"])
def test_all_interfaces_value_enables_lan_mode(value: str) -> None:
    """0.0.0.0 is accepted and turns on LAN mode."""
    cfg = Settings(_env_file=None, BIND_HOST=value)
    assert cfg.BIND_HOST == "0.0.0.0"
    assert cfg.is_lan_mode is True


@pytest.mark.parametrize(
    "value",
    ["::", "::1", "192.168.1.5", "*", "0.0.0.0/0", "my-pc", "0.0.0.0:8000"],
)
def test_other_values_are_refused(value: str) -> None:
    """Every value outside the whitelist raises a validation error."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, BIND_HOST=value)


def test_error_message_is_ascii_and_explains_options() -> None:
    """The refusal message names the value, the accepted values and the LAN IP rule."""
    message = bind_host_error_message("192.168.1.5")
    assert message.isascii()
    for fragment in ("'192.168.1.5'", "127.0.0.1", "localhost", "0.0.0.0", "not supported", "use 0.0.0.0"):
        assert fragment in message


def test_import_config_with_invalid_host_exits_with_code_2() -> None:
    """Importing shared.config with a bad value exits 2 without a traceback."""
    result = _run_python("import shared.config", "192.168.1.5")
    assert result.returncode == 2
    assert "192.168.1.5" in result.stderr
    assert "0.0.0.0" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_import_run_with_invalid_host_exits_before_startup_work() -> None:
    """run.py exits at import, before orphan cleanup or admin bootstrap."""
    result = _run_python("import run", "::")
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "orphan-cleanup" not in result.stdout
    assert "Bootstrap admin" not in result.stdout


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0.0.0.0", "0.0.0.0 True"), ("", "127.0.0.1 False")],
)
def test_settings_singleton_reflects_environment(value: str, expected: str) -> None:
    """The module-level settings object picks up BIND_HOST from the environment."""
    code = "from shared.config import settings; print(settings.BIND_HOST, settings.is_lan_mode)"
    result = _run_python(code, value)
    assert result.returncode == 0
    assert result.stdout.strip() == expected
