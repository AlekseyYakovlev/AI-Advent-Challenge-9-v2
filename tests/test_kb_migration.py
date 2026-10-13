"""Tests for the kbdocument rebuild that drops UNIQUE(kb_id, sha256)."""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from shared.database import (
    async_session_factory,
    engine,
    init_db,
    migrate_kbdocument_drop_sha_unique,
)
from shared.models import KbChunk, KbDocument, KbStrategy, KnowledgeBase, User

OLD_CONSTRAINT = "uq_kbdocument_kb_sha256"


async def _downgrade_kbdocument_to_old_schema() -> None:
    """Rebuild kbdocument into the pre-migration shape with the sha unique constraint."""
    autocommit = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit.connect() as conn:
        await conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            await conn.exec_driver_sql("DROP TABLE kbdocument")
            await conn.exec_driver_sql(
                "CREATE TABLE kbdocument ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "kb_id INTEGER NOT NULL REFERENCES knowledgebase (id) ON DELETE CASCADE, "
                "filename VARCHAR NOT NULL, sha256 VARCHAR(64) NOT NULL, "
                "size_bytes INTEGER NOT NULL, page_count INTEGER, stored_name VARCHAR NOT NULL, "
                f"CONSTRAINT {OLD_CONSTRAINT} UNIQUE (kb_id, sha256))"
            )
            await conn.exec_driver_sql("CREATE INDEX ix_kbdocument_kb_id ON kbdocument (kb_id)")
        finally:
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")


async def _seed(doc_count: int = 2, chunk_count: int = 3, same_sha: bool = False) -> int:
    """Insert a user, a knowledge base, documents and chunks; returns the kb id."""
    async with async_session_factory() as session:
        user = User(username="migr", password_hash="x")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        kb = KnowledgeBase(
            user_id=user.id, name="kb", strategy=KbStrategy.FIXED, chunk_size=500,
            chunk_overlap=50, embedding_model="m",
        )
        session.add(kb)
        await session.commit()
        await session.refresh(kb)
        docs: list[KbDocument] = []
        for i in range(doc_count):
            sha = "c" * 64 if same_sha else f"{i}" * 64
            doc = KbDocument(
                kb_id=kb.id, filename=f"d{i}.txt", sha256=sha, size_bytes=1, stored_name=f"s{i}"
            )
            session.add(doc)
            docs.append(doc)
        await session.commit()
        for i in range(chunk_count):
            doc = docs[i % len(docs)]
            session.add(
                KbChunk(
                    kb_id=kb.id, document_id=doc.id, chunk_index=i, chunk_id=f"{doc.id}-{i}",
                    text="t", source=doc.filename, title="d", char_start=0, char_end=1,
                )
            )
        await session.commit()
        return kb.id


async def _count(model: type) -> int:
    async with async_session_factory() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _master(name: str) -> str | None:
    async with async_session_factory() as session:
        row = (
            await session.execute(
                text("SELECT sql FROM sqlite_master WHERE name = :n"), {"n": name}
            )
        ).first()
    return None if row is None else (row[0] or "")


async def test_old_schema_rejects_same_sha() -> None:
    """The downgrade helper really reproduces the old unique constraint."""
    await _downgrade_kbdocument_to_old_schema()
    with pytest.raises(IntegrityError):
        await _seed(doc_count=2, chunk_count=0, same_sha=True)


async def test_migration_preserves_chunks() -> None:
    """Rebuilding kbdocument keeps every document and chunk row."""
    await _downgrade_kbdocument_to_old_schema()
    await _seed(doc_count=2, chunk_count=3)
    async with async_session_factory() as session:
        ids_before = sorted((await session.execute(select(KbDocument.id))).scalars().all())

    await migrate_kbdocument_drop_sha_unique()

    assert await _count(KbChunk) == 3
    assert await _count(KbDocument) == 2
    async with async_session_factory() as session:
        ids_after = sorted((await session.execute(select(KbDocument.id))).scalars().all())
    assert ids_after == ids_before
    assert OLD_CONSTRAINT not in (await _master("kbdocument") or "")
    assert await _master("ix_kbdocument_kb_id") is not None
    assert await _master("ix_kbdocument_kb_filename") is not None


async def test_migration_allows_same_sha_and_keeps_cascade() -> None:
    """After the rebuild equal hashes are accepted and FK cascade still works."""
    await _downgrade_kbdocument_to_old_schema()
    await migrate_kbdocument_drop_sha_unique()
    kb_id = await _seed(doc_count=2, chunk_count=2, same_sha=True)
    assert await _count(KbDocument) == 2

    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        await session.delete(kb)
        await session.commit()
    assert await _count(KbDocument) == 0
    assert await _count(KbChunk) == 0
    async with async_session_factory() as session:
        flag = (await session.execute(text("PRAGMA foreign_keys"))).scalar_one()
    assert flag == 1


async def test_migration_is_idempotent() -> None:
    """A second run changes nothing and raises nothing."""
    await _downgrade_kbdocument_to_old_schema()
    await _seed(doc_count=2, chunk_count=3)
    await migrate_kbdocument_drop_sha_unique()
    await migrate_kbdocument_drop_sha_unique()
    assert await _count(KbDocument) == 2
    assert await _count(KbChunk) == 3


async def test_init_db_runs_migration() -> None:
    """init_db rebuilds an old table at start-up without losing chunks."""
    await _downgrade_kbdocument_to_old_schema()
    await _seed(doc_count=1, chunk_count=2)
    await init_db()
    assert OLD_CONSTRAINT not in (await _master("kbdocument") or "")
    assert await _count(KbChunk) == 2
