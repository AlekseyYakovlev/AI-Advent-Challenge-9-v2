"""Tests for knowledge-base semantic search."""

import pytest

from agent import kb_search
from agent.kb_indexer import run_index_job
from agent.kb_search import (
    EmbeddingDimMismatchError,
    KbIndexCorruptError,
    KbNotReadyError,
    cosine_for_ids,
    search_kb,
    search_kb_vectors,
)
from agent.state import cleanup_kb_caches, kb_index_cache
from kb_helpers import (
    DIM,
    NOMIC,
    RU_TEXT,
    chunk_rows,
    get_kb,
    install_fake_embedder,
    seed_kb,
    seed_user,
    vector_for,
)
from shared.database import async_session_factory
from shared.kb_storage import index_path, write_index_bytes
from shared.models import KnowledgeBase

import faiss
import numpy as np

DISTINCT = {"doc.txt": " ".join(f"Раздел номер {i} описывает тему {i * 7919}." for i in range(80))}


async def _ready_kb(monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    kb_id = await seed_kb(user_id, DISTINCT, chunk_size=120, chunk_overlap=10)
    await run_index_job(kb_id, user_id)
    assert (await get_kb(kb_id)).status.value == "ready"
    return user_id, kb_id


def _patch_query(monkeypatch: pytest.MonkeyPatch, text: str, seen: list[str]) -> None:
    async def fake_query(model_id: str, query: str, *args: object) -> list[float]:
        seen.append(model_id)
        return vector_for(text)

    monkeypatch.setattr(kb_search, "embed_query", fake_query)


async def test_search_returns_known_chunk_first(monkeypatch: pytest.MonkeyPatch) -> None:
    _, kb_id = await _ready_kb(monkeypatch)
    chunks = await chunk_rows(kb_id)
    target = chunks[3]
    seen: list[str] = []
    _patch_query(monkeypatch, target.text, seen)
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        results = await search_kb(session, kb, "q", 5)
    assert seen == [NOMIC]
    assert len(results) == min(5, len(chunks))
    assert results[0]["chunk_id"] == target.chunk_id
    assert results[0]["rank"] == 1
    assert results[0]["score"] == pytest.approx(1.0, abs=1e-3)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)
    for key in ("chunk_id", "source", "title", "section", "page", "text"):
        assert key in results[0]


async def test_not_ready_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await seed_user()
    kb_id = await seed_kb(user_id)
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        with pytest.raises(KbNotReadyError):
            await search_kb(session, kb, "q", 5)


async def test_index_loaded_once_and_cache_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    _, kb_id = await _ready_kb(monkeypatch)
    calls: list[object] = []
    original = kb_search.read_index_bytes

    def spy(path: object) -> object:
        calls.append(path)
        return original(path)

    monkeypatch.setattr(kb_search, "read_index_bytes", spy)
    _patch_query(monkeypatch, "x", [])
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        await search_kb(session, kb, "q", 3)
        await search_kb(session, kb, "q", 3)
    assert len(calls) == 1
    assert kb_id in kb_index_cache
    cleanup_kb_caches(kb_id)
    assert kb_id not in kb_index_cache


async def test_top_k_larger_than_ntotal_filters_padding(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await seed_user()
    install_fake_embedder(monkeypatch)
    kb_id = await seed_kb(user_id, {"doc.txt": RU_TEXT[:150]}, chunk_size=300, chunk_overlap=10)
    await run_index_job(kb_id, user_id)
    kb = await get_kb(kb_id)
    _patch_query(monkeypatch, "x", [])
    async with async_session_factory() as session:
        results = await search_kb(session, kb, "q", 20)
    assert len(results) == kb.chunk_count


async def test_count_mismatch_is_corrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _ready_kb(monkeypatch)
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(8))
    index.add_with_ids(np.ones((1, 8), dtype="float32"), np.asarray([1], dtype="int64"))
    write_index_bytes(index_path(user_id, kb_id), index)
    _patch_query(monkeypatch, "x", [])
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        with pytest.raises(KbIndexCorruptError):
            await search_kb(session, kb, "q", 3)


