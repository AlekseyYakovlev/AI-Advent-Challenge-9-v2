"""Schema checks for the frozen RAG control set."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

FIXTURE: Path = Path(__file__).parent / "fixtures" / "rag" / "control_set.json"
ARTICLE_PATTERN: re.Pattern[str] = re.compile(r"^\d+(?:[._-]\d+)*$")
DATE_PATTERN: re.Pattern[str] = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load() -> dict[str, Any]:
    """Read the control set fixture."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _questions(category: str | None = None) -> list[dict[str, Any]]:
    """Return all questions, or those of one category."""
    items: list[dict[str, Any]] = _load()["questions"]
    return [q for q in items if category is None or q["category"] == category]


def test_exactly_ten_questions() -> None:
    assert len(_questions()) == 10


def test_ids_unique_and_ordered() -> None:
    ids: list[str] = [q["id"] for q in _questions()]
    assert ids == [f"Q{n:02d}" for n in range(1, 11)]
    assert len(set(ids)) == len(ids)


def test_category_counts() -> None:
    assert len(_questions("direct")) == 6
    assert len(_questions("synthesis")) == 2
    assert len(_questions("out_of_corpus")) == 2
    assert len(_questions()) == 10


def test_questions_and_answers_non_empty() -> None:
    for q in _questions():
        assert q["question"].strip()
        assert q["expected_answer"].strip()


def test_direct_have_single_source() -> None:
    for q in _questions("direct"):
        assert len(q["expected_sources"]) == 1


def test_synthesis_cover_both_documents() -> None:
    for q in _questions("synthesis"):
        files: set[str] = {s["file_contains"] for s in q["expected_sources"]}
        assert len(q["expected_sources"]) == 2
        assert files == {"FZ_N_196_FZ", "N_195-FZ"}


def test_out_of_corpus_have_no_sources() -> None:
    for q in _questions("out_of_corpus"):
        assert q["expected_sources"] == []


def test_article_numbers_well_formed() -> None:
    for q in _questions():
        for source in q["expected_sources"]:
            assert ARTICLE_PATTERN.match(source["article"])


def test_status_value() -> None:
    assert _load()["status"] in {"draft", "frozen"}


def test_fixture_is_frozen() -> None:
    data: dict[str, Any] = _load()
    assert data["status"] == "frozen"
    assert DATE_PATTERN.match(str(data["frozen_at"]))
