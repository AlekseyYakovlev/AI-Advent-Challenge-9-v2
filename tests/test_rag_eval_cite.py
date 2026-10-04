"""Tests for the Day 24 cite run of the offline RAG evaluation script, with fakes only."""

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from agent import kb_search, rag
from agent.kb_indexer import run_index_job
from agent.llm_client import ChatCompletionResult
from agent.rag_cite import STRICT_INSTRUCTION
from kb_helpers import install_fake_embedder, seed_kb, seed_user, vector_for
from shared.database import async_session_factory

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("rag_eval_cite", REPO_ROOT / "scripts" / "rag_eval.py")
rag_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rag_eval)

DOC = {"doc.txt": " ".join(f"Раздел номер {i} описывает тему {i * 7919}." for i in range(80))}
FRAGMENT = "Водитель обязан иметь при себе водительское удостоверение и регистрационные документы."
STRICT_ANSWER = f"Нужны документы [1].\n\nЦитаты:\n[1] «{FRAGMENT}»"


class FakeClient:
    """Records completion requests and returns a scripted answer."""

    def __init__(self, answer: str = STRICT_ANSWER, finish_reason: str = "stop") -> None:
        self.calls: list[list[dict[str, Any]]] = []
        self.answer = answer
        self.finish_reason = finish_reason

    async def complete_chat_detailed(
        self, messages: list[dict[str, Any]], model: str, temperature: float = 0.0,
        max_tokens: int = 1024, extra_body: dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        self.calls.append(messages)
        return ChatCompletionResult(self.answer, self.finish_reason, False, 11)


def _chunk() -> dict[str, Any]:
    return {
        "rank": 1,
        "score": 0.8,
        "chunk_id": "1-1",
        "source": "doc.txt",
        "title": None,
        "section": "Статья 1",
        "page": 1,
        "text": FRAGMENT,
    }


def _trace(below: bool) -> dict[str, Any]:
    return {
        "verdict": "below_threshold" if below else "ok",
        "config": {"candidate_k": 2, "top_k": 5, "threshold": 0.59},
        "stages": ["threshold"], "stage_ms": {}, "skipped": [], "latency_ms": 1,
        "candidates": [
            {"chunk_id": "1-1", "file": "doc.txt", "section": "Статья 1", "rank_before": 1,
             "rank_after": None if below else 1, "cos": 0.4 if below else 0.8,
             "status": "below_threshold" if below else "in_answer"},
        ],
    }


@pytest.fixture
def scripted_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent import rag_pipeline

    async def fake(session: Any, kb: Any, question: str, config: Any, client: Any = None, model: Any = None) -> Any:
        index = int(question.split()[1].rstrip("?"))
        if index == 3:
            return [], _trace(True)
        return [_chunk()], _trace(False)

    monkeypatch.setattr(rag_pipeline, "run_retrieval_pipeline", fake)


async def _ready_kb(monkeypatch: pytest.MonkeyPatch) -> int:
    install_fake_embedder(monkeypatch)

    async def fake_query(model_id: str, query: str, *args: object) -> list[float]:
        return vector_for(query)

    monkeypatch.setattr(kb_search, "embed_query", fake_query)
    user_id = await seed_user()
    kb_id = await seed_kb(user_id, DOC, chunk_size=120, chunk_overlap=10)
    await run_index_job(kb_id, user_id)
    return kb_id


def _fixture(tmp_path: Path) -> Path:
    questions = [
        {
            "id": f"Q0{i}",
            "category": category,
            "question": f"Вопрос {i}?",
            "expected_answer": "x",
            "expected_sources": [{"file_contains": "doc", "article": "1"}] if category != "out_of_corpus" else [],
        }
        for i, category in ((1, "direct"), (2, "synthesis"), (3, "out_of_corpus"))
    ]
    path = tmp_path / "control_set.json"
    path.write_text(
        json.dumps({"version": 1, "status": "frozen", "questions": questions}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _opts(tmp_path: Path, kb_id: int, **kwargs: Any) -> Any:
    return rag_eval.EvalOptions(
        kb={"a": kb_id}, fixture=_fixture(tmp_path), out=tmp_path / "out", max_tokens=8192, **kwargs
    )


def test_classify_reply_kinds() -> None:
    from agent.rag_cite import CitationResult

    ok = {"answer": "text", "error": None}
    assert rag_eval.classify_reply(True, True, None, {"answer": "Не знаю", "error": None}) == "gated"
    assert rag_eval.classify_reply(True, False, None, {"answer": "", "error": "X"}) == "error"
    assert rag_eval.classify_reply(True, False, None, {"answer": "  ", "error": None}) == "empty"
    assert rag_eval.classify_reply(True, False, CitationResult("Не знаю", model_idk=True), ok) == "model_idk"
    assert rag_eval.classify_reply(True, False, CitationResult("text"), ok) == "answer"
    assert rag_eval.classify_reply(False, False, None, ok) == "answer"
    assert rag_eval.classify_reply(False, False, None, {"answer": "", "finish_reason": "length", "error": None}) == "empty"


async def test_strict_below_threshold_makes_no_model_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scripted_pipeline: None
) -> None:
    kb_id = await _ready_kb(monkeypatch)
    client = FakeClient()
    records = await rag_eval.run_cite(
        _opts(tmp_path, kb_id), client, async_session_factory, ["strict"], kb_id, 20, 0.59
    )
    gated = next(r for r in records if r["id"] == "Q03")
    assert gated["kind"] == "gated" and gated["gated"] is True
    assert gated["answer"].startswith("Не знаю") and gated["finish_reason"] is None
    assert len(client.calls) == 2


async def test_strict_off_always_calls_client_and_has_no_quotes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scripted_pipeline: None
) -> None:
    kb_id = await _ready_kb(monkeypatch)
    client = FakeClient(answer="Ответ [1]")
    records = await rag_eval.run_cite(
        _opts(tmp_path, kb_id), client, async_session_factory, ["strict_off"], kb_id, 20, 0.59
    )
    assert len(client.calls) == 3
    assert all(r["quotes"] == [] and r["kind"] == "answer" for r in records)
    below = next(r for r in records if r["id"] == "Q03")
    assert rag.NO_FRAGMENTS_INSTRUCTION in below["messages"][-1]["content"]
    first = next(r for r in records if r["id"] == "Q01")
    assert rag.RAG_INSTRUCTION in first["messages"][-1]["content"]
    assert STRICT_INSTRUCTION not in first["messages"][-1]["content"]


async def test_strict_record_has_clean_answer_quotes_and_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scripted_pipeline: None
) -> None:
    kb_id = await _ready_kb(monkeypatch)
    records = await rag_eval.run_cite(
        _opts(tmp_path, kb_id), FakeClient(), async_session_factory, ["strict"], kb_id, 20, 0.59
    )
    first = next(r for r in records if r["id"] == "Q01")
    assert first["messages"][-1]["content"].count(STRICT_INSTRUCTION) == 1
    assert "Цитаты:" in first["raw_answer"] and "Цитаты:" not in first["answer"]
    assert first["kind"] == "answer" and first["cite_verdict"] == "ok"
    assert [q["state"] for q in first["quotes"]] == ["exact"]
    assert first["quote_counts"] == {"exact": 1, "fuzzy": 0, "unverified": 0, "auto": 0}
    assert first["sources"][0]["file"] == "doc.txt"


async def test_empty_answer_is_its_own_kind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scripted_pipeline: None
) -> None:
    kb_id = await _ready_kb(monkeypatch)
    records = await rag_eval.run_cite(
        _opts(tmp_path, kb_id), FakeClient(answer="", finish_reason="length"),
        async_session_factory, ["strict"], kb_id, 20, 0.59,
    )
    first = next(r for r in records if r["id"] == "Q01")
    assert first["kind"] == "empty" and first["quotes"] == []


async def test_run_cite_writes_raw_files_in_run_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scripted_pipeline: None
) -> None:
    kb_id = await _ready_kb(monkeypatch)
    opts = _opts(tmp_path, kb_id)
    records = await rag_eval.run_cite(
        opts, FakeClient(), async_session_factory, ["strict", "strict_off"], kb_id, 20, 0.59
    )
    assert [(r["run"], r["id"]) for r in records] == [
        (run, f"Q0{i}") for run in ("strict", "strict_off") for i in (1, 2, 3)
    ]
    loaded = rag_eval._load_raw(opts.out / "raw")
    assert len(loaded) == 6
    assert {r["run"] for r in loaded} == {"strict", "strict_off"}