async def test_missing_index_file_is_corrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _ready_kb(monkeypatch)
    index_path(user_id, kb_id).unlink()
    _patch_query(monkeypatch, "x", [])
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        with pytest.raises(KbIndexCorruptError):
            await search_kb(session, kb, "q", 3)


async def test_foreign_chunk_ids_are_not_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await seed_user()
    install_fake_embedder(monkeypatch)
    kb_a = await seed_kb(user_id, DISTINCT, chunk_size=120, chunk_overlap=10)
    kb_b = await seed_kb(user_id, DISTINCT, chunk_size=120, chunk_overlap=10)
    await run_index_job(kb_a, user_id)
    await run_index_job(kb_b, user_id)
    b_ids = {c.id for c in await chunk_rows(kb_b)}
    _patch_query(monkeypatch, "x", [])
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_a)
        results = await search_kb(session, kb, "q", 20)
    a_chunk_ids = {c.chunk_id for c in await chunk_rows(kb_a)}
    assert results
    assert {r["chunk_id"] for r in results} <= a_chunk_ids
    assert b_ids


async def test_wrong_query_dimension_raises_dim_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _, kb_id = await _ready_kb(monkeypatch)

    async def wrong_dim(model_id: str, query: str, *args: object) -> list[float]:
        return [0.1] * (DIM + 1)

    monkeypatch.setattr(kb_search, "embed_query", wrong_dim)
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        with pytest.raises(EmbeddingDimMismatchError) as info:
            await search_kb(session, kb, "q", 3)
    assert isinstance(info.value, KbIndexCorruptError)
    assert info.value.message == kb_search.MSG_DIM_MISMATCH


async def test_search_kb_vectors_returns_row_ids_and_unit_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, kb_id = await _ready_kb(monkeypatch)
    chunks = await chunk_rows(kb_id)
    target = chunks[3]
    _patch_query(monkeypatch, target.text, [])
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        results, vector = await search_kb_vectors(session, kb, "q", 5)
        plain = await search_kb(session, kb, "q", 5)
    assert results[0]["row_id"] == target.id
    assert all("row_id" not in item for item in plain)
    assert [{k: v for k, v in r.items() if k != "row_id"} for r in results] == plain
    assert vector.shape == (kb.dim,)
    assert vector.dtype == np.float32
    assert float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=1e-5)


async def test_search_kb_vectors_not_ready_raises() -> None:
    user_id = await seed_user()
    kb_id = await seed_kb(user_id)
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        with pytest.raises(KbNotReadyError):
            await search_kb_vectors(session, kb, "q", 5)


async def test_cosine_for_ids_matches_scores_and_covers_outside_top_k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, kb_id = await _ready_kb(monkeypatch)
    chunks = await chunk_rows(kb_id)
    _patch_query(monkeypatch, chunks[3].text, [])
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        results, vector = await search_kb_vectors(session, kb, "q", 3)
    cosines = await cosine_for_ids(kb, vector, [r["row_id"] for r in results])
    for item in results:
        assert cosines[item["row_id"]] == pytest.approx(item["score"], abs=1e-3)
    top_ids = {r["row_id"] for r in results}
    outside = [c.id for c in chunks if c.id not in top_ids]
    assert outside
    assert set(await cosine_for_ids(kb, vector, outside)) == set(outside)


async def test_cosine_for_ids_skips_unknown_and_handles_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, kb_id = await _ready_kb(monkeypatch)
    chunks = await chunk_rows(kb_id)
    _patch_query(monkeypatch, "x", [])
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        _, vector = await search_kb_vectors(session, kb, "q", 1)
    assert await cosine_for_ids(kb, vector, []) == {}
    got = await cosine_for_ids(kb, vector, [chunks[0].id, 10_000_000])
    assert set(got) == {chunks[0].id}
