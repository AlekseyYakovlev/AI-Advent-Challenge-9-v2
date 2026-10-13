"""Tests for the background job that adds and replaces files in a ready knowledge base."""

import asyncio
import inspect
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from agent import kb_indexer, kb_search
from agent import state as agent_state
from agent.events import hub
from agent.kb_indexer import (
    MSG_ADD_DIM_MISMATCH,
    MSG_ADD_UNEXPECTED,
    run_add_job,
    spawn_add_job,
)
from agent.kb_limits import PHASE_ADDING
from agent.kb_search import search_kb
from kb_helpers import (
    DIM,
    chunk_rows,
    document_rows,
    get_kb,
    install_fake_embedder,
    ready_kb,
    seed_user,
    set_kb_phase,
    stage_add_items,
    vector_for,
    wait_until,
)
from shared.database import async_session_factory, engine
from shared.kb_storage import index_path, new_staging_dir, read_index_bytes, uploads_dir
from shared.models import KbStatus, KbStrategy, KnowledgeBase


def _body(word: str) -> str:
    """Distinct paragraph text long enough for several chunks."""
    return " ".join(f"{word} описывает раздел номер {i} подробно." for i in range(40))


MD_BODY = "# Заголовок\n\n## Первый раздел\n\n" + _body("Альфа") + "\n\n## Второй раздел\n\n" + _body("Бета")


def _upload_files(user_id: int, kb_id: int) -> set[str]:
    target = uploads_dir(user_id, kb_id)
    return {p.name for p in target.iterdir() if p.is_file()} if target.exists() else set()


async def _snapshot(user_id: int, kb_id: int) -> dict[str, Any]:
    kb = await get_kb(kb_id)
    return {
        "index": index_path(user_id, kb_id).read_bytes(),
        "chunks": {(c.id, c.text) for c in await chunk_rows(kb_id)},
        "chunk_count": kb.chunk_count,
        "file_count": kb.file_count,
        "documents": [(d.id, d.stored_name) for d in await document_rows(kb_id)],
        "files": _upload_files(user_id, kb_id),
    }


async def _setup(
    monkeypatch: pytest.MonkeyPatch, files: dict[str, str | bytes], **kwargs: Any
) -> tuple[int, int]:
    install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    kb_id = await ready_kb(monkeypatch, user_id, files, **kwargs)
    return user_id, kb_id


async def _add(
    user_id: int,
    kb_id: int,
    files: dict[str, str | bytes],
    replaces: dict[str, tuple[int, ...]] | None = None,
    unchanged: int = 0,
) -> Path:
    staging, items = stage_add_items(user_id, kb_id, files, replaces)
    await set_kb_phase(kb_id, PHASE_ADDING)
    await run_add_job(kb_id, user_id, staging, items, unchanged)
    return staging


def _patch_query(monkeypatch: pytest.MonkeyPatch, query_text: str) -> None:
    async def fake_query(model_id: str, query: str, *args: object) -> list[float]:
        return vector_for(query_text)

    monkeypatch.setattr(kb_search, "embed_query", fake_query)


async def _search(kb_id: int, limit: int = 50) -> list[dict[str, Any]]:
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        return await search_kb(session, kb, "q", limit)


def _drain(queue: asyncio.Queue) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    while not queue.empty():
        frames.append(queue.get_nowait())
    return frames


async def test_add_new_file_keeps_old_chunks_and_extends_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, kb_id = await _setup(monkeypatch, {"README.md": _body("Гамма")})
    before = await chunk_rows(kb_id)
    max_old = max(c.id for c in before)

    staging = await _add(user_id, kb_id, {"docs/API_SPEC.md": _body("Дельта")})

    kb = await get_kb(kb_id)
    assert kb.status == KbStatus.READY
    assert kb.phase is None
    assert kb.error is None
    after = await chunk_rows(kb_id)
    assert {(c.id, c.text) for c in before} <= {(c.id, c.text) for c in after}
    new = [c for c in after if c.id > max_old]
    assert new and len(new) + len(before) == len(after)
    assert all(c.source == "docs/API_SPEC.md" and c.title == "API_SPEC" for c in new)
    assert kb.file_count == 2
    assert kb.chunk_count == len(after)
    assert read_index_bytes(index_path(user_id, kb_id)).ntotal == kb.chunk_count
    assert agent_state.kb_index_cache[kb_id].ntotal == kb.chunk_count
    assert not staging.exists()
    for doc in await document_rows(kb_id):
        assert (uploads_dir(user_id, kb_id) / doc.stored_name).exists()


async def test_add_uses_base_strategy_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _setup(
        monkeypatch, {"README.md": MD_BODY}, strategy=KbStrategy.STRUCTURAL
    )
    fake = install_fake_embedder(monkeypatch)
    before_ids = {c.id for c in await chunk_rows(kb_id)}

    await _add(user_id, kb_id, {"notes.md": MD_BODY})

    new = [c for c in await chunk_rows(kb_id) if c.id not in before_ids]
    assert new
    assert all(c.section is not None for c in new)
    assert sum(fake.batch_sizes) == len(new)


