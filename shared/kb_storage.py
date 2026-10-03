"""On-disk storage helpers for knowledge-base uploads and FAISS indexes."""

import os
import shutil
from pathlib import Path

import faiss
import numpy as np

from shared.config import settings
from shared.logger import get_logger

logger = get_logger(__name__)


def kb_root() -> Path:
    """Return the storage root, derived from DB_PATH unless KB_STORAGE_DIR is set."""
    if settings.KB_STORAGE_DIR:
        return Path(settings.KB_STORAGE_DIR)
    db_path = Path(settings.DB_PATH)
    return db_path.with_name(f"{db_path.stem}_kb")


def kb_dir(user_id: int, kb_id: int) -> Path:
    """Return the directory holding one knowledge base's files."""
    return kb_root() / str(user_id) / str(kb_id)


def uploads_dir(user_id: int, kb_id: int) -> Path:
    """Return the directory holding uploaded source files."""
    return kb_dir(user_id, kb_id) / "uploads"


def index_path(user_id: int, kb_id: int) -> Path:
    """Return the FAISS index file path."""
    return kb_dir(user_id, kb_id) / "index.faiss"


def build_index(vectors: np.ndarray, ids: list[int]) -> faiss.Index:
    """Build a cosine-similarity index (L2-normalized inner product) keyed by ids."""
    matrix = np.ascontiguousarray(vectors, dtype="float32")
    if matrix.ndim != 2:
        raise ValueError("vectors must be a 2-D array")
    faiss.normalize_L2(matrix)
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(matrix.shape[1]))
    index.add_with_ids(matrix, np.asarray(ids, dtype="int64"))
    return index


def write_index_bytes(path: Path, index: faiss.Index) -> None:
    """Serialize the index to bytes and replace the file atomically.

    Bytes are written through Python I/O because faiss.write_index fails on
    non-ASCII paths on Windows.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_bytes(faiss.serialize_index(index).tobytes())
    os.replace(tmp_path, path)


def read_index_bytes(path: Path) -> faiss.Index:
    """Load an index previously written by write_index_bytes."""
    return faiss.deserialize_index(np.frombuffer(path.read_bytes(), dtype="uint8"))


def remove_kb_dir(user_id: int, kb_id: int) -> None:
    """Delete a knowledge base's directory if it exists."""
    target = kb_dir(user_id, kb_id)
    if target.exists():
        shutil.rmtree(target)


def remove_index_file(user_id: int, kb_id: int) -> None:
    """Delete the index file and any leftover temporary file."""
    index_path(user_id, kb_id).unlink(missing_ok=True)
    index_path(user_id, kb_id).with_suffix(".tmp").unlink(missing_ok=True)
