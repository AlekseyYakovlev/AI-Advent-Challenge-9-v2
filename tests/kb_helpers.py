"""Shared seeding and fake-embedding helpers for knowledge-base indexing tests."""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlmodel import select

from agent import kb_indexer
from agent.embeddings import EmbeddingError
from shared.auth import hash_password
from shared.database import async_session_factory
from shared.kb_storage import uploads_dir
from shared.models import (
    KbChunk,
    KbDocument,
    KbStatus,
    KbStrategy,
    KnowledgeBase,
    User,
)

NOMIC = "text-embedding-nomic-embed-text-v1.5"
DIM = 8

RU_TEXT = (
    "Это пример русского текста для проверки индексации базы знаний. "
    "Каждое предложение достаточно длинное, чтобы чанки получились осмысленными. "
) * 40


async def seed_user(username: str = "kbuser") -> int:
    """Insert a user and return its id."""
    async with async_session_factory() as session:
        user = User(username=username, password_hash=hash_password("pw"))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def seed_kb(
    user_id: int,
    files: dict[str, str | bytes] | None = None,
    *,
    strategy: KbStrategy = KbStrategy.FIXED,
    chunk_size: int = 300,
    chunk_overlap: int = 50,
    status: KbStatus = KbStatus.QUEUED,
    model: str = NOMIC,
) -> int:
    """Insert a knowledge base with documents written into its uploads directory."""
    files = {"doc.txt": RU_TEXT} if files is None else files
    async with async_session_factory() as session:
        kb = KnowledgeBase(
            user_id=user_id,
            name="Тестовая база",
            status=status,
            strategy=strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            embedding_model=model,
            file_count=len(files),
        )
        session.add(kb)
        await session.commit()
        await session.refresh(kb)
        target = uploads_dir(user_id, kb.id)
        target.mkdir(parents=True, exist_ok=True)
        for index, (name, content) in enumerate(files.items()):
            data = content.encode("utf-8") if isinstance(content, str) else content
            stored = f"{index}.bin"
            (target / stored).write_bytes(data)
            session.add(
                KbDocument(
                    kb_id=kb.id,
                    filename=name,
                    sha256=hashlib.sha256(data + str(index).encode()).hexdigest(),
                    size_bytes=len(data),
                    stored_name=stored,
                )
            )
        await session.commit()
        return kb.id


async def get_kb(kb_id: int) -> KnowledgeBase | None:
    """Fetch a knowledge base row."""
    async with async_session_factory() as session:
        return await session.get(KnowledgeBase, kb_id)


async def chunk_rows(kb_id: int) -> list[KbChunk]:
    """Fetch all chunk rows of a knowledge base ordered by id."""
    async with async_session_factory() as session:
        return list(
            (
                await session.exec(
                    select(KbChunk).where(KbChunk.kb_id == kb_id).order_by(KbChunk.id)
                )
            ).all()
        )


def vector_for(text: str) -> list[float]:
    """Deterministic non-zero DIM-dimensional vector derived from the text."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [digest[i] / 255.0 + 0.01 for i in range(DIM)]


class FakeEmbedder:
    """Replaces model loading and embedding in kb_indexer, recording how it was called."""

    def __init__(
        self,
        *,
        sleep: float = 0.0,
        fail_on_batch: int | None = None,
        block: asyncio.Event | None = None,
        ensure_error: str | None = None,
    ) -> None:
        self.sleep = sleep
        self.fail_on_batch = fail_on_batch
        self.block = block
        self.ensure_error = ensure_error
        self.batch_sizes: list[int] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def ensure(self, model_id: str, *args: Any, **kwargs: Any) -> None:
        if self.ensure_error is not None:
            raise EmbeddingError(self.ensure_error)

    async def embed(self, model_id: str, texts: list[str], *args: Any) -> list[list[float]]:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            self.batch_sizes.append(len(texts))
            if self.block is not None:
                await self.block.wait()
            if self.sleep:
                await asyncio.sleep(self.sleep)
            if self.fail_on_batch is not None and len(self.batch_sizes) == self.fail_on_batch:
                raise EmbeddingError("Сбой эмбеддингов в тесте.")
            return [vector_for(text) for text in texts]
        finally:
            self.in_flight -= 1


def install_fake_embedder(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> FakeEmbedder:
    """Patch kb_indexer's embedding entry points with a FakeEmbedder."""
    fake = FakeEmbedder(**kwargs)
    monkeypatch.setattr(kb_indexer, "ensure_embedding_model", fake.ensure)
    monkeypatch.setattr(kb_indexer, "embed_passages", fake.embed)
    return fake


async def wait_until(predicate: Callable[[], Awaitable[bool] | bool], timeout: float = 5.0) -> None:
    """Poll until the predicate is true or fail after the timeout."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)
