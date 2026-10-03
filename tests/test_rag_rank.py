"""Unit tests for the pure ranking, validation and calibration helpers."""

from typing import Any

import pytest

from agent.rag_rank import (
    RRF_K,
    article_numbers,
    build_fts_query,
    choose_threshold,
    lexical_rerank,
    lexical_score,
    parse_article,
    rrf_order,
    stems,
    tokenize,
)


def _chunk(text: str, section: str | None = None, score: float = 0.5) -> dict[str, Any]:
    return {"score": score, "text": text, "section": section, "title": "Кодекс"}


def test_tokenize_drops_short_stop_and_numeric_tokens() -> None:
    assert tokenize("Штраф ЗА превышение на 40 км") == ["штраф", "превышение"]


def test_stems_merge_word_forms() -> None:
    assert stems("превышение превышения") == {"превы"}


def test_article_numbers_dotted_and_word_forms() -> None:
    assert article_numbers("ст. 12.9 и статья 26, а также 12.9") == ["12.9", "26"]


def test_article_numbers_plain_integer_needs_marker() -> None:
    assert article_numbers("в 2024 году 40 км") == []


def test_parse_article_deepest_and_none() -> None:
    assert parse_article("Глава 12 / Статья 12.9. Превышение") == "12.9"
    assert parse_article(None) is None


def test_lexical_score_ordering() -> None:
    query = "штраф за превышение скорости ст. 12.9"
    best = _chunk("Текст.", "Статья 12.9. Превышение установленной скорости")
    mention = _chunk("см. также 12.9 для случаев", "Раздел")
    other = _chunk("Совсем другое про налоги", "Раздел")
    s_best = lexical_score(query, best)
    s_mention = lexical_score(query, mention)
    s_other = lexical_score(query, other)
    assert s_best > s_mention > s_other == 0.0
    assert 0.0 <= s_best <= 1.0


def test_lexical_score_whole_number_only() -> None:
    assert lexical_score("ст. 12.9", _chunk("штраф по 12.91 и 112.9")) == 0.0


def test_lexical_score_empty_query_is_zero() -> None:
    assert lexical_score("за на и", _chunk("что-то 12.9")) == 0.0


def test_lexical_rerank_single_chunk() -> None:
    order, lex = lexical_rerank("штраф", [_chunk("штраф")])
    assert order == [0]
    assert len(lex) == 1


def test_lexical_rerank_equal_cosine_prefers_lexical() -> None:
    chunks = [_chunk("налоги", score=0.6), _chunk("штраф за превышение", score=0.6)]
    order, _ = lexical_rerank("штраф превышение", chunks)
    assert order == [1, 0]


def test_lexical_rerank_stable_on_ties() -> None:
    chunks = [_chunk("aaa", score=0.5), _chunk("bbb", score=0.5), _chunk("ccc", score=0.5)]
    order, _ = lexical_rerank("запрос", chunks)
    assert order == [0, 1, 2]


def test_lexical_rerank_does_not_mutate_input() -> None:
    chunks = [_chunk("штраф", score=0.4), _chunk("налог", score=0.9)]
    snapshot = [dict(c) for c in chunks]
    lexical_rerank("штраф", chunks)
    assert chunks == snapshot


def test_rrf_order_prefers_items_in_both_lists() -> None:
    order = rrf_order([10, 20, 30], [30, 40])
    assert order[0] == 30
    assert sorted(order) == [10, 20, 30, 40]
    assert len(order) == 4


def test_rrf_order_single_list() -> None:
    assert rrf_order([1, 2], []) == [1, 2]
    assert RRF_K == 60


def test_build_fts_query_terms() -> None:
    query = build_fts_query("штраф по ст. 12.9")
    assert query is not None
    assert '"12.9"' in query
    assert '"штраф"*' in query
    assert " OR " in query


def test_build_fts_query_none_for_stopwords_only() -> None:
    assert build_fts_query("за на и") is None


def test_build_fts_query_neutralises_operators() -> None:
    query = build_fts_query('штраф " OR (NOT) NEAR - * : AND ст. 12.9')
    assert query is not None
    assert "(" not in query and ":" not in query and "-" not in query
    assert query.count('"') % 2 == 0
    for term in query.split(" OR "):
        assert term.startswith('"') and (term.endswith('"') or term.endswith('"*'))


def test_choose_threshold_midpoint_rounds_half_up() -> None:
    threshold, stats = choose_threshold([0.63, 0.70, 0.75], [0.52, 0.54])
    assert threshold == 0.59
    assert stats["separable"] is True
    assert stats["method"] == "midpoint"


def test_choose_threshold_youden_when_overlapping() -> None:
    threshold, stats = choose_threshold([0.76, 0.80, 0.84], [0.76, 0.77])
    assert stats["separable"] is False
    assert stats["method"] == "youden"
    assert 0.0 <= threshold <= 1.0


@pytest.mark.parametrize(
    ("gold", "ooc"), [([], [0.5]), ([0.7], [])], ids=["no-gold", "no-ooc"]
)
def test_choose_threshold_none_for_empty(gold: list[float], ooc: list[float]) -> None:
    threshold, stats = choose_threshold(gold, ooc)
    assert threshold == 0.0
    assert stats["method"] == "none"
