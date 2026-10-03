"""Tests for the shared two-stage RAG retrieval pipeline."""

import asyncio
from typing import Any

import pytest

from agent import kb_search, rag
from agent import rag_pipeline
from agent.embeddings import EmbeddingError
from agent.kb_indexer import run_index_job
from agent.llm_client import ChatCompletionResult
from agent.rag import RagFailure, retrieve_vectors
from agent.rag_pipeline import (
    PipelineConfig,
    config_from_row,
    mark_over_budget,
    run_retrieval_pipeline,
)
from kb_helpers import NOMIC, get_kb, install_fake_embedder, seed_kb, seed_user, vector_for
from shared.database import async_session_factory
from shared.models import ChatRagConfig, KbStatus, KnowledgeBase

DISTINCT = " ".join(f"Раздел номер {i} описывает тему {i * 7919}." for i in range(80))
RARE = "Зябликовый реестр содержит записи о редких птицах и их кормушках в северных лесах."
BASE_TEXT = "Раздел номер 3 описывает тему"
QUESTION = "О чём раздел номер 3?"
REWRITTEN = "раздел номер 3 описывает тему"
CHUNK_COUNT_MIN = 20


async def _ready_kb(
    monkeypatch: pytest.MonkeyPatch,
    files: dict[str, str | bytes] | None = None,
    fail_queries: tuple[str, ...] = (),
) -> tuple[int, list[str]]:
    """Index a KB with the fake embedder; returns its id and the list of embedded queries."""
    install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    kb_id = await seed_kb(
        user_id, files or {"doc.txt": DISTINCT}, chunk_size=120, chunk_overlap=10
    )
    await run_index_job(kb_id, user_id)
    assert (await get_kb(kb_id)).status == KbStatus.READY
    calls: list[str] = []

    async def fake_query(model_id: str, query: str, *args: object) -> list[float]:
        calls.append(query)
        if query in fail_queries:
            raise EmbeddingError("scripted failure")
        return vector_for(query if query == REWRITTEN else BASE_TEXT)

    monkeypatch.setattr(kb_search, "embed_query", fake_query)
    return kb_id, calls


