"""Guards keeping the Day 33 /support documentation true to the code and free of keys."""

import re
from pathlib import Path

import pytest

from agent.support_command import DEFAULT_SUPPORT_THRESHOLD, SUPPORT_USAGE_TEXT
from mcp_servers import support_desk

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
DOCS_DIR: Path = REPO_ROOT / "docs"
TESTS_DIR: Path = REPO_ROOT / "tests"
SUPPORT_HEADING: str = "## /support command (Day 33)"
GUIDE_HEADING: str = "## Команда /support"
USAGE_EXAMPLE: str = "/support #T-1001 Почему не работает авторизация?"
KEY_PATTERN: re.Pattern[str] = re.compile(r"sk-[A-Za-z0-9]{20,}")
TEST_FILES: tuple[str, ...] = (
    "test_command_turn.py",
    "test_mcp_support_desk.py",
    "test_docs_faq.py",
    "test_support_command.py",
    "test_support_lookup.py",
    "test_support_api.py",
    "test_support_ws.py",
    "test_support_http.py",
    "test_support_ui.py",
    "test_support_probe.py",
    "test_e2e_support_script.py",
    "test_docs_support.py",
)
SCRIPTS: tuple[str, ...] = (
    "scripts/e2e_support_playwright.py",
    "scripts/support_probe.py",
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


def test_user_guide_heading_order() -> None:
    """The guide section sits after the /help section and before the tips."""
    text = _read("USER_GUIDE.md")
    assert text.count(GUIDE_HEADING + "\n") == 1
    assert text.index("## Команда /help") < text.index(GUIDE_HEADING + "\n") < text.index("## Tips")


def test_api_spec_and_architecture_have_the_section() -> None:
    """The API and architecture documents carry the /support section after /help."""
    api = _read("API_SPEC.md")
    assert api.index("## /help command (Day 31)") < api.index(SUPPORT_HEADING)
    assert api.index(SUPPORT_HEADING) < api.index("## Environment Settings")
    assert SUPPORT_HEADING in _read("ARCHITECTURE.md")


def test_testing_guide_has_the_section() -> None:
    """The testing guide carries the /support section with a coverage map."""
    testing = _read("TESTING_GUIDE.md")
    assert testing.index("## /help command (Day 31)") < testing.index(SUPPORT_HEADING)
    assert "### Coverage map for Day 33" in _section(testing, SUPPORT_HEADING)


@pytest.mark.parametrize("needle", [
    "/api/v1/support/config", "help_notice", '"command": "support"', "mcp_server_ids",
    "tools_memory", "tools_tasks", "tools_scheduler", "support_server_present",
    "help_kb_id", "kb_name", "warnings", "label", "lookup", "#T-1042", "@login",
    "Использование: /support", "415", "404", "403", "401",
])
def test_api_spec_support_section_needles(needle: str) -> None:
    """The API section names every route, frame and field of /support."""
    assert needle in _section(_read("API_SPEC.md"), SUPPORT_HEADING)


def test_api_spec_states_that_kb_id_is_ignored() -> None:
    """A sentence says a kb_id sent with /support is ignored."""
    section = _section(_read("API_SPEC.md"), SUPPORT_HEADING)
    sentences = re.split(r"(?<=[.:])\s+", section)
    assert any("kb_id" in s and "ignored" in s for s in sentences)


def test_api_spec_default_threshold_matches_code() -> None:
    """The documented default threshold is the constant in the code."""
    assert str(DEFAULT_SUPPORT_THRESHOLD) in _section(_read("API_SPEC.md"), SUPPORT_HEADING)


def test_api_spec_config_route_named_for_both_methods() -> None:
    """Both the GET and the PUT route of the settings are documented."""
    api = _read("API_SPEC.md")
    assert "### GET /api/v1/support/config" in api
    assert "### PUT /api/v1/support/config" in api


@pytest.mark.parametrize("needle", [
    "command_turn", "CommandSpec", "support_data", "SupportConfig", "support_desk.py",
    "help_command.py", "support_command.py", "18000", "20000", "1 MiB",
])
def test_architecture_support_section_needles(needle: str) -> None:
    """The architecture section names the shared mechanism, the fence and the server."""
    assert needle in _section(_read("ARCHITECTURE.md"), SUPPORT_HEADING)


def test_guide_section_has_server_form_values() -> None:
    """The guide gives the exact «MCP серверы» form values for the support server."""
    section = _section(_read("USER_GUIDE.md"), GUIDE_HEADING)
    for expected in (
        "`support`", "`python`", "`mcp_servers/support_desk.py`",
        "`--data=mcp_servers/support_data.json`", "Рабочая директория", "корень репозитория",
        "Поддержка (/support)", "Название", "Команда",
    ):
        assert expected in section, f"{expected} is missing"


def test_guide_quotes_usage_example_and_threshold() -> None:
    """The guide quotes the usage example of the notice and the default threshold."""
    assert USAGE_EXAMPLE in SUPPORT_USAGE_TEXT
    section = _section(_read("USER_GUIDE.md"), GUIDE_HEADING)
    assert USAGE_EXAMPLE in section
    assert str(DEFAULT_SUPPORT_THRESHOLD) in section


async def test_guide_names_every_server_tool() -> None:
    """Every tool the support server registers appears in the guide section."""
    names = [tool.name for tool in await support_desk.server.list_tools()]
    assert len(names) == 4
    section = _section(_read("USER_GUIDE.md"), GUIDE_HEADING)
    for name in names:
        assert name in section, f"{name} is missing from the user guide"


@pytest.mark.parametrize("needle", [
    "не найден, ответ дан без данных тикета", "не найден, ответ дан без данных пользователя",
    "Сервер поддержки недоступен", "Сервер support недоступен, его инструменты не использовались",
    "любой тикет", "провайдера", "последние 3 пары", "FAQ.md",
])
def test_guide_section_needles(needle: str) -> None:
    """The guide explains the warnings, the role note, the provider note and the follow-ups."""
    assert needle in _section(_read("USER_GUIDE.md"), GUIDE_HEADING)


def test_guide_says_live_model_checks_were_not_run() -> None:
    """The guide does not claim that the live-model end-to-end checks passed."""
    section = _section(_read("USER_GUIDE.md"), GUIDE_HEADING)
    assert "NOT RUN" in section
    assert "NOT RUN" in _section(_read("TESTING_GUIDE.md"), SUPPORT_HEADING)


@pytest.mark.parametrize("name", [*TEST_FILES, *SCRIPTS])
def test_testing_guide_lists_every_file(name: str) -> None:
    """The /support testing section lists each new test file and script."""
    assert name in _section(_read("TESTING_GUIDE.md"), SUPPORT_HEADING)


@pytest.mark.parametrize("name", TEST_FILES)
def test_listed_test_files_exist(name: str) -> None:
    """Every test file named in the guide exists."""
    assert (TESTS_DIR / name).is_file()


@pytest.mark.parametrize("name", SCRIPTS)
def test_listed_scripts_exist(name: str) -> None:
    """Every script named in the guide exists."""
    assert (REPO_ROOT / name).is_file()


def test_readme_mentions_support() -> None:
    """The README mentions /support, the FAQ and the support server files."""
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "/support" in text
    assert "docs/FAQ.md" in text
    assert "support_desk.py" in text and "support_data.json" in text


def test_docs_contain_no_key_shaped_string() -> None:
    """No document carries a string that looks like an API key."""
    for path in DOCS_DIR.rglob("*.md"):
        assert not KEY_PATTERN.search(path.read_text(encoding="utf-8")), path.name
    assert not KEY_PATTERN.search((REPO_ROOT / "README.md").read_text(encoding="utf-8"))


def test_no_video_script_in_docs() -> None:
    """No video script file is added to docs/."""
    assert [p.name for p in DOCS_DIR.rglob("*") if "video" in p.name.lower()] == []
