"""Tests for KB progress events and event-loop responsiveness while indexing."""

import asyncio
import time
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from agent import kb_indexer
from agent import state as agent_state
from agent.events import hub
from agent.kb_indexer import run_index_job, spawn_index_job
from agent.kb_loaders import load_document
from agent.kb_schemas import KbOut
from agent.main import app
from kb_helpers import RU_TEXT, get_kb, install_fake_embedder, seed_kb, seed_user, wait_until
from shared.models import KbStatus


def _drain(queue: asyncio.Queue[dict[str, Any]]) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    while not queue.empty():
        frames.append(queue.get_nowait())
    return frames


async def test_frames_cover_every_phase_and_end_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    kb_id = await seed_kb(
        user_id, {"doc.txt": RU_TEXT * 3}, chunk_size=100, chunk_overlap=10
    )
    queue = hub.subscribe(user_id)

    await run_index_job(kb_id, user_id)

    frames = _drain(queue)
    assert frames and all(frame["type"] == "kb_progress" for frame in frames)
    assert all(set(frame["kb"]) == set(KbOut.model_fields) for frame in frames)
    kbs = [frame["kb"] for frame in frames]
    assert kbs[0]["status"] == "indexing" and kbs[0]["phase"] == "loading_model"
    phases = [kb["phase"] for kb in kbs]
    assert "parsing" in phases and "embedding" in phases
    last = kbs[-1]
    chunk_count = (await get_kb(kb_id)).chunk_count
    assert last["status"] == "ready" and last["phase"] is None
    assert last["done"] == last["total"] == chunk_count


async def test_embedding_frames_are_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install_fake_embedder(monkeypatch, sleep=0.05)
    user_id = await seed_user()
    kb_id = await seed_kb(
        user_id, {"doc.txt": RU_TEXT * 12}, chunk_size=100, chunk_overlap=10
    )
    queue = hub.subscribe(user_id)

    await run_index_job(kb_id, user_id)

    batches = len(fake.batch_sizes)
    assert batches >= 15
    embedding = [f["kb"] for f in _drain(queue) if f["kb"]["phase"] == "embedding"]
    assert len(embedding) <= batches * 0.05 / 0.5 + 3
    assert embedding[-1]["done"] == embedding[-1]["total"]


async def test_failed_job_publishes_failed_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_embedder(monkeypatch, ensure_error="Модель X не найдена.")
    user_id = await seed_user()
    kb_id = await seed_kb(user_id)
    queue = hub.subscribe(user_id)

    await run_index_job(kb_id, user_id)

    last = _drain(queue)[-1]["kb"]
    assert last["status"] == "failed" and last["error"] == "Модель X не найдена."


async def test_frames_reach_only_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_embedder(monkeypatch)
    owner = await seed_user("owner")
    other = await seed_user("other")
    kb_id = await seed_kb(owner)
    other_queue = hub.subscribe(other)
    owner_queue = hub.subscribe(owner)

    await run_index_job(kb_id, owner)

    assert _drain(other_queue) == []
    assert _drain(owner_queue)


async def test_health_stays_responsive_while_indexing(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_embedder(monkeypatch, sleep=0.2)

    def slow_load(path: Any, filename: str) -> Any:
        time.sleep(1.0)
        return load_document(path, filename)

    monkeypatch.setattr(kb_indexer, "load_document", slow_load)
    user_id = await seed_user()
    kb_id = await seed_kb(
        user_id, {"doc.txt": RU_TEXT * 12}, chunk_size=100, chunk_overlap=10
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        spawn_index_job(kb_id, user_id)
        durations: list[float] = []
        for _ in range(5):
            started = time.monotonic()
            response = await client.get("/health")
            durations.append(time.monotonic() - started)
            assert response.status_code == 200
            await asyncio.sleep(0.1)
        assert (await get_kb(kb_id)).status in (KbStatus.INDEXING, KbStatus.QUEUED)
        await wait_until(lambda: kb_id not in agent_state.kb_jobs, timeout=30)

    assert max(durations) < 0.5
    assert (await get_kb(kb_id)).status == KbStatus.READY