async def _run(
    kb_id: int,
    config: PipelineConfig,
    question: str = QUESTION,
    client: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        return await run_retrieval_pipeline(session, kb, question, config, client, "model-x")


def _cfg(**kwargs: Any) -> PipelineConfig:
    return PipelineConfig(**{"candidate_k": 20, "top_k": 5, **kwargs})


def _result(content: str | None) -> ChatCompletionResult:
    return ChatCompletionResult(
        content=content, finish_reason="stop", has_reasoning=False, completion_tokens=5
    )


class FakeClient:
    """Replays scripted replies (or raises scripted errors) for complete_chat_detailed."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def complete_chat_detailed(self, **kwargs: Any) -> ChatCompletionResult:
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FailingClient:
    """Every call fails."""

    async def complete_chat_detailed(self, **kwargs: Any) -> ChatCompletionResult:
        raise RuntimeError("down")


# ---- retrieve_vectors ----


async def test_retrieve_vectors_kb_deleted() -> None:
    async with async_session_factory() as session:
        with pytest.raises(RagFailure) as info:
            await retrieve_vectors(session, None, "q", 3)
    assert info.value.code == "kb_deleted"


async def test_retrieve_vectors_kb_not_ready() -> None:
    user_id = await seed_user()
    kb_id = await seed_kb(user_id, {"doc.txt": DISTINCT})
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        with pytest.raises(RagFailure) as info:
            await retrieve_vectors(session, kb, "q", 3)
    assert info.value.code == "kb_not_ready"


async def test_retrieve_vectors_embedder_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _ready_kb(monkeypatch, fail_queries=(QUESTION,))
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        with pytest.raises(RagFailure) as info:
            await retrieve_vectors(session, kb, QUESTION, 3)
    assert info.value.code == "embedder_unavailable"


async def test_retrieve_vectors_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)

    async def slow(*args: object) -> list[float]:
        await asyncio.sleep(1)
        return vector_for("x")

    monkeypatch.setattr(kb_search, "embed_query", slow)
    monkeypatch.setattr(rag.settings, "RAG_EMBED_TIMEOUT", 0.05)
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        with pytest.raises(RagFailure) as info:
            await retrieve_vectors(session, kb, QUESTION, 3)
    assert info.value.code == "embedder_unavailable"


async def test_retrieve_vectors_returns_row_ids_and_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        results, vector = await retrieve_vectors(session, kb, QUESTION, 3)
    assert len(results) == 3
    assert all("row_id" in item for item in results)
    assert len(vector) == len(vector_for("x"))


# ---- config_from_row ----


async def test_config_from_row_calibrated_user_and_none(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await seed_user()
    kb_id = await seed_kb(user_id, {"doc.txt": DISTINCT}, model=NOMIC)
    kb = await get_kb(kb_id)
    monkeypatch.setattr(rag, "CALIBRATED_THRESHOLDS", {"nomic": 0.55})
    row = ChatRagConfig(chat_id=1, kb_id=kb_id, top_k=5, candidate_k=20, threshold=None)
    config = config_from_row(row, kb)
    assert (config.threshold, config.threshold_source) == (0.55, "calibrated")
    user_row = ChatRagConfig(chat_id=1, kb_id=kb_id, top_k=5, candidate_k=20, threshold=0.3)
    config = config_from_row(user_row, kb)
    assert (config.threshold, config.threshold_source) == (0.3, "user")
    config = config_from_row(row, None)
    assert (config.threshold, config.threshold_source) == (0.0, "none")
    monkeypatch.setattr(rag, "CALIBRATED_THRESHOLDS", {})
    config = config_from_row(row, kb)
    assert (config.threshold, config.threshold_source) == (0.0, "none")


async def test_config_from_row_clamps_candidate_k_and_flags() -> None:
    row = ChatRagConfig(
        chat_id=1, top_k=8, candidate_k=3, lexical=True, llm_rerank=None, hybrid=True, rewrite=False
    )
    config = config_from_row(row, None)
    assert config.candidate_k == 8
    assert (config.lexical, config.llm_rerank, config.hybrid, config.rewrite) == (
        True,
        False,
        True,
        False,
    )


# ---- two-stage core ----


async def test_default_config_is_threshold_only(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)
    chunks, trace = await _run(kb_id, _cfg())
    assert len(chunks) == 5
    candidates = trace["candidates"]
    assert [item["rank_before"] for item in candidates] == list(range(1, 21))
    assert [item["status"] for item in candidates[:5]] == ["in_answer"] * 5
    assert [item["rank_after"] for item in candidates[:5]] == [1, 2, 3, 4, 5]
    assert all(item["status"] == "outside_top_k" for item in candidates[5:])
    assert all(item["rank_after"] is None for item in candidates[5:])
    assert trace["stages"] == ["threshold"]
    assert trace["skipped"] == []
    assert trace["verdict"] == "ok"
    assert [chunk["chunk_id"] for chunk in chunks] == [item["chunk_id"] for item in candidates[:5]]


async def test_threshold_cuts_below_raw_cosine(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)
    _, base = await _run(kb_id, _cfg())
    cosines = sorted({item["cos"] for item in base["candidates"]})
    threshold = cosines[len(cosines) // 2]
    chunks, trace = await _run(kb_id, _cfg(threshold=threshold, top_k=20))
    for item in trace["candidates"]:
        if item["cos"] < threshold:
            assert item["status"] == "below_threshold"
            assert item["rank_after"] is None
        else:
            assert item["status"] == "in_answer"
    assert chunks
    assert all(chunk["score"] >= threshold for chunk in chunks)


async def test_all_cut_gives_below_threshold_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)
    _, base = await _run(kb_id, _cfg())
    best = max(item["cos"] for item in base["candidates"])
    chunks, trace = await _run(kb_id, _cfg(threshold=best + 0.01))
    assert chunks == []
    assert trace["verdict"] == "below_threshold"
    assert trace["best_cosine"] == best
    assert len(trace["candidates"]) == 20
    assert all(item["status"] == "below_threshold" for item in trace["candidates"])


async def test_zero_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)

    async def empty(*args: object) -> tuple[list[dict[str, Any]], list[float]]:
        return [], vector_for("x")

    monkeypatch.setattr(rag_pipeline, "retrieve_vectors", empty)
    chunks, trace = await _run(kb_id, _cfg())
    assert chunks == []
    assert trace["verdict"] == "ok"
    assert trace["candidates"] == []
    assert trace["best_cosine"] is None


async def test_trace_has_no_chunk_text(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)
    chunks, trace = await _run(kb_id, _cfg())
    assert all("text" not in item for item in trace["candidates"])
    assert chunks and all("text" in chunk for chunk in chunks)
    assert trace["query"] == QUESTION
    assert trace["rewritten"] is None
    assert isinstance(trace["latency_ms"], int) and trace["latency_ms"] >= 0


async def test_candidate_k_above_chunk_count(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)
    kb = await get_kb(kb_id)
    assert kb.chunk_count >= CHUNK_COUNT_MIN
    _, trace = await _run(kb_id, _cfg(candidate_k=kb.chunk_count + 30))
    assert len(trace["candidates"]) == kb.chunk_count


def test_mark_over_budget() -> None:
    trace = {
        "candidates": [
            {"status": "in_answer", "rank_after": rank} for rank in range(1, 6)
        ]
        + [{"status": "outside_top_k", "rank_after": None}]
    }
    mark_over_budget(trace, kept=2)
    assert [item["status"] for item in trace["candidates"]] == [
        "in_answer",
        "in_answer",
        "over_budget",
        "over_budget",
        "over_budget",
        "outside_top_k",
    ]
    assert [item["rank_after"] for item in trace["candidates"][:5]] == [1, 2, 3, 4, 5]
