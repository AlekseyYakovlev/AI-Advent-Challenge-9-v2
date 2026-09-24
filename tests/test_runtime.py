"""Tests for the Python version startup guard."""

from pathlib import Path

import pytest

from shared.runtime import MIN_PYTHON, check_python_version

ROOT: Path = Path(__file__).parent.parent


def test_old_python_exits_with_clear_message() -> None:
    """Python 3.10 is rejected with a message naming the requirement and the running version."""
    with pytest.raises(SystemExit) as excinfo:
        check_python_version((3, 10, 12))

    message: str = str(excinfo.value)
    assert "3.11" in message
    assert "3.10" in message


@pytest.mark.parametrize("version", [(3, 11, 0), (3, 13, 1)])
def test_supported_python_passes(version: tuple[int, int, int]) -> None:
    """3.11 and newer are accepted."""
    assert check_python_version(version) is None


def test_current_interpreter_passes() -> None:
    """The interpreter running the suite satisfies the minimum."""
    assert MIN_PYTHON == (3, 11)
    assert check_python_version() is None


@pytest.mark.parametrize("relative_path", ["run.py", "agent/main.py"])
def test_entry_points_call_the_guard(relative_path: str) -> None:
    """Both process entry points run the guard."""
    source: str = (ROOT / relative_path).read_text(encoding="utf-8")
    assert "check_python_version" in source
