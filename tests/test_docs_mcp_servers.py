"""Guards keeping the MCP server documentation true to the code and free of secrets."""

import re
from pathlib import Path

import pytest

from agent.mcp_config import ENV_MASK
from mcp_servers import project_git
from shared.config import Settings

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
DOCS_DIR: Path = REPO_ROOT / "docs"
PHASE_DIR: Path = REPO_ROOT / ".planning" / "phases" / "22-project-mcp-servers-day-31"
MCP_SECTION: str = "## MCP серверы"
TOKEN_PATTERN: re.Pattern[str] = re.compile(
    r"github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,}"
)
BINARY_SUFFIXES: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".exe", ".db", ".pyc", ".pdf",
})
TEST_FILES: tuple[str, ...] = (
    "test_mcp_project_git_core.py",
    "test_mcp_project_git.py",
    "test_mcp_real_github.py",
    "test_docs_mcp_servers.py",
    "test_mcp_project_files.py",
    "test_files_task.py",
    "test_files_eval.py",
    "test_diff_card_ui.py",
)


def _read(path: Path) -> str:
    """Return a file as UTF-8 text with Windows line endings normalised."""
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def _section(text: str, heading: str) -> str:
    """Return the text from a `## ` heading up to the next `## ` heading."""
    start = text.index(heading)
    match = re.search(r"^## ", text[start + len(heading):], flags=re.MULTILINE)
    if match is None:
        return text[start:]
    return text[start:start + len(heading) + match.start()]


def _guide_section() -> str:
    """Return the «MCP серверы» section of the user guide."""
    return _section(_read(DOCS_DIR / "USER_GUIDE.md"), MCP_SECTION)


def _scanned_files() -> list[Path]:
    """Return the repository text files that must never contain a token."""
    files: list[Path] = []
    for directory in (DOCS_DIR, REPO_ROOT / "mcp_servers", REPO_ROOT / "scripts", PHASE_DIR):
        if directory.is_dir():
            files.extend(p for p in directory.rglob("*") if p.is_file())
    files.extend((REPO_ROOT / "tests").glob("test_mcp_*.py"))
    return sorted(p for p in set(files) if p.suffix.lower() not in BINARY_SUFFIXES
                  and "__pycache__" not in p.parts)


def test_user_guide_has_mcp_section_before_tips() -> None:
    """The «MCP серверы» section exists and precedes `## Tips`."""
    text = _read(DOCS_DIR / "USER_GUIDE.md")
    assert MCP_SECTION in text
    assert text.index(MCP_SECTION) < text.index("## Tips")


async def test_user_guide_names_every_registered_tool() -> None:
    """Every tool the project git server registers appears in the guide."""
    tools = await project_git.server.list_tools()
    names = [tool.name for tool in tools]
    assert len(names) == 8
    section = _guide_section()
    for name in names:
        assert name in section, f"{name} is missing from the user guide"


def test_user_guide_local_git_form_values() -> None:
    """The guide gives the exact form values for local-git."""
    section = _guide_section().replace("/", "\\")
    for expected in (
        "local-git", "mcp_servers\\project_git.py", "--root=",
        "--max-chars=18000", "--timeout=10", "Рабочая директория",
    ):
        assert expected in section, f"{expected} is missing"


def test_user_guide_limits_match_code() -> None:
    """The documented limits equal the module constants and the app settings."""
    section = _guide_section()
    assert str(project_git.DEFAULT_MAX_CHARS) in section
    assert str(Settings.model_fields["MCP_TOOL_RESULT_MAX_CHARS"].default) in section


def test_user_guide_github_form_values() -> None:
    """The guide gives the exact form values, install and token steps for github."""
    section = _guide_section()
    for expected in (
        "github-mcp-server.exe", "C:\\Users\\Aleksey\\go\\bin", "stdio", "--read-only",
        "--toolsets=repos,pull_requests", "GITHUB_PERSONAL_ACCESS_TOKEN", "v2.0.2",
        "AlekseyYakovlev/AI-Advent-Challenge-9-v2", "Pull requests", "Contents",
        "Metadata", ENV_MASK,
    ):
        assert expected in section, f"{expected} is missing"


