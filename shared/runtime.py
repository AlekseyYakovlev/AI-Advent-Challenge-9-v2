"""Runtime environment guards shared by both processes."""

import sys

MIN_PYTHON: tuple[int, int] = (3, 11)


def check_python_version(version: tuple[int, ...] | None = None) -> None:
    """Exit with a one-line message when the interpreter is older than MIN_PYTHON."""
    if version is None:
        version = tuple(sys.version_info)
    if tuple(version[:2]) < MIN_PYTHON:
        raise SystemExit(
            "AiAdventAgentV2 requires Python 3.11+ "
            f"(asyncio.timeout, BaseExceptionGroup); running {version[0]}.{version[1]}."
        )
