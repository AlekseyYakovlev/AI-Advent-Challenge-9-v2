"""Guards keeping the support FAQ true to the code and free of keys."""

import re
from pathlib import Path

from agent.dependencies import MSG_INVALID_API_KEY
from agent.limits import MSG_LOGIN_BLOCKED
from shared.auth import SESSION_TTL_DAYS
from shared.config import Settings

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
DOCS_DIR: Path = REPO_ROOT / "docs"
FAQ_PATH: Path = DOCS_DIR / "FAQ.md"
KEY_PATTERN: re.Pattern[str] = re.compile(r"sk-[A-Za-z0-9]{20,}")
API_KEY_PATTERN: re.Pattern[str] = re.compile(r"aia_[A-Za-z0-9_\-]{20,}")
MAX_SECTION_CHARS: int = 1500


def _faq() -> str:
    """Return the FAQ text."""
    return FAQ_PATH.read_text(encoding="utf-8")


def _sections(text: str) -> list[str]:
    """Split the text into its `## ` sections, each starting with its heading."""
    return [part for part in re.split(r"(?m)^(?=## )", text) if part.startswith("## ")]


def _default(name: str) -> object:
    """Return the code default of a setting, ignoring any local .env override."""
    return Settings.model_fields[name].default


def test_faq_values_match_code() -> None:
    """Numbers, defaults and error texts in the FAQ equal the values used by the code."""
    text = _faq()
    for name in (
        "LOGIN_MAX_FAILED_ATTEMPTS",
        "LOGIN_LOCKOUT_SECONDS",
        "RATE_LIMIT_MESSAGES_PER_MINUTE",
    ):
        assert name in text
        assert str(_default(name)) in text
    assert str(SESSION_TTL_DAYS) in text
    assert MSG_LOGIN_BLOCKED in text
    assert MSG_INVALID_API_KEY in text
    assert "Cross-origin request rejected" in text
    assert "session_id" in text
    assert "BIND_HOST" in text


def test_faq_sections_are_symptom_first() -> None:
    """Each problem is a short `## ` section so it stays one retrievable fragment."""
    sections = _sections(_faq())
    assert len(sections) >= 8
    for section in sections:
        assert len(section) <= MAX_SECTION_CHARS, section.splitlines()[0]


def test_faq_has_no_keys() -> None:
    """The FAQ carries no key-shaped strings."""
    text = _faq()
    assert KEY_PATTERN.search(text) is None
    assert API_KEY_PATTERN.search(text) is None


def test_seed_data_not_in_docs() -> None:
    """Ticket seed data stays out of the documentation base."""
    assert list(DOCS_DIR.rglob("*.json")) == []
    assert "support_data.json" not in _faq()
