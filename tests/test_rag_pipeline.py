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
from agent.rag_fts import FtsQueryError
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


# ---- optional stages ----

RARE_QUESTION = "зябликовый"


async def _rare_kb(monkeypatch: pytest.MonkeyPatch) -> tuple[int, list[str]]:
    return await _ready_kb(monkeypatch, {"doc.txt": DISTINCT, "rare.txt": RARE})


async def _rare_row_cos(kb_id: int) -> dict[str, Any]:
    _, trace = await _run(kb_id, _cfg(candidate_k=1000, top_k=1000), RARE_QUESTION)
    return next(item for item in trace["candidates"] if item["file"] == "rare.txt")


async def test_rewrite_merges_both_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, calls = await _ready_kb(monkeypatch)
    big = _cfg(candidate_k=1000, top_k=1000)
    _, original = await _run(kb_id, big)
    original_cos = {item["chunk_id"]: item["cos"] for item in original["candidates"]}
    calls.clear()
    client = FakeClient(_result(REWRITTEN))
    _, trace = await _run(kb_id, _cfg(candidate_k=1000, top_k=1000, rewrite=True), client=client)
    assert calls == [QUESTION, REWRITTEN]
    assert trace["rewritten"] == REWRITTEN
    assert "rewrite" in trace["stages"]
    assert isinstance(trace["rewrite_cosine"], float)
    assert {item["chunk_id"] for item in trace["candidates"]} == set(original_cos)
    assert all(item["found_by"] == "both" for item in trace["candidates"])
    assert all(item["cos"] >= original_cos[item["chunk_id"]] for item in trace["candidates"])


async def test_rewrite_keeps_original_candidates_and_marks_rewritten_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)
    _, original = await _run(kb_id, _cfg(candidate_k=3, top_k=3))
    original_ids = {item["chunk_id"] for item in original["candidates"]}
    client = FakeClient(_result(REWRITTEN))
    _, trace = await _run(kb_id, _cfg(candidate_k=3, top_k=3, rewrite=True), client=client)
    ids = {item["chunk_id"] for item in trace["candidates"]}
    assert original_ids <= ids
    for item in trace["candidates"]:
        if item["chunk_id"] not in original_ids:
            assert item["found_by"] == "rewritten"
        else:
            assert item["found_by"] in {"original", "both"}


async def test_rewrite_bad_output_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, calls = await _ready_kb(monkeypatch)
    _, off = await _run(kb_id, _cfg())
    calls.clear()
    client = FakeClient(_result("Конечно! Вот переписанный запрос: раздел номер 3"))
    chunks, trace = await _run(kb_id, _cfg(rewrite=True), client=client)
    assert {"stage": "rewrite", "reason": "bad_output"} in trace["skipped"]
    assert calls == [QUESTION]
    assert "rewrite" not in trace["stages"]
    assert trace["rewritten"] is None
    assert trace["candidates"] == off["candidates"]


async def test_rewrite_unchanged_is_not_a_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, calls = await _ready_kb(monkeypatch)
    client = FakeClient(_result(QUESTION))
    _, trace = await _run(kb_id, _cfg(rewrite=True), client=client)
    assert trace["skipped"] == []
    assert calls == [QUESTION]
    assert "rewrite" in trace["stages"]
    assert trace["rewritten"] is None


@pytest.mark.parametrize("flag", ["rewrite", "llm_rerank"])
async def test_no_llm_skips_stage(monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)
    _, off = await _run(kb_id, _cfg())
    _, trace = await _run(kb_id, _cfg(**{flag: True}), client=None)
    stage = "rewrite" if flag == "rewrite" else "llm"
    assert {"stage": stage, "reason": "no_llm"} in trace["skipped"]
    assert trace["candidates"] == off["candidates"]


async def test_rewrite_search_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _ready_kb(monkeypatch, fail_queries=(REWRITTEN,))
    _, off = await _run(kb_id, _cfg())
    client = FakeClient(_result(REWRITTEN))
    _, trace = await _run(kb_id, _cfg(rewrite=True), client=client)
    assert {"stage": "rewrite", "reason": "search_failed"} in trace["skipped"]
    assert trace["rewritten"] is None
    assert trace["candidates"] == off["candidates"]


