"""Tests for the offline RAG evaluation script: scoring, rendering and a fake end-to-end run."""

import csv
import importlib.util
import io
import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("rag_eval", REPO_ROOT / "scripts" / "rag_eval.py")
rag_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rag_eval)

FZ = "19951210_20260626_FZ_N_196_FZ.pdf"
KOAP = "Kodex_N_195-FZ.pdf"


def _chunk(source: str, section: str, rank: int = 1) -> dict[str, Any]:
    return {
        "rank": rank,
        "score": 0.9 - rank / 100,
        "chunk_id": f"1-{rank}",
        "source": source,
        "title": None,
        "section": section,
        "page": rank,
        "text": f"text {rank}",
    }


def test_parse_article() -> None:
    assert rag_eval.parse_article("ФЗ-196 > Глава 4 > Статья 19. Подготовка водителей") == "19"
    assert rag_eval.parse_article("Статья 12.9. Превышение") == "12.9"
    assert rag_eval.parse_article("Статья 5. A > Статья 12.9. B") == "12.9"
    assert rag_eval.parse_article(None) is None
    assert rag_eval.parse_article("Глава 4") is None


def test_chunk_hits_requires_file_and_exact_article() -> None:
    chunk = _chunk(FZ, "ФЗ > Статья 19. Подготовка")
    assert rag_eval.chunk_hits(chunk, {"file_contains": "FZ_N_196_FZ", "article": "19"}) is True
    assert rag_eval.chunk_hits(chunk, {"file_contains": "FZ_N_196_FZ", "article": "19.1"}) is False
    assert rag_eval.chunk_hits(chunk, {"file_contains": "FZ_N_196_FZ", "article": "1"}) is False
    koap = _chunk(KOAP, "Статья 19. Другое")
    assert rag_eval.chunk_hits(koap, {"file_contains": "FZ_N_196_FZ", "article": "19"}) is False


def test_chunk_hits_falls_back_to_title() -> None:
    chunk = _chunk(FZ, "")
    chunk["section"] = None
    chunk["title"] = "Статья 26. Возраст"
    assert rag_eval.chunk_hits(chunk, {"file_contains": "FZ", "article": "26"}) is True


def test_hit_at_k_and_rank() -> None:
    chunks = [_chunk(KOAP, "Статья 1. x", 1), _chunk(FZ, "Статья 26. y", 2), _chunk(FZ, "Статья 17. z", 3)]
    expected = [{"file_contains": "FZ_N_196_FZ", "article": "26"}]
    assert rag_eval.hit_at_k(chunks, expected, 1) is False
    assert rag_eval.hit_at_k(chunks, expected, 3) is True
    assert rag_eval.first_hit_rank(chunks, expected) == 2
    assert rag_eval.hit_at_k(chunks, [], 3) is None
    assert rag_eval.first_hit_rank(chunks, [{"file_contains": "FZ", "article": "99"}]) is None


def test_all_hit_at_k_requires_every_source() -> None:
    chunks = [_chunk(FZ, "Статья 19. a", 1), _chunk(KOAP, "Статья 12.37. b", 2)]
    both = [
        {"file_contains": "FZ_N_196_FZ", "article": "19"},
        {"file_contains": "N_195", "article": "12.37"},
    ]
    assert rag_eval.all_hit_at_k(chunks, both, 1) is False
    assert rag_eval.all_hit_at_k(chunks, both, 2) is True
    assert rag_eval.all_hit_at_k(chunks, [], 2) is None


def test_load_fixture_requires_frozen(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    path.write_text(json.dumps({"status": "draft", "questions": []}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        rag_eval.load_fixture(path, require_frozen=True)
    assert exc.value.code == 2
    assert rag_eval.load_fixture(path, require_frozen=False)["status"] == "draft"
    assert len(rag_eval.fixture_sha256(path)) == 64


def test_render_retrieval_table() -> None:
    rows = [
        {
            "id": "Q01",
            "category": "direct",
            "kb": {"a": {"hit1": True, "hit3": True, "hit5": True, "hitk": True, "rank": 1, "top1": 0.5}},
        },
        {
            "id": "Q09",
            "category": "out_of_corpus",
            "kb": {"a": {"hit1": None, "hit3": None, "hit5": None, "hitk": None, "rank": None, "top1": 0.25}},
        },
    ]
    table = rag_eval.render_retrieval_table(rows, ["a"], 5)
    lines = table.strip().splitlines()
    assert "a hit@1" in lines[0] and "a hit@5" in lines[0] and "a top1 score" in lines[0]
    assert "| Q01 | direct | 1 | 1 | 1 | 1 | 1 | 0.500 |" == lines[2]
    assert "| Q09 | out_of_corpus | n/a | n/a | n/a | n/a | n/a | 0.250 |" == lines[3]


def test_render_answers_csv_has_empty_verdict() -> None:
    rows = [
        {"id": "Q01", "category": "direct", "mode": "rag", "kb": "a", "answer": "18, [1]", "cited_sources": "x"}
    ]
    parsed = list(csv.reader(io.StringIO(rag_eval.render_answers_csv(rows))))
    assert parsed[0] == ["id", "category", "mode", "kb", "answer", "cited_sources", "verdict", "comment"]
    assert parsed[1][6] == "" and parsed[1][7] == ""
    assert parsed[1][4] == "18, [1]"


def test_render_answers_table_truncates() -> None:
    rows = [{"id": "Q1", "category": "direct", "mode": "off", "kb": "", "answer": "я" * 500}]
    table = rag_eval.render_answers_table(rows)
    assert "я" * 300 in table and "я" * 301 not in table


def test_importing_module_creates_no_db() -> None:
    assert not (REPO_ROOT / "eval_out" / "day22" / "eval.db").exists()