async def test_replace_removes_old_fragments(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _setup(monkeypatch, {"docs/a.md": _body("Старый")})
    old_doc = (await document_rows(kb_id))[0]
    old_file = uploads_dir(user_id, kb_id) / old_doc.stored_name
    assert old_file.exists()

    await _add(user_id, kb_id, {"docs/a.md": _body("Новый")}, {"docs/a.md": (old_doc.id,)})

    docs = await document_rows(kb_id)
    assert [d.filename for d in docs] == ["docs/a.md"]
    assert docs[0].id != old_doc.id
    chunks = await chunk_rows(kb_id)
    assert chunks and all(c.document_id == docs[0].id for c in chunks)
    _patch_query(monkeypatch, chunks[0].text)
    results = await _search(kb_id)
    assert results[0]["chunk_id"] == chunks[0].chunk_id
    assert not any(r["chunk_id"].startswith(f"{old_doc.id}-") for r in results)
    assert not old_file.exists()
    kb = await get_kb(kb_id)
    assert kb.file_count == 1
    assert kb.chunk_count == len(chunks)
    assert read_index_bytes(index_path(user_id, kb_id)).ntotal == len(chunks)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT count(*) FROM kb_chunk_fts WHERE kb_id = :kb"), {"kb": kb_id}
        )
        assert rows.scalar_one() == len(chunks)


async def test_untouched_documents_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _setup(
        monkeypatch, {"a.md": _body("Первый"), "b.md": _body("Второй")}
    )
    docs = {d.filename: d for d in await document_rows(kb_id)}
    b_before = [(c.id, c.text) for c in await chunk_rows(kb_id) if c.source == "b.md"]
    probe = b_before[0][0]
    vector_before = read_index_bytes(index_path(user_id, kb_id)).reconstruct(probe)

    await _add(user_id, kb_id, {"a.md": _body("Третий")}, {"a.md": (docs["a.md"].id,)})

    b_after = [(c.id, c.text) for c in await chunk_rows(kb_id) if c.source == "b.md"]
    assert b_after == b_before
    vector_after = read_index_bytes(index_path(user_id, kb_id)).reconstruct(probe)
    assert list(vector_after) == list(vector_before)


async def test_summary_in_final_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _setup(
        monkeypatch, {"a.md": _body("Один"), "b.md": _body("Два")}
    )
    docs = {d.filename: d for d in await document_rows(kb_id)}
    queue = hub.subscribe(user_id)
    _drain(queue)

    await _add(
        user_id,
        kb_id,
        {"c.md": _body("Три"), "a.md": _body("Четыре")},
        {"a.md": (docs["a.md"].id,)},
        unchanged=3,
    )

    frames = _drain(queue)
    last = frames[-1]
    assert last["add_summary"] == {"added": 1, "replaced": 1, "unchanged": 3}
    assert last["kb"]["status"] == "ready"
    assert last["kb"]["phase"] is None
    assert last["kb"]["error"] is None
    assert any(
        f["kb"]["status"] == "ready" and f["kb"]["phase"] == "adding" for f in frames[:-1]
    )


async def test_only_unchanged_finishes_without_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _setup(monkeypatch, {"a.md": _body("Один")})
    fake = install_fake_embedder(monkeypatch)
    index_before = index_path(user_id, kb_id).read_bytes()
    queue = hub.subscribe(user_id)
    _drain(queue)
    staging = new_staging_dir(user_id, kb_id)
    await set_kb_phase(kb_id, PHASE_ADDING)

    await run_add_job(kb_id, user_id, staging, [], 2)

    assert fake.batch_sizes == []
    assert (await get_kb(kb_id)).phase is None
    assert _drain(queue)[-1]["add_summary"] == {"added": 0, "replaced": 0, "unchanged": 2}
    assert index_path(user_id, kb_id).read_bytes() == index_before
    assert not staging.exists()