async def test_hybrid_adds_fts_hit_with_cosine(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _rare_kb(monkeypatch)
    _, off = await _run(kb_id, _cfg(candidate_k=3, top_k=3), RARE_QUESTION)
    assert all(item["file"] != "rare.txt" for item in off["candidates"])
    _, trace = await _run(kb_id, _cfg(candidate_k=3, top_k=3, hybrid=True), RARE_QUESTION)
    assert "hybrid" in trace["stages"]
    rare = [item for item in trace["candidates"] if item["file"] == "rare.txt"]
    assert len(rare) == 1
    assert rare[0]["fts_rank"] == 1
    assert isinstance(rare[0]["cos"], float)
    assert [item["rank_before"] for item in trace["candidates"]] == list(
        range(1, len(trace["candidates"]) + 1)
    )
    # RRF puts the keyword hit, found by the keyword list only, below the best vector hits
    # but it must be present among the candidates in rank order.
    assert rare[0]["rank_before"] <= len(trace["candidates"])


async def test_fts_exempt_survives_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _rare_kb(monkeypatch)
    rare_cos = (await _rare_row_cos(kb_id))["cos"]
    _, base = await _run(kb_id, _cfg(), RARE_QUESTION)
    threshold = max(rare_cos, max(item["cos"] for item in base["candidates"])) + 0.01
    chunks, trace = await _run(
        kb_id, _cfg(threshold=threshold, candidate_k=3, hybrid=True), RARE_QUESTION
    )
    assert trace["verdict"] == "ok"
    survivors = [item for item in trace["candidates"] if item["status"] == "in_answer"]
    assert [item["file"] for item in survivors] == ["rare.txt"]
    assert survivors[0]["fts_exempt"] is True
    assert survivors[0]["cos"] < threshold
    assert all(
        item["status"] == "below_threshold" for item in trace["candidates"] if item not in survivors
    )
    assert len(chunks) == 1
    chunks, trace = await _run(kb_id, _cfg(threshold=threshold, candidate_k=3), RARE_QUESTION)
    assert chunks == []
    assert trace["verdict"] == "below_threshold"


async def _cut_everything_threshold(kb_id: int) -> float:
    _, base = await _run(kb_id, _cfg(candidate_k=1000, top_k=1000), RARE_QUESTION)
    return max(item["cos"] for item in base["candidates"]) + 0.01


async def test_fts_exempt_requires_keyword_cuts_weak_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kb_id, _ = await _rare_kb(monkeypatch)
    threshold = await _cut_everything_threshold(kb_id)
    question = "зябликовый ракета галактика квазар туманность"
    chunks, trace = await _run(
        kb_id, _cfg(threshold=threshold, candidate_k=3, hybrid=True), question
    )
    rare = [item for item in trace["candidates"] if item["file"] == "rare.txt"]
    assert rare and rare[0]["fts_rank"] is not None
    assert rare[0]["fts_exempt"] is False
    assert rare[0]["status"] == "below_threshold"
    assert chunks == []
    assert trace["verdict"] == "below_threshold"


async def test_fts_exempt_requires_keyword_passes_on_article_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files: dict[str, str | bytes] = {"doc.txt": DISTINCT, "law.txt": "Статья 7.3 Порядок учёта. " + RARE}
    kb_id, _ = await _ready_kb(monkeypatch, files)
    threshold = await _cut_everything_threshold(kb_id)
    question = "ракета галактика квазар туманность 7.3"
    _, trace = await _run(kb_id, _cfg(threshold=threshold, candidate_k=3, hybrid=True), question)
    exempt = [item for item in trace["candidates"] if item["fts_exempt"]]
    assert [item["file"] for item in exempt] == ["law.txt"]
    assert exempt[0]["status"] == "in_answer"


async def test_hybrid_fts_error_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _rare_kb(monkeypatch)
    _, off = await _run(kb_id, _cfg(), RARE_QUESTION)

    async def boom(*args: object) -> list[int]:
        raise FtsQueryError("bad")

    monkeypatch.setattr(rag_pipeline, "fts_search", boom)
    _, trace = await _run(kb_id, _cfg(hybrid=True), RARE_QUESTION)
    assert {"stage": "hybrid", "reason": "fts_error"} in trace["skipped"]
    assert "hybrid" not in trace["stages"]
    assert trace["candidates"] == off["candidates"]


async def test_lexical_reorders_survivors_only(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)
    _, base = await _run(kb_id, _cfg(top_k=20))
    threshold = sorted(item["cos"] for item in base["candidates"])[5]
    config = dict(threshold=threshold, top_k=20)
    off_chunks, off = await _run(kb_id, _cfg(**config))
    chunks, trace = await _run(kb_id, _cfg(lexical=True, **config))
    assert "lexical" in trace["stages"]
    assert {c["chunk_id"] for c in chunks} == {c["chunk_id"] for c in off_chunks}
    for item in trace["candidates"]:
        if item["status"] == "below_threshold":
            assert item["lex"] is None
        else:
            assert isinstance(item["lex"], float)
    expected_order, _ = rag_pipeline.lexical_rerank(QUESTION, off_chunks)
    assert [c["chunk_id"] for c in chunks] == [off_chunks[i]["chunk_id"] for i in expected_order]


async def test_lexical_order_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)

    def reverse(query: str, chunks: list[dict[str, Any]]) -> tuple[list[int], list[float]]:
        return list(reversed(range(len(chunks)))), [0.5] * len(chunks)

    off_chunks, _ = await _run(kb_id, _cfg(candidate_k=5))
    monkeypatch.setattr(rag_pipeline, "lexical_rerank", reverse)
    chunks, trace = await _run(kb_id, _cfg(candidate_k=5, lexical=True))
    assert [c["chunk_id"] for c in chunks] == [c["chunk_id"] for c in reversed(off_chunks)]
    assert trace["candidates"][0]["lex"] == 0.5


