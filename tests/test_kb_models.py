"""Knowledge-base table and cascade tests."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from shared.database import async_session_factory
from shared.models import KbChunk, KbDocument, KbStrategy, KnowledgeBase, User


async def _seed_kb() -> int:
    """Insert a user and an empty knowledge base, returning the KB id."""
    async with async_session_factory() as session:
        user = User(username="kbuser", password_hash="x")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        kb = KnowledgeBase(
            user_id=user.id,
            name="kb",
            strategy=KbStrategy.FIXED,
            chunk_size=500,
            chunk_overlap=50,
            embedding_model="m",
        )
        session.add(kb)
        await session.commit()
        await session.refresh(kb)
        return kb.id


async def test_kb_cascade_delete() -> None:
    """Deleting a KnowledgeBase removes its documents and chunks."""
    kb_id = await _seed_kb()
    async with async_session_factory() as session:
        doc = KbDocument(
            kb_id=kb_id, filename="a.txt", sha256="a" * 64, size_bytes=1, stored_name="s"
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        for i in range(2):
            session.add(
                KbChunk(
                    kb_id=kb_id,
                    document_id=doc.id,
                    chunk_index=i,
                    chunk_id=f"{doc.id}-{i}",
                    text="t",
                    source="a.txt",
                    title="a",
                    char_start=0,
                    char_end=1,
                )
            )
        await session.commit()

        kb = await session.get(KnowledgeBase, kb_id)
        await session.delete(kb)
        await session.commit()

        for model in (KbDocument, KbChunk):
            count = (await session.execute(select(func.count()).select_from(model))).scalar_one()
            assert count == 0


async def test_kb_document_unique_sha() -> None:
    """The same file hash cannot be stored twice in one knowledge base."""
    kb_id = await _seed_kb()
    async with async_session_factory() as session:
        for name in ("a.txt", "b.txt"):
            session.add(
                KbDocument(
                    kb_id=kb_id, filename=name, sha256="b" * 64, size_bytes=1, stored_name=name
                )
            )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
