"""Knowledge-base storage helper and in-memory state tests."""

import asyncio
from pathlib import Path

import faiss
import numpy as np
import pytest

from agent.state import cleanup_kb_caches, kb_index_cache, kb_jobs
from shared import kb_storage
from shared.config import settings


def test_kb_root_default_derives_from_db_path() -> None:
    """Without an override the root sits next to the DB file."""
    db_path = Path(settings.DB_PATH)
    assert kb_storage.kb_root() == db_path.with_name(db_path.stem + "_kb")


def test_kb_root_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """KB_STORAGE_DIR wins over the derived root."""
    monkeypatch.setattr(settings, "KB_STORAGE_DIR", str(tmp_path))
    assert kb_storage.kb_root() == tmp_path


def test_kb_dir_layout() -> None:
    """Paths follow <root>/<user>/<kb>/..."""
    assert kb_storage.kb_dir(7, 42).parts[-2:] == ("7", "42")
    assert kb_storage.uploads_dir(7, 42).name == "uploads"
    assert kb_storage.index_path(7, 42).name == "index.faiss"


def test_index_round_trip_cyrillic_path(tmp_path: Path) -> None:
    """An index survives a write/read through a non-ASCII directory."""
    rng = np.random.default_rng(0)
    vectors = rng.random((5, 8), dtype="float32")
    ids = [101, 102, 103, 104, 105]
    index = kb_storage.build_index(vectors.copy(), ids)
    path = tmp_path / "база знаний" / "index.faiss"
    kb_storage.write_index_bytes(path, index)

    loaded = kb_storage.read_index_bytes(path)
    assert loaded.ntotal == 5
    assert loaded.d == 8
    query = np.ascontiguousarray(vectors[2:3])
    faiss.normalize_L2(query)
    scores, found = loaded.search(query, 1)
    assert found[0][0] == 103
    assert scores[0][0] == pytest.approx(1.0, abs=1e-4)


def test_write_is_atomic(tmp_path: Path) -> None:
    """No temporary file is left behind after a write."""
    index = kb_storage.build_index(np.ones((2, 4), dtype="float32"), [1, 2])
    path = tmp_path / "index.faiss"
    kb_storage.write_index_bytes(path, index)
    assert path.exists()
    assert not (tmp_path / "index.tmp").exists()


def test_remove_kb_dir_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Removing a missing directory is a no-op; an existing one is deleted."""
    monkeypatch.setattr(settings, "KB_STORAGE_DIR", str(tmp_path))
    kb_storage.remove_kb_dir(1, 1)
    target = kb_storage.uploads_dir(1, 1)
    target.mkdir(parents=True)
    (target / "a.txt").write_text("x", encoding="utf-8")
    kb_storage.remove_kb_dir(1, 1)
    assert not kb_storage.kb_dir(1, 1).exists()


async def test_cleanup_kb_caches_cancels_task() -> None:
    """Cleanup cancels the pending job, returns it and drops the cached index."""
    task = asyncio.create_task(asyncio.sleep(30))
    kb_jobs[1] = task
    kb_index_cache[1] = object()

    popped = cleanup_kb_caches(1)

    assert popped is task
    assert 1 not in kb_jobs
    assert 1 not in kb_index_cache
    await asyncio.sleep(0)
    assert task.cancelled()
