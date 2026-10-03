"""Tests for KB delete-with-cancel, orphan recovery, shutdown and lifespan wiring."""

import asyncio

import pytest
from sqlmodel import select
from starlette.testclient import TestClient

from agent import state as agent_state
from agent.events import hub
from agent.kb_indexer import (
    MSG_INTERRUPTED_RESTART,
    delete_kb,
    recover_orphaned_kb_jobs,
    run_index_job,
    shutdown_kb_jobs,
    spawn_index_job,
)
from agent.main import app
from kb_helpers import (
    chunk_rows,
    get_kb,
    install_fake_embedder,
    seed_kb,
    seed_user,
    wait_until,
)
from shared.database import async_session_factory, engine
from shared.kb_storage import index_path, kb_dir
from shared.models import KbChunk, KbDocument, KbStatus, KnowledgeBase


async def _delete(kb_id: int) -> None:
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        await delete_kb(session, kb)


async def _count(model: type, kb_id_field: str, kb_id: int) -> int:
    async with async_session_factory() as session:
        rows = (
            await session.exec(select(model).where(getattr(model, kb_id_field) == kb_id))
        ).all()
    return len(rows)


async def test_delete_ready_kb_removes_everything_and_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    kb_id = await seed_kb(user_id)
    await run_index_job(kb_id, user_id)
    agent_state.kb_index_cache[kb_id] = object()
    queue = hub.subscribe(user_id)

    await _delete(kb_id)

    assert await get_kb(kb_id) is None
    assert await _count(KbDocument, "kb_id", kb_id) == 0
    assert await _count(KbChunk, "kb_id", kb_id) == 0
    assert not kb_dir(user_id, kb_id).exists()
    assert kb_id not in agent_state.kb_index_cache
    frames = []
    while not queue.empty():
        frames.append(queue.get_nowait())
    assert frames[-1] == {"type": "kb_deleted", "kb_id": kb_id}


async def test_delete_during_embedding_cancels_job(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install_fake_embedder(monkeypatch, block=asyncio.Event())
    user_id = await seed_user()
    kb_id = await seed_kb(user_id)
    spawn_index_job(kb_id, user_id)
    await wait_until(lambda: len(fake.batch_sizes) >= 1)
    task = agent_state.kb_jobs[kb_id]

    await _delete(kb_id)

    assert task.done()
    assert kb_id not in agent_state.kb_jobs
    assert await get_kb(kb_id) is None
    assert await chunk_rows(kb_id) == []
    assert not kb_dir(user_id, kb_id).exists()


@pytest.mark.parametrize("status", [KbStatus.QUEUED, KbStatus.FAILED])
async def test_delete_queued_and_failed_kb(status: KbStatus) -> None:
    user_id = await seed_user()
    kb_id = await seed_kb(user_id, status=status)

    await _delete(kb_id)

    assert await get_kb(kb_id) is None
    assert not kb_dir(user_id, kb_id).exists()


async def _seed_chunks(kb_id: int, count: int) -> None:
    async with async_session_factory() as session:
        doc_id = (
            await session.exec(select(KbDocument).where(KbDocument.kb_id == kb_id))
        ).first().id
        for i in range(count):
            session.add(
                KbChunk(
                    kb_id=kb_id,
                    document_id=doc_id,
                    chunk_index=i,
                    chunk_id=f"{doc_id}-{i}",
                    text="t",
                    source="doc.txt",
                    title="doc",
                    char_start=0,
                    char_end=1,
                )
            )
        await session.commit()


async def test_recover_orphaned_kb_jobs() -> None:
    user_id = await seed_user()
    queued = await seed_kb(user_id, status=KbStatus.QUEUED)
    indexing = await seed_kb(user_id, status=KbStatus.INDEXING)
    ready = await seed_kb(user_id, status=KbStatus.READY)
    failed = await seed_kb(user_id, status=KbStatus.FAILED)
    await _seed_chunks(indexing, 3)
    index_path(user_id, indexing).write_bytes(b"partial")
    index_path(user_id, ready).write_bytes(b"keep")

    assert await recover_orphaned_kb_jobs() == 2

    for kb_id in (queued, indexing):
        kb = await get_kb(kb_id)
        assert kb.status == KbStatus.FAILED
        assert kb.error == MSG_INTERRUPTED_RESTART
        assert kb.phase is None
    assert await chunk_rows(indexing) == []
    assert not index_path(user_id, indexing).exists()
    assert (await get_kb(ready)).status == KbStatus.READY
    assert index_path(user_id, ready).exists()
    assert (await get_kb(failed)).error is None


async def test_shutdown_cancels_and_clears_running_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install_fake_embedder(monkeypatch, block=asyncio.Event())
    user_id = await seed_user()
    kb_id = await seed_kb(user_id)
    spawn_index_job(kb_id, user_id)
    await wait_until(lambda: len(fake.batch_sizes) >= 1)
    task = agent_state.kb_jobs[kb_id]

    await shutdown_kb_jobs()

    assert task.done()
    assert agent_state.kb_jobs == {}
    assert (await get_kb(kb_id)).status == KbStatus.FAILED


def test_lifespan_recovers_orphaned_kb() -> None:
    async def _seed() -> int:
        user_id = await seed_user()
        kb_id = await seed_kb(user_id, status=KbStatus.INDEXING)
        await engine.dispose()
        return kb_id

    kb_id = asyncio.run(_seed())

    with TestClient(app) as client:
        kb = client.portal.call(get_kb, kb_id)
        assert kb.status == KbStatus.FAILED
        assert kb.error == MSG_INTERRUPTED_RESTART
