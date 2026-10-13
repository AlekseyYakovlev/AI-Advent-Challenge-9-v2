"""Guards that keep README.md true to the repository."""

import re
from pathlib import Path

import pytest

from shared.config import Settings

REPO_ROOT: Path = Path(__file__).resolve().parent.parent

SECTION_HEADINGS: tuple[str, ...] = (
    "## What it does",
    "## Directory structure",
    "## Requirements",
    "## Install",
    "## Run",
    "## Configuration (.env)",
    "## Tests",
    "## Documentation",
)
DOC_FILES: tuple[str, ...] = (
    "USER_GUIDE.md",
    "API_SPEC.md",
    "ARCHITECTURE.md",
    "TESTING_GUIDE.md",
)


def _readme() -> str:
    """Return README.md as UTF-8 text."""
    return (REPO_ROOT / "README.md").read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """Return the text between a '## ' heading and the next '## ' heading."""
    start = text.index(heading) + len(heading)
    match = re.search(r"^## ", text[start:], flags=re.MULTILINE)
    end = start + match.start() if match else len(text)
    return text[start:end]


def test_readme_exists_in_english() -> None:
    """The README exists and has no Cyrillic characters."""
    assert (REPO_ROOT / "README.md").is_file()
    assert re.search("[Ѐ-ӿ]", _readme()) is None


def test_readme_sections_in_order() -> None:
    """The required headings appear in increasing position."""
    text = _readme()
    positions = [text.index(heading) for heading in SECTION_HEADINGS]
    assert positions == sorted(positions)


@pytest.mark.parametrize("name", ["agent", "ui", "shared", "scripts", "tests", "docs"])
def test_readme_lists_directories(name: str) -> None:
    """Each named directory is described and exists."""
    assert f"{name}/" in _section(_readme(), "## Directory structure")
    assert (REPO_ROOT / name).is_dir()


def test_readme_doc_links_resolve() -> None:
    """Every docs link points to an existing file and all four documents are linked."""
    text = _readme()
    targets = re.findall(r"\]\((docs/[^)]+\.md)\)", text)
    for target in targets:
        assert (REPO_ROOT / target).is_file(), target
    linked = {Path(target).name for target in targets}
    assert set(DOC_FILES) <= linked


def test_readme_settings_exist_in_config() -> None:
    """Backticked setting names in the configuration section are Settings fields."""
    section = _section(_readme(), "## Configuration (.env)")
    names = set(re.findall(r"`([A-Z][A-Z0-9_]{2,})`", section))
    unknown = names - set(Settings.model_fields)
    assert not unknown, unknown
    required = {
        "UI_PORT",
        "AGENT_PORT",
        "BIND_HOST",
        "DB_PATH",
        "DEEPSEEK_API_KEY",
        "LM_STUDIO_BASE_URL",
        "LLM_TIMEOUT",
    }
    assert required <= names


def test_readme_ports_match_defaults() -> None:
    """Default UI and Agent ports appear in the README."""
    text = _readme()
    for name in ("UI_PORT", "AGENT_PORT"):
        assert str(Settings.model_fields[name].default) in text


def test_readme_commands() -> None:
    """Documented commands and the files they use exist."""
    text = _readme()
    for command in ("python run.py", "pip install -r requirements.txt", "pytest tests/ -v"):
        assert command in text
    for name in ("run.py", "requirements.txt", ".env.example"):
        assert (REPO_ROOT / name).is_file()


def test_readme_has_no_secret() -> None:
    """The README contains no key-shaped string."""
    assert re.search(r"sk-[A-Za-z0-9]{20,}", _readme()) is None
