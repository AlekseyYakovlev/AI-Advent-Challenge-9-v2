"""Guards that keep the documents true to the knowledge base upload behaviour."""

import re
from pathlib import Path

import pytest

from agent import kb_api
from agent.kb_limits import MAX_FILES

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
DOCS: Path = REPO_ROOT / "docs"

NEW_TEST_FILES: tuple[str, ...] = (
    "test_kb_migration.py",
    "test_kb_add_job.py",
    "test_kb_add_api.py",
    "test_kb_folder_ui.py",
    "test_kb_add_ui.py",
    "test_readme.py",
    "test_docs_kb_upload.py",
)


def _read(relative: str) -> str:
    """Return a repository file as UTF-8 text."""
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """Return the text between a '## ' heading and the next '## ' heading."""
    start = text.index(heading) + len(heading)
    match = re.search(r"^## ", text[start:], flags=re.MULTILINE)
    end = start + match.start() if match else len(text)
    return text[start:end]


@pytest.mark.parametrize(
    ("path", "stale"),
    [
        ("docs/USER_GUIDE.md", "до 10 файлов"),
        ("docs/USER_GUIDE.md", "Одинаковые файлы добавить нельзя"),
        ("docs/API_SPEC.md", "up to 10 files"),
        ("docs/API_SPEC.md", "SHA-256 de-duplicated"),
        ("docs/API_SPEC.md", "не больше 10 файлов"),
        ("docs/ARCHITECTURE.md", "unique on `(kb_id, sha256)`"),
    ],
)
def test_no_stale_upload_rules(path: str, stale: str) -> None:
    """A rule removed from the product no longer appears in the documents."""
    assert stale not in _read(path)


def test_api_spec_lists_new_routes() -> None:
    """The API specification names the limits route, the add route and the new fields."""
    text = _read("docs/API_SPEC.md")
    for needle in ("/api/v1/kb/limits", "/api/v1/kb/{kb_id}/files", "add_summary", "adding"):
        assert needle in text


def test_api_spec_messages_match_code() -> None:
    """The messages quoted in the API specification equal the ones the code sends."""
    text = _read("docs/API_SPEC.md")
    for message in (kb_api.MSG_TOO_MANY_FILES, kb_api.MSG_ADD_NOT_READY, kb_api.MSG_ADD_BUSY):
        assert message in text


def test_limit_number_matches_constant() -> None:
    """The documented file limit is the value of MAX_FILES."""
    guide = _section(_read("docs/USER_GUIDE.md"), "## База знаний")
    spec = _section(_read("docs/API_SPEC.md"), "## Knowledge bases (Day 21)")
    assert str(MAX_FILES) in guide
    assert str(MAX_FILES) in spec


def test_user_guide_describes_folder_and_addition() -> None:
    """The user guide explains folder choice, adding files and replacement."""
    guide = _section(_read("docs/USER_GUIDE.md"), "## База знаний")
    for needle in ("Выбрать папку", "Добавить файлы", "заменено", "docs/"):
        assert needle in guide


def test_architecture_describes_addition() -> None:
    """The architecture document covers the identity index, staging and the addition flow."""
    text = _read("docs/ARCHITECTURE.md")
    for needle in ("ix_kbdocument_kb_filename", "_staging", "Addition to a ready base"):
        assert needle in text


def test_testing_guide_lists_new_files() -> None:
    """Every new test file and the folder script are listed, and the test files exist."""
    text = _read("docs/TESTING_GUIDE.md")
    for name in NEW_TEST_FILES:
        assert name in text
        assert (REPO_ROOT / "tests" / name).is_file()
    assert "scripts/e2e_kb_folder_playwright.py" in text
    assert (REPO_ROOT / "scripts" / "e2e_kb_folder_playwright.py").is_file()


def test_readme_mentions_docs_knowledge_base() -> None:
    """The README has the section about loading the project docs."""
    text = _read("README.md")
    assert "## Knowledge base from the project docs" in text
    assert "docs/API_SPEC.md" in text
