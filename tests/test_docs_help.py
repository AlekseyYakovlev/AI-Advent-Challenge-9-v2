"""Guards keeping the Day 31 /help documentation true to the code and free of keys."""

import re
from pathlib import Path

import pytest

from agent.help_command import DEFAULT_HELP_THRESHOLD, HELP_USAGE_TEXT

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
DOCS_DIR: Path = REPO_ROOT / "docs"
TESTS_DIR: Path = REPO_ROOT / "tests"
HELP_HEADING: str = "## /help command (Day 31)"
GUIDE_HEADING: str = "## Команда /help"
KEY_PATTERN: re.Pattern[str] = re.compile(r"sk-[A-Za-z0-9]{20,}")
TEST_FILES: tuple[str, ...] = (
    "test_help_command.py",
    "test_help_api.py",
    "test_help_turn.py",
    "test_help_ws.py",
    "test_help_http.py",
    "test_help_ui.py",
    "test_help_eval.py",
    "test_docs_help.py",
)


def _read(name: str) -> str:
    """Return a document as UTF-8 text with Windows line endings normalised."""
    return (DOCS_DIR / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def _section(text: str, heading: str) -> str:
    """Return the text from a `## ` heading up to the next `## ` heading."""
    start = text.index(heading)
    match = re.search(r"^## ", text[start + len(heading):], flags=re.MULTILINE)
    if match is None:
        return text[start:]
    return text[start:start + len(heading) + match.start()]


def test_api_spec_heading_order() -> None:
    """The API section sits between the MCP servers section and the settings table."""
    text = _read("API_SPEC.md")
    assert (
        text.index("## Project MCP servers (Day 31)")
        < text.index(HELP_HEADING)
        < text.index("## Environment Settings")
    )


def test_user_guide_heading_order() -> None:
    """The user guide section sits between the MCP servers section and the tips."""
    text = _read("USER_GUIDE.md")
    assert text.index("## MCP серверы") < text.index(GUIDE_HEADING) < text.index("## Tips")


def test_architecture_and_testing_guide_have_the_section() -> None:
    """Both documents carry the /help section."""
    assert HELP_HEADING in _read("ARCHITECTURE.md")
    testing = _read("TESTING_GUIDE.md")
    assert testing.index("## Project MCP servers (Day 31)") < testing.index(HELP_HEADING)
    assert "### Coverage map for Day 31" in _section(testing, HELP_HEADING)


@pytest.mark.parametrize("needle", [
    "/api/v1/help/config", "help_notice", '"stored": false', "mcp_server_ids",
    "tools_memory", "tools_tasks", "tools_scheduler", "kb_name", "warnings",
])
def test_api_spec_help_section_needles(needle: str) -> None:
    """The API section names every route, frame and field of /help."""
    assert needle in _section(_read("API_SPEC.md"), HELP_HEADING)


def test_api_spec_states_that_kb_id_is_ignored() -> None:
    """A sentence says a kb_id sent with /help is ignored."""
    section = _section(_read("API_SPEC.md"), HELP_HEADING)
    sentences = re.split(r"(?<=[.:])\s+", section)
    assert any("kb_id" in s and "ignored" in s for s in sentences)


def test_api_spec_default_threshold_matches_code() -> None:
    """The documented default threshold is the constant in the code."""
    assert str(DEFAULT_HELP_THRESHOLD) in _section(_read("API_SPEC.md"), HELP_HEADING)


@pytest.mark.parametrize("needle", [
    "Справка по проекту (/help)", "Документация проекта", "Справка по проекту ·",
    "В документации ничего не найдено.", "Память", "Задачи", "Планировщик",
    "local-git", "название чата не создаётся автоматически",
])
def test_user_guide_help_section_needles(needle: str) -> None:
    """The guide explains the settings, the labels and the title rule in Russian."""
    assert needle in _section(_read("USER_GUIDE.md"), GUIDE_HEADING)


def test_usage_text_is_documented() -> None:
    """The usage form shown by the notice is in the guide."""
    assert "/help <вопрос>" in HELP_USAGE_TEXT
    assert "/help <вопрос>" in _read("USER_GUIDE.md")


@pytest.mark.parametrize("needle", [
    "HelpConfig", "parse_help", "allowed_tools", "local-git", "rag_sources",
])
def test_architecture_help_section_needles(needle: str) -> None:
    """The architecture section names the table, the parser and the storage."""
    assert needle in _section(_read("ARCHITECTURE.md"), HELP_HEADING)


@pytest.mark.parametrize("name", [
    *TEST_FILES, "scripts/e2e_help_playwright.py", "scripts/help_eval.py",
])
def test_testing_guide_lists_every_file(name: str) -> None:
    """The /help testing section lists each new test file and script."""
    assert name in _section(_read("TESTING_GUIDE.md"), HELP_HEADING)


@pytest.mark.parametrize("name", TEST_FILES)
def test_listed_test_files_exist(name: str) -> None:
    """Every test file named in the guide exists."""
    assert (TESTS_DIR / name).is_file()


def test_earlier_phase_descriptions_remain() -> None:
    """The upload and git server descriptions of Phases 21 and 22 are still present."""
    testing = _read("TESTING_GUIDE.md")
    for name in (
        "test_kb_add_job.py", "test_kb_add_api.py", "test_kb_folder_ui.py",
        "test_mcp_project_git_core.py", "test_mcp_project_git.py",
    ):
        assert name in testing
    guide = _read("USER_GUIDE.md")
    assert "github-mcp-server" in guide and "local-git" in guide
    api = _read("API_SPEC.md")
    assert "/api/v1/kb/{kb_id}/files" in api and "## Project MCP servers (Day 31)" in api
    assert "project_git.py" in _read("ARCHITECTURE.md")


def test_docs_contain_no_key_shaped_string() -> None:
    """No document carries a string that looks like an API key."""
    for path in DOCS_DIR.rglob("*.md"):
        assert not KEY_PATTERN.search(path.read_text(encoding="utf-8")), path.name
