"""Guards keeping the PR review documentation true to the code and free of secrets."""

import re
from pathlib import Path

import pytest

from agent.review_api import STATUS_BY_CODE

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
DOCS_DIR: Path = REPO_ROOT / "docs"
WORKFLOW: Path = REPO_ROOT / ".github" / "workflows" / "pr-review.yml"
CI_SCRIPT: Path = REPO_ROOT / "scripts" / "pr_review_ci.py"
GUIDE_HEADING: str = "## Автоматическое ревью Pull Request"
KEY_PATTERN: re.Pattern[str] = re.compile(r"aia_[A-Za-z0-9_-]{20,}|ghp_|github_pat_")
FAILURE_PREFIXES: tuple[str, ...] = ("Ревью не выполнено: ", "Комментарий не опубликован: ")
GUIDE_PHRASES: tuple[str, ...] = (
    "PR_REVIEW_API_KEY",
    "pr-review",
    "Require approval for all external contributors",
    "--ephemeral",
    "CLAUDE.md",
    "config.cmd",
    "run.cmd",
    "приват",
)
NEW_ERROR_CODES: tuple[str, ...] = ("REVIEW_BUSY", "REVIEW_INDEX_BUSY", "INTERNAL_ERROR")
NEW_ROUTES: tuple[str, ...] = (
    "/api/v1/reviews",
    "/api/v1/reviews/config",
    "/api/v1/reviews/code-index",
    "/api/v1/reviews/code-index/refresh",
)


def _read(path: Path) -> str:
    """Return a file as UTF-8 text with Windows line endings normalised."""
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def _guide_section() -> str:
    """Return the PR review section of the user guide."""
    text = _read(DOCS_DIR / "USER_GUIDE.md")
    start = text.index(GUIDE_HEADING)
    match = re.search(r"^## ", text[start + len(GUIDE_HEADING):], flags=re.MULTILINE)
    end = start + len(GUIDE_HEADING) + match.start() if match else len(text)
    return text[start:end]


def test_guide_section_precedes_tips() -> None:
    """The section exists in the user guide before `## Tips`."""
    text = _read(DOCS_DIR / "USER_GUIDE.md")
    assert text.index(GUIDE_HEADING) < text.index("## Tips")


@pytest.mark.parametrize("phrase", GUIDE_PHRASES)
def test_guide_names_the_setup_and_safety_phrases(phrase: str) -> None:
    """The guide has the setup names and the safe configuration phrases."""
    assert phrase in _guide_section()


def test_guide_says_private_repository_is_the_only_complete_fix() -> None:
    """The residual risk is stated and the private repository is named the complete fix."""
    section = _guide_section()
    assert re.search(r"Единственная полная защита[^.]*приватн", section)


def test_guide_names_main_and_day32_in_one_limit_sentence() -> None:
    """One sentence names both branches and says Pull Requests against `main` wait."""
    section = _guide_section()
    sentences = re.split(r"(?<=[.;])\s+", section)
    assert any("`main`" in s and "`Day32`" in s and "не ревьюируются" in s for s in sentences)


def test_guide_secret_and_variable_names_equal_the_workflow() -> None:
    """The names of the secret and the variable are the ones the workflow reads."""
    workflow = _read(WORKFLOW)
    section = _guide_section()
    secrets = set(re.findall(r"secrets\.([A-Z0-9_]+)", workflow))
    variables = set(re.findall(r"vars\.([A-Z0-9_]+)", workflow))
    assert secrets == {"PR_REVIEW_API_KEY"}
    assert variables == {"PR_REVIEW_APP_URL"}
    for name in secrets | variables:
        assert name in section
    assert set(re.findall(r"PR_REVIEW_[A-Z_]+", section)) <= secrets | variables


def test_guide_runner_label_equals_the_workflow() -> None:
    """The label in the registration command is the label the job asks for."""
    assert "pr-review]" in _read(WORKFLOW)
    assert "--labels pr-review" in _guide_section()


def _failure_leads() -> list[str]:
    """Return the first three words of every failure line literal of the CI script."""
    source = _read(CI_SCRIPT)
    leads: list[str] = []
    for prefix in FAILURE_PREFIXES:
        for match in re.finditer(re.escape(prefix) + r"([^{\"(:.\n]+)", source):
            words = match.group(1).split()[:3]
            if words:
                leads.append(" ".join(words))
    return leads


def test_failure_lines_are_found_in_the_script() -> None:
    """The extraction sees the failure lines at all, so the next test cannot pass empty."""
    assert len(_failure_leads()) >= 8


def test_every_failure_line_is_explained_in_the_guide() -> None:
    """The leading words of each failure line of the script appear in the guide."""
    section = _guide_section()
    for lead in _failure_leads():
        assert lead in section, lead


@pytest.mark.parametrize("code", sorted(STATUS_BY_CODE))
def test_api_spec_lists_every_status_code(code: str) -> None:
    """Every code the route maps to a status appears in the API reference."""
    assert code in _read(DOCS_DIR / "API_SPEC.md")


@pytest.mark.parametrize("name", NEW_ERROR_CODES + NEW_ROUTES)
def test_api_spec_lists_routes_and_extra_codes(name: str) -> None:
    """The route paths and the codes that are not in the status table are documented."""
    assert name in _read(DOCS_DIR / "API_SPEC.md")


def test_api_spec_statuses_match_the_code() -> None:
    """Each documented code is listed with the HTTP status the route uses."""
    text = _read(DOCS_DIR / "API_SPEC.md")
    for code, status in STATUS_BY_CODE.items():
        assert re.search(rf"`{code}` \| {status} \|", text), code


def test_readme_mentions_the_workflow() -> None:
    """The README names the workflow file and the caller script."""
    text = _read(REPO_ROOT / "README.md")
    assert "pr-review.yml" in text
    assert "pr_review_ci.py" in text


@pytest.mark.parametrize("name", ["API_SPEC.md", "ARCHITECTURE.md", "TESTING_GUIDE.md", "USER_GUIDE.md"])
def test_docs_hold_no_key_shaped_string(name: str) -> None:
    """No document contains a key or token shaped string."""
    assert KEY_PATTERN.search(_read(DOCS_DIR / name)) is None


def test_readme_holds_no_key_shaped_string() -> None:
    """The README contains no key or token shaped string."""
    assert KEY_PATTERN.search(_read(REPO_ROOT / "README.md")) is None
