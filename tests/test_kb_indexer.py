"""Tests for the background knowledge-base indexing job."""

from pathlib import Path

import faiss
import pymupdf
import pytest

from agent import kb_indexer
from agent.kb_indexer import MSG_UNEXPECTED, run_index_job
from agent.kb_limits import EMBED_BATCH_SIZE
from agent.kb_loaders import MSG_SCAN
from kb_helpers import (
    DIM,
    NOMIC,
    RU_TEXT,
    chunk_rows,
    get_kb,
    install_fake_embedder,
    seed_kb,
    seed_user,
)
from shared.kb_storage import index_path, read_index_bytes
from shared.models import KbStatus, KbStrategy


def _scan_pdf_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    doc.save(str(path))
    doc.close()
    return path.read_bytes()


async def test_happy_path_ready_with_consistent_index(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    kb_id = await seed_kb(user_id, {"doc.txt": RU_TEXT}, chunk_size=100, chunk_overlap=10)

    await run_index_job(kb_id, user_id)

    kb = await get_kb(kb_id)
    chunks = await chunk_rows(kb_id)
    assert kb.status == KbStatus.READY
    assert kb.phase is None and kb.error is None
    assert kb.dim == DIM
    assert kb.chunk_count == len(chunks) > EMBED_BATCH_SIZE
    assert kb.done_chunks == kb.total_chunks == kb.chunk_count
    assert kb.doc_prefix == "search_document: " and kb.query_prefix == "search_query: "
    assert kb.embedding_model == NOMIC
    assert all(size <= EMBED_BATCH_SIZE for size in fake.batch_sizes)
    assert len(fake.batch_sizes) > 1
    assert fake.max_in_flight == 1

    index = read_index_bytes(index_path(user_id, kb_id))
    assert index.ntotal == kb.chunk_count and index.d == DIM
    index_ids = set(faiss.vector_to_array(index.id_map).tolist())
    assert index_ids == {chunk.id for chunk in chunks}

    first = chunks[0]
    assert first.chunk_id == f"{first.document_id}-{first.chunk_index}"
    assert first.source == "doc.txt" and first.title == "doc"
    assert first.char_end > first.char_start >= 0


async def test_txt_page_count_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    kb_id = await seed_kb(user_id)
    await run_index_job(kb_id, user_id)

    from sqlmodel import select

    from shared.database import async_session_factory
    from shared.models import KbDocument

    async with async_session_factory() as session:
        docs = (await session.exec(select(KbDocument).where(KbDocument.kb_id == kb_id))).all()
    assert [doc.page_count for doc in docs] == [None]


async def test_structural_strategy_breadcrumbs(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    legal = "Глава 1. Общие положения\n\nСтатья 1. Предмет\n\nТекст первой статьи.\n\nСтатья 2. Цель\n\nТекст второй."
    kb_id = await seed_kb(user_id, {"law.txt": legal}, strategy=KbStrategy.STRUCTURAL)

    await run_index_job(kb_id, user_id)

    kb = await get_kb(kb_id)
    chunks = await chunk_rows(kb_id)
    assert kb.status == KbStatus.READY
    assert chunks[0].text.startswith("law > Глава 1 > Статья 1.")
    assert chunks[0].section == "Глава 1 > Статья 1"


async def test_failed_file_is_all_or_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    kb_id = await seed_kb(user_id, {"ok.txt": RU_TEXT, "scan.pdf": _scan_pdf_bytes(tmp_path)})

    await run_index_job(kb_id, user_id)

    kb = await get_kb(kb_id)
    assert kb.status == KbStatus.FAILED
    assert kb.error == MSG_SCAN.format(name="scan.pdf")
    assert await chunk_rows(kb_id) == []
    assert not index_path(user_id, kb_id).exists()


async def test_ensure_model_error_fails_with_its_message(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_embedder(monkeypatch, ensure_error="Модель X не найдена.")
    user_id = await seed_user()
    kb_id = await seed_kb(user_id)

    await run_index_job(kb_id, user_id)

    kb = await get_kb(kb_id)
    assert kb.status == KbStatus.FAILED and kb.error == "Модель X не найдена."
    assert await chunk_rows(kb_id) == []


async def test_embedding_failure_on_second_batch_leaves_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_embedder(monkeypatch, fail_on_batch=2)
    user_id = await seed_user()
    kb_id = await seed_kb(user_id, {"doc.txt": RU_TEXT}, chunk_size=100, chunk_overlap=10)

    await run_index_job(kb_id, user_id)

    kb = await get_kb(kb_id)
    assert kb.status == KbStatus.FAILED and kb.error == "Сбой эмбеддингов в тесте."
    assert await chunk_rows(kb_id) == []
    assert not index_path(user_id, kb_id).exists()
    assert kb.done_chunks == 0 and kb.phase is None


async def test_unexpected_error_uses_generic_message(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_embedder(monkeypatch)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(kb_indexer, "build_index", boom)
    user_id = await seed_user()
    kb_id = await seed_kb(user_id)

    await run_index_job(kb_id, user_id)

    kb = await get_kb(kb_id)
    assert kb.status == KbStatus.FAILED and kb.error == MSG_UNEXPECTED
    assert await chunk_rows(kb_id) == []


async def test_missing_kb_is_a_no_op() -> None:
    await run_index_job(999, 1)
