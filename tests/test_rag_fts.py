"""Tests for FTS5 keyword retrieval over knowledge-base chunks."""

import pytest

from agent import rag_fts
from agent.kb_indexer import run_index_job
from agent.rag_fts import FtsQueryError, fts_search, load_chunks_by_ids
from kb_helpers import chunk_rows, install_fake_embedder, seed_kb, seed_user
from shared.database import async_session_factory

FILES = {
    "law.txt": "Статья 12.9 устанавливает штраф за превышение скорости.",
}


async def _kb(monkeypatch: pytest.MonkeyPatch, files: dict[str, str] = FILES) -> tuple[int, int]:
    install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    kb_id = await seed_kb(user_id, files, chunk_size=400, chunk_overlap=10)
    await run_index_job(kb_id, user_id)
    return user_id, kb_id


async def test_fts_finds_article_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    _, kb_id = await _kb(monkeypatch)
    chunks = await chunk_rows(kb_id)
    async with async_session_factory() as session:
        ids = await fts_search(session, kb_id, "штраф по ст. 12.9", 10)
    assert chunks[0].id in ids


async def test_fts_limit_and_no_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    text = " ".join(f"Штраф пункт {i} превышение скорости." for i in range(60))
    _, kb_id = await _kb(monkeypatch, {"a.txt": text})
    async with async_session_factory() as session:
        ids = await fts_search(session, kb_id, "штраф скорости", 2)
    assert len(ids) <= 2
    assert len(ids) == len(set(ids))


async def test_fts_scoped_to_kb_other_kb_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    kb_a = await seed_kb(user_id, FILES, chunk_size=400, chunk_overlap=10)
    kb_b = await seed_kb(user_id, FILES, chunk_size=400, chunk_overlap=10)
    await run_index_job(kb_a, user_id)
    await run_index_job(kb_b, user_id)
    b_ids = {c.id for c in await chunk_rows(kb_b)}
    async with async_session_factory() as session:
        ids = await fts_search(session, kb_a, "штраф скорости", 10)
        loaded = await load_chunks_by_ids(session, kb_a, list(b_ids))
    assert ids
    assert not set(ids) & b_ids
    assert loaded == {}


async def test_fts_stopwords_only_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _, kb_id = await _kb(monkeypatch)
    monkeypatch.setattr(rag_fts, "build_fts_query", lambda question: None)
    async with async_session_factory() as session:
        assert await fts_search(session, kb_id, "и в на", 10) == []


async def test_fts_invalid_match_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _, kb_id = await _kb(monkeypatch)
    monkeypatch.setattr(rag_fts, "build_fts_query", lambda question: '"unbalanced')
    async with async_session_factory() as session:
        with pytest.raises(FtsQueryError):
            await fts_search(session, kb_id, "штраф", 10)


async def test_load_chunks_by_ids_shape_and_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _, kb_id = await _kb(monkeypatch)
    chunk = (await chunk_rows(kb_id))[0]
    async with async_session_factory() as session:
        loaded = await load_chunks_by_ids(session, kb_id, [chunk.id, 10_000_000])
        empty = await load_chunks_by_ids(session, kb_id, [])
    assert set(loaded) == {chunk.id}
    assert set(loaded[chunk.id]) == {
        "row_id", "chunk_id", "source", "title", "section", "page", "text"
    }
    assert loaded[chunk.id]["row_id"] == chunk.id
    assert empty == {}
