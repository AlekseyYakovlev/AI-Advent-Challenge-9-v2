"""Schema checks for the RAG threshold calibration set."""

import json
import re
from pathlib import Path
from typing import Any

FIXTURES: Path = Path(__file__).parent / "fixtures" / "rag"
FIXTURE: Path = FIXTURES / "calibration_set.json"
CONTROL: Path = FIXTURES / "control_set.json"
ARTICLE_PATTERN: re.Pattern[str] = re.compile(r"^\d+(?:[._-]\d+)*$")
DATE_PATTERN: re.Pattern[str] = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load(path: Path = FIXTURE) -> dict[str, Any]:
    """Read a fixture file."""
    return json.loads(path.read_text(encoding="utf-8"))


def _questions(category: str | None = None) -> list[dict[str, Any]]:
    """Return all calibration questions, or those of one category."""
    items: list[dict[str, Any]] = _load()["questions"]
    return [q for q in items if category is None or q["category"] == category]


def _pairs(questions: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Set of (file_contains, article) pairs expected by the questions."""
    return {(s["file_contains"], s["article"]) for q in questions for s in q["expected_sources"]}


def test_twenty_questions_with_ordered_ids() -> None:
    ids: list[str] = [q["id"] for q in _questions()]
    assert ids == [f"C{n:02d}" for n in range(1, 21)]
    assert len(set(ids)) == len(ids)


def test_category_counts() -> None:
    assert len(_questions("answerable")) == 12
    assert len(_questions("out_of_corpus")) == 8
    assert len(_questions()) == 20


def test_questions_and_answers_non_empty() -> None:
    for q in _questions():
        assert q["question"].strip()
        assert q["expected_answer"].strip()


def test_answerable_have_well_formed_sources() -> None:
    for q in _questions("answerable"):
        assert q["expected_sources"]
        for source in q["expected_sources"]:
            assert source["file_contains"].strip()
            assert ARTICLE_PATTERN.match(source["article"])


def test_out_of_corpus_have_no_sources() -> None:
    for q in _questions("out_of_corpus"):
        assert q["expected_sources"] == []


def test_disjoint_from_control_set() -> None:
    control: list[dict[str, Any]] = _load(CONTROL)["questions"]
    assert not {q["question"] for q in control} & {q["question"] for q in _questions()}
    assert not _pairs(control) & _pairs(_questions())


def test_status_value() -> None:
    data: dict[str, Any] = _load()
    assert data["status"] in {"draft", "frozen"}
    if data["status"] == "frozen":
        assert DATE_PATTERN.match(str(data["frozen_at"]))
