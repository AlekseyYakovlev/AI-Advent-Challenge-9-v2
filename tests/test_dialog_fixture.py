"""Shape checks for the dialog scenarios fixture."""

import json
import re
from pathlib import Path
from typing import Any

FIXTURE: Path = Path(__file__).parent / "fixtures" / "rag" / "dialog_scenarios.json"
ARTICLE_PATTERN: re.Pattern[str] = re.compile(r"^\d+(?:\.\d+)*$")
KINDS: set[str] = {"direct", "followup", "out_of_corpus", "goal_check"}
MEMORY_KEYS: set[str] = {"goal", "clarified", "constraints"}


def _load() -> dict[str, Any]:
    """Read the dialog scenarios fixture."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _scenarios() -> list[dict[str, Any]]:
    """Return the scenario list."""
    items: list[dict[str, Any]] = _load()["scenarios"]
    return items


def _turns(scenario: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """Return the turns of a scenario that have the given kind."""
    turns: list[dict[str, Any]] = scenario["turns"]
    return [t for t in turns if t["kind"] == kind]


def _normalise(text: str) -> str:
    """Lowercase and trim a user message for comparison."""
    return text.strip().lower()


def test_envelope() -> None:
    data: dict[str, Any] = _load()
    assert data["version"] == 1
    assert data["corpus"] == ["ФЗ-196", "КоАП РФ"]
    assert data["status"] in {"draft", "frozen"}


def test_exactly_two_scenarios() -> None:
    assert [s["id"] for s in _scenarios()] == ["A", "B"]


def test_scenario_metadata() -> None:
    for s in _scenarios():
        assert s["title"].strip()
        assert s["goal"].strip()
        assert 2 <= len(s["goal_keywords"]) <= 4
        assert all(k == k.lower() and k.strip() for k in s["goal_keywords"])


def test_turn_counts() -> None:
    for s in _scenarios():
        assert 10 <= len(s["turns"]) <= 15


def test_turn_ids_unique_prefixed_ordered() -> None:
    for s in _scenarios():
        ids: list[str] = [t["id"] for t in s["turns"]]
        assert ids == [f"{s['id']}{n:02d}" for n in range(1, len(ids) + 1)]
        assert len(set(ids)) == len(ids)


def test_kinds_allowed() -> None:
    for s in _scenarios():
        assert {t["kind"] for t in s["turns"]} <= KINDS


def test_single_out_of_corpus_and_goal_check() -> None:
    for s in _scenarios():
        assert len(_turns(s, "out_of_corpus")) == 1
        assert len(_turns(s, "goal_check")) == 1


def test_goal_check_in_last_three_turns() -> None:
    for s in _scenarios():
        last_ids: list[str] = [t["id"] for t in s["turns"][-3:]]
        assert _turns(s, "goal_check")[0]["id"] in last_ids


def test_followups_have_articles() -> None:
    for s in _scenarios():
        followups: list[dict[str, Any]] = _turns(s, "followup")
        assert len(followups) >= 4
        assert sum(1 for t in followups if t["expect_article"] is not None) >= 2


def test_article_format_and_out_of_corpus_null() -> None:
    for s in _scenarios():
        for t in s["turns"]:
            if t["expect_article"] is not None:
                assert ARTICLE_PATTERN.match(t["expect_article"])
        assert _turns(s, "out_of_corpus")[0]["expect_article"] is None


def test_texts_non_empty() -> None:
    for s in _scenarios():
        assert all(t["text"].strip() for t in s["turns"])


def test_expect_memory_keys() -> None:
    for s in _scenarios():
        for t in s["turns"]:
            memory: dict[str, list[str]] = t["expect_memory"]
            assert set(memory) <= MEMORY_KEYS
            assert all(isinstance(v, list) and v for v in memory.values())
            assert all(e == e.lower() for v in memory.values() for e in v)


def test_expect_keywords_lowercase() -> None:
    for s in _scenarios():
        for t in s["turns"]:
            assert all(k == k.lower() for k in t["expect_keywords"])


def test_scenario_a_has_repeat_followup_after_speeding() -> None:
    turns: list[dict[str, Any]] = _scenarios()[0]["turns"]
    texts: list[str] = [_normalise(t["text"]) for t in turns]
    assert "а за повторное?" in texts
    index: int = texts.index("а за повторное?")
    assert index == 1
    assert turns[index]["kind"] == "followup"
    assert turns[index]["expect_article"] == "12.9"


def test_constraint_fixed_early() -> None:
    a_first: dict[str, Any] = _scenarios()[0]["turns"][0]
    assert "коап" in a_first["expect_memory"].get("constraints", [])
    b_early: list[dict[str, Any]] = _scenarios()[1]["turns"][:3]
    assert any(t["expect_memory"].get("constraints") for t in b_early)


def test_frozen_requires_date() -> None:
    data: dict[str, Any] = _load()
    if data["status"] == "frozen":
        assert isinstance(data["frozen_at"], str) and data["frozen_at"].strip()