def _scores(count: int) -> str:
    return "\n".join(f"{n}: {n}" for n in range(1, count + 1))


async def test_llm_rerank_single_call(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)
    off_chunks, _ = await _run(kb_id, _cfg(candidate_k=12, top_k=12))
    client = FakeClient(_result(_scores(10)))
    chunks, trace = await _run(
        kb_id, _cfg(candidate_k=12, top_k=12, llm_rerank=True), client=client
    )
    assert len(client.calls) == 1
    assert "llm" in trace["stages"]
    prior = [c["chunk_id"] for c in off_chunks]
    expected = list(reversed(prior[:10])) + prior[10:]
    assert [c["chunk_id"] for c in chunks] == expected
    by_id = {item["chunk_id"]: item for item in trace["candidates"]}
    assert [by_id[cid]["llm"] for cid in prior[:10]] == [float(n) for n in range(1, 11)]
    assert by_id[prior[10]]["llm"] is None and by_id[prior[11]]["llm"] is None


async def test_llm_rerank_ties_keep_prior_order(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)
    off_chunks, _ = await _run(kb_id, _cfg(candidate_k=12, top_k=12))
    client = FakeClient(_result("\n".join(f"{n}: 5" for n in range(1, 11))))
    chunks, _ = await _run(kb_id, _cfg(candidate_k=12, top_k=12, llm_rerank=True), client=client)
    assert [c["chunk_id"] for c in chunks] == [c["chunk_id"] for c in off_chunks]


@pytest.mark.parametrize(
    ("reply", "reason"),
    [(_result("полная ерунда"), "bad_output"), (asyncio.TimeoutError(), "timeout")],
)
async def test_llm_rerank_failure_keeps_order(
    monkeypatch: pytest.MonkeyPatch, reply: Any, reason: str
) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)
    off_chunks, _ = await _run(kb_id, _cfg())
    chunks, trace = await _run(kb_id, _cfg(llm_rerank=True), client=FakeClient(reply))
    assert {"stage": "llm", "reason": reason} in trace["skipped"]
    assert "llm" not in trace["stages"]
    assert [c["chunk_id"] for c in chunks] == [c["chunk_id"] for c in off_chunks]


async def test_lexical_then_llm_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _ready_kb(monkeypatch)

    def reverse(query: str, chunks: list[dict[str, Any]]) -> tuple[list[int], list[float]]:
        return list(reversed(range(len(chunks)))), [0.1] * len(chunks)

    off_chunks, _ = await _run(kb_id, _cfg(candidate_k=6, top_k=6))
    monkeypatch.setattr(rag_pipeline, "lexical_rerank", reverse)
    client = FakeClient(_result("\n".join(f"{n}: 5" for n in range(1, 7))))
    chunks, trace = await _run(
        kb_id, _cfg(candidate_k=6, top_k=6, lexical=True, llm_rerank=True), client=client
    )
    prompt = client.calls[0]["messages"][1]["content"]
    first_fragment = prompt.split("[1]")[1].split("[2]")[0]
    assert off_chunks[-1]["text"][:30] in first_fragment
    assert [c["chunk_id"] for c in chunks] == [c["chunk_id"] for c in reversed(off_chunks)]
    assert trace["stages"] == ["threshold", "lexical", "llm"]


async def test_all_stages_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    kb_id, _ = await _rare_kb(monkeypatch)
    off_chunks, _ = await _run(kb_id, _cfg(), RARE_QUESTION)
    config = _cfg(rewrite=True, llm_rerank=True)
    chunks, trace = await _run(kb_id, config, RARE_QUESTION, client=FailingClient())
    assert {entry["stage"] for entry in trace["skipped"]} == {"rewrite", "llm"}
    assert [c["chunk_id"] for c in chunks] == [c["chunk_id"] for c in off_chunks]
    assert trace["verdict"] == "ok"