def test_user_guide_does_not_document_go_install() -> None:
    """The guide does not offer `go install` as a way to get the GitHub server."""
    assert "go install" not in _guide_section()


def test_architecture_has_bundled_servers_subsection() -> None:
    """The architecture document describes the bundled servers inside the tool-call section."""
    text = _read(DOCS_DIR / "ARCHITECTURE.md")
    start = text.index("## Chat tool calls (built-in + MCP)")
    sub = text.index("### Bundled MCP servers")
    end = text.index("## Scheduler")
    assert start < sub < end
    span = text[sub:end]
    assert "mcp_servers/project_git.py" in span
    assert "--read-only" in span
    for name in (
        "git_current_branch", "git_status", "git_diff", "git_tracked_files", "git_recent_commits",
    ):
        assert name in span


def test_user_guide_project_files_form_values() -> None:
    """The guide gives the exact form values for the project-files entry."""
    section = _guide_section()
    assert "### Ассистент по файлам проекта (project-files)" in section
    normalised = section.replace("/", "\\")
    for expected in (
        "project-files", "mcp_servers\\project_git.py", "--root=", "--allow-write",
        "Рабочая директория",
    ):
        assert expected in normalised, f"{expected} is missing"


def test_user_guide_names_write_tools() -> None:
    """The guide names the write tools, the size limit and the app tool prefix."""
    section = _guide_section()
    for expected in ("write_file", "edit_file", "mcp__project-files__"):
        assert expected in section, f"{expected} is missing"
    assert "200000" in section or "200 000" in section, "the write size limit is missing"


def test_user_guide_keeps_local_git_read_only() -> None:
    """The local-git subsection stays read-only and /help keeps using it."""
    section = _guide_section()
    start = section.index("### Сервер git проекта (local-git)")
    end = section.index("### Ассистент по файлам проекта (project-files)")
    sub = section[start:end]
    assert "только для чтения" in sub, "the read-only statement is missing"
    assert "/help" in sub, "the /help note is missing"


def test_architecture_describes_file_tools() -> None:
    """The bundled-servers span covers the read tools and the write gate."""
    text = _read(DOCS_DIR / "ARCHITECTURE.md")
    span = text[text.index("### Bundled MCP servers"):text.index("## Scheduler")]
    for expected in (
        "read_file", "search_text", "find_usages", "write_file", "edit_file", "--allow-write",
    ):
        assert expected in span, f"{expected} is missing"


def test_testing_guide_lists_phase26_scripts() -> None:
    """The testing guide names the project files scripts and they exist."""
    text = _read(DOCS_DIR / "TESTING_GUIDE.md")
    for name in ("e2e_project_files_playwright.py", "files_task.py", "files_eval.py"):
        assert name in text, f"{name} is missing from the testing guide"
        assert (REPO_ROOT / "scripts" / name).is_file(), f"scripts/{name} does not exist"


def test_api_spec_has_project_server_examples() -> None:
    """The API specification shows both project server request bodies and the masked response."""
    text = _read(DOCS_DIR / "API_SPEC.md")
    for expected in ("local-git", "--toolsets=repos,pull_requests", "env_keys"):
        assert expected in text


def test_testing_guide_lists_new_test_files() -> None:
    """The testing guide names every new test file and the browser script."""
    text = _read(DOCS_DIR / "TESTING_GUIDE.md")
    for name in TEST_FILES:
        assert name in text
        assert (REPO_ROOT / "tests" / name).is_file()
    assert "e2e_mcp_git_playwright.py" in text


@pytest.mark.parametrize("path", _scanned_files(), ids=lambda p: p.name)
def test_no_github_token_in_repository_text(path: Path) -> None:
    """No scanned file contains a token-shaped string (the failure names the file only)."""
    found = TOKEN_PATTERN.search(_read(path)) is not None
    assert not found, f"token-shaped string found in {path.relative_to(REPO_ROOT)}"
