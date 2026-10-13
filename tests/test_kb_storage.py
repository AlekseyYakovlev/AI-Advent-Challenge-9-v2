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


def _base_index(tmp_path: Path) -> tuple[Path, np.ndarray]:
    vectors = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]], dtype="float32")
    path = tmp_path / "index.faiss"
    kb_storage.write_index_bytes(path, kb_storage.build_index(vectors.copy(), [1, 2, 3]))
    return path, vectors


def _ids(index: faiss.Index) -> set[int]:
    return {int(i) for i in faiss.vector_to_array(index.id_map)}


def test_staging_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Staging directories live under the uploads directory and are unique."""
    monkeypatch.setattr(settings, "KB_STORAGE_DIR", str(tmp_path))
    assert kb_storage.staging_root(1, 2) == kb_storage.uploads_dir(1, 2) / "_staging"
    first = kb_storage.new_staging_dir(1, 2)
    second = kb_storage.new_staging_dir(1, 2)
    assert first.is_dir() and second.is_dir() and first != second
    assert first.parent == kb_storage.staging_root(1, 2)
    assert len(first.name) == 32
    int(first.name, 16)


def test_remove_tree(tmp_path: Path) -> None:
    """remove_tree deletes a populated directory and ignores a missing one."""
    target = tmp_path / "t"
    target.mkdir()
    (target / "a.txt").write_text("x", encoding="utf-8")
    kb_storage.remove_tree(target)
    assert not target.exists()
    kb_storage.remove_tree(target)


def test_patch_index_removes_and_adds(tmp_path: Path) -> None:
    """Patching drops removed ids, adds new ones and never touches the file."""
    path, vectors = _base_index(tmp_path)
    before = path.read_bytes()
    new = np.array([[0, 0, 0, 1], [1, 1, 0, 0]], dtype="float32")

    patched = kb_storage.patch_index(path, [2], new, [10, 11])

    assert patched.ntotal == 4
    assert _ids(patched) == {1, 3, 10, 11}
    assert np.allclose(patched.reconstruct(1), vectors[0])
    assert path.read_bytes() == before


def test_patch_index_remove_only_and_add_only(tmp_path: Path) -> None:
    """Either side of the patch may be empty."""
    path, _ = _base_index(tmp_path)
    assert kb_storage.patch_index(path, [2], None, []).ntotal == 2
    added = kb_storage.patch_index(path, [], np.ones((1, 4), dtype="float32"), [7])
    assert added.ntotal == 4


def test_patch_index_rejects_wrong_dimension(tmp_path: Path) -> None:
    """Vectors of another dimension are refused."""
    path, _ = _base_index(tmp_path)
    with pytest.raises(ValueError):
        kb_storage.patch_index(path, [], np.ones((1, 5), dtype="float32"), [9])


def test_pending_write_and_promote(tmp_path: Path) -> None:
    """The pending file leaves the live one alone until promoted."""
    path, _ = _base_index(tmp_path)
    before = path.read_bytes()
    patched = kb_storage.patch_index(path, [1], None, [])

    pending = kb_storage.write_index_pending(path, patched)

    assert pending == path.with_suffix(".pending")
    assert path.read_bytes() == before
    kb_storage.promote_index(pending, path)
    assert kb_storage.read_index_bytes(path).ntotal == 2
    assert not pending.exists()


def test_remove_index_file_removes_pending(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A leftover pending file is removed with the index."""
    monkeypatch.setattr(settings, "KB_STORAGE_DIR", str(tmp_path))
    target = kb_storage.index_path(1, 1)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    target.with_suffix(".pending").write_bytes(b"y")
    kb_storage.remove_index_file(1, 1)
    assert not target.exists()
    assert not target.with_suffix(".pending").exists()


def test_progress_frame_summary_is_optional() -> None:
    """The summary key appears only when given."""
    from datetime import datetime, timezone

    from agent.kb_schemas import kb_progress_frame
    from shared.models import KbStatus, KbStrategy, KnowledgeBase

    kb = KnowledgeBase(
        id=1, user_id=1, name="n", embedding_model="m", status=KbStatus.READY, strategy=KbStrategy.FIXED,
        chunk_size=300, chunk_overlap=50, file_count=1, chunk_count=1, done_chunks=1,
        total_chunks=1, created_at=datetime.now(timezone.utc),
    )
    assert set(kb_progress_frame(kb)) == {"type", "kb"}
    summary = {"added": 1, "replaced": 0, "unchanged": 2}
    assert kb_progress_frame(kb, add_summary=summary)["add_summary"] == summary