async def test_search_works_while_adding(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _setup(monkeypatch, {"old.md": _body("Старый")})
    old_chunk = (await chunk_rows(kb_id))[0]
    gate = asyncio.Event()
    fake = install_fake_embedder(monkeypatch, block=gate)
    staging, items = stage_add_items(user_id, kb_id, {"new.md": _body("Новый")})
    await set_kb_phase(kb_id, PHASE_ADDING)

    spawn_add_job(kb_id, user_id, staging, items, 0)
    task = agent_state.kb_jobs[kb_id]
    await wait_until(lambda: len(fake.batch_sizes) >= 1)

    kb = await get_kb(kb_id)
    assert kb.status == KbStatus.READY
    assert kb.phase == PHASE_ADDING
    _patch_query(monkeypatch, old_chunk.text)
    assert (await _search(kb_id))[0]["chunk_id"] == old_chunk.chunk_id

    gate.set()
    await task

    assert (await get_kb(kb_id)).phase is None
    new_chunk = next(c for c in await chunk_rows(kb_id) if c.source == "new.md")
    _patch_query(monkeypatch, new_chunk.text)
    assert (await _search(kb_id))[0]["chunk_id"] == new_chunk.chunk_id


async def test_embedding_failure_leaves_base_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _setup(monkeypatch, {"a.md": _body("Один")})
    _patch_query(monkeypatch, "x")
    await _search(kb_id)
    cached = agent_state.kb_index_cache[kb_id].ntotal
    before = await _snapshot(user_id, kb_id)
    install_fake_embedder(monkeypatch, fail_on_batch=1)
    queue = hub.subscribe(user_id)
    _drain(queue)

    staging = await _add(user_id, kb_id, {"b.md": _body("Два")})

    assert await _snapshot(user_id, kb_id) == before
    kb = await get_kb(kb_id)
    assert kb.status == KbStatus.READY
    assert kb.phase is None
    assert kb.error == "Сбой эмбеддингов в тесте."
    assert not staging.exists()
    assert agent_state.kb_index_cache[kb_id].ntotal == cached
    last = _drain(queue)[-1]
    assert "add_summary" not in last
    assert last["kb"]["status"] == "ready"
    assert last["kb"]["error"] == "Сбой эмбеддингов в тесте."


async def test_unreadable_file_leaves_base_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _setup(monkeypatch, {"a.md": _body("Один")})
    before = await _snapshot(user_id, kb_id)

    staging = await _add(user_id, kb_id, {"broken.pdf": b"this is not a pdf"})

    assert await _snapshot(user_id, kb_id) == before
    kb = await get_kb(kb_id)
    assert kb.status == KbStatus.READY
    assert kb.phase is None
    assert isinstance(kb.error, str) and kb.error
    assert not staging.exists()


async def test_dimension_mismatch_fails_add(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _setup(monkeypatch, {"a.md": _body("Один")})
    before = await _snapshot(user_id, kb_id)

    async def wrong_dim(model_id: str, texts: list[str], *args: Any) -> list[list[float]]:
        return [[0.5] * (DIM + 1) for _ in texts]

    monkeypatch.setattr(kb_indexer, "embed_passages", wrong_dim)

    await _add(user_id, kb_id, {"b.md": _body("Два")})

    assert (await get_kb(kb_id)).error == MSG_ADD_DIM_MISMATCH
    assert await _snapshot(user_id, kb_id) == before


def test_commit_section_holds_no_slow_calls() -> None:
    source = inspect.getsource(kb_indexer._commit_addition)
    commit = source.index("session.commit()")
    assert source.index("patch_index") < commit
    assert source.index("write_index_pending") < commit
    assert source.index("promote_index") > commit
    for forbidden in ("embed_passages", "_embed_all", "ensure_embedding_model", "hub.publish"):
        assert forbidden not in source


async def test_slow_commit_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _setup(monkeypatch, {"a.md": _body("Один")})
    events: list[tuple[str, dict[str, Any]]] = []

    class Recorder:
        def __getattr__(self, name: str) -> Any:
            def record(event: str, **kwargs: Any) -> None:
                events.append((event, kwargs))

            return record

    monkeypatch.setattr(kb_indexer, "ADD_COMMIT_WARN_SECONDS", -1.0)
    monkeypatch.setattr(kb_indexer, "logger", Recorder())

    await _add(user_id, kb_id, {"b.md": _body("Два")})

    slow = [kwargs for event, kwargs in events if event == "kb_add_commit_slow"]
    assert len(slow) == 1
    assert isinstance(slow[0]["commit_seconds"], float)


async def test_failure_during_commit_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _setup(monkeypatch, {"a.md": _body("Один")})
    before = await _snapshot(user_id, kb_id)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("patch failed")

    monkeypatch.setattr(kb_indexer, "patch_index", boom)

    await _add(user_id, kb_id, {"b.md": _body("Два")})

    kb = await get_kb(kb_id)
    assert kb.error == MSG_ADD_UNEXPECTED
    assert kb.status == KbStatus.READY
    assert kb.phase is None
    assert await _snapshot(user_id, kb_id) == before
    assert not index_path(user_id, kb_id).with_suffix(".pending").exists()


async def test_replacement_failure_keeps_old_fragments(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _setup(monkeypatch, {"docs/a.md": _body("Старый")})
    old_doc = (await document_rows(kb_id))[0]
    before = await _snapshot(user_id, kb_id)
    install_fake_embedder(monkeypatch, fail_on_batch=1)

    await _add(user_id, kb_id, {"docs/a.md": _body("Новый")}, {"docs/a.md": (old_doc.id,)})

    assert await _snapshot(user_id, kb_id) == before
    assert (uploads_dir(user_id, kb_id) / old_doc.stored_name).exists()
    assert [d.id for d in await document_rows(kb_id)] == [old_doc.id]


async def test_missing_kb_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    staging = new_staging_dir(user_id, 9999)
    (staging / "x.md").write_text("x", encoding="utf-8")

    await run_add_job(9999, user_id, staging, [], 0)

    assert not staging.exists()

