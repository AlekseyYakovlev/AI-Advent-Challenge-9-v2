"""Task-memory lines in the system prompt of RAG chats."""

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import SQLAlchemyError

from agent import memory, task_memory
from agent.context_engine import build_system_prompt, compute_chat_stats
from agent.task_memory import TaskMemoryDoc, TaskMemoryItem
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Chat, ChatRagConfig, Settings

MODEL = "test-model"
MARK = "Память задачи (этот чат):"


def _doc() -> TaskMemoryDoc:
    return TaskMemoryDoc(
        goal="Разобраться с разделом 3",
        clarified=[TaskMemoryItem(id=1, text="интересует раздел 5")],
        constraints=[TaskMemoryItem(id=2, text="только тема 7919")],
        next_id=3,
    )


async def _chat(user_id: int, *, mode: str | None = "rag", doc: TaskMemoryDoc | None) -> int:
    async with async_session_factory() as session:
        chat = Chat(title="Task memory", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        session.add(Settings(chat_id=chat.id, system_prompt="Base prompt"))
        if mode is not None:
            session.add(ChatRagConfig(chat_id=chat.id, kb_id=None, mode=mode, strict=False))
        if doc is not None:
            await task_memory.stage_doc(session, chat, doc)
        await session.commit()
        return chat.id


@pytest.mark.asyncio
async def test_rag_chat_prompt_has_labelled_lines_between_blocks(
    authenticated_client: AsyncClient,
) -> None:
    user_id = authenticated_client.seeded_user_id
    chat_id = await _chat(user_id, doc=_doc())
    async with async_session_factory() as session:
        await memory.save_working_memory(session, user_id, chat_id, "step", "intro")
        await memory.save_long_term_memory(session, user_id, "pref", "short answers")
        prompt = await build_system_prompt(session, chat_id)
    assert MARK in prompt
    assert "Цель диалога: Разобраться с разделом 3" in prompt
    assert "Уточнено пользователем: интересует раздел 5" in prompt
    assert "Ограничения и термины (соблюдай их): только тема 7919" in prompt
    assert prompt.index("Working memory") < prompt.index(MARK)
    assert prompt.index(MARK) < prompt.index("Long-term memory")


@pytest.mark.asyncio
async def test_empty_document_adds_nothing(authenticated_client: AsyncClient) -> None:
    chat_id = await _chat(authenticated_client.seeded_user_id, doc=None)
    async with async_session_factory() as session:
        assert await build_system_prompt(session, chat_id) == "Base prompt"


@pytest.mark.asyncio
async def test_rag_off_chat_with_stored_document_adds_nothing(
    authenticated_client: AsyncClient,
) -> None:
    chat_id = await _chat(authenticated_client.seeded_user_id, mode="off", doc=_doc())
    async with async_session_factory() as session:
        assert MARK not in await build_system_prompt(session, chat_id)


@pytest.mark.asyncio
async def test_flag_off_adds_nothing(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat_id = await _chat(authenticated_client.seeded_user_id, doc=_doc())
    monkeypatch.setattr(settings, "TASK_MEMORY_ENABLED", False)
    async with async_session_factory() as session:
        assert MARK not in await build_system_prompt(session, chat_id)


@pytest.mark.asyncio
async def test_working_memory_line_holds_no_task_memory(
    authenticated_client: AsyncClient,
) -> None:
    user_id = authenticated_client.seeded_user_id
    chat_id = await _chat(user_id, doc=_doc())
    async with async_session_factory() as session:
        await memory.save_working_memory(session, user_id, chat_id, "step", "intro")
        prompt = await build_system_prompt(session, chat_id)
    line = next(row for row in prompt.splitlines() if row.startswith("Working memory"))
    assert line == 'Working memory (this chat\'s current task data): {"step": "intro"}'


@pytest.mark.asyncio
async def test_stats_count_the_added_lines(authenticated_client: AsyncClient) -> None:
    user_id = authenticated_client.seeded_user_id
    with_doc = await _chat(user_id, doc=_doc())
    without = await _chat(user_id, doc=None)
    async with async_session_factory() as session:
        big = await compute_chat_stats(session, with_doc, MODEL)
        small = await compute_chat_stats(session, without, MODEL)
    assert big != small
    assert big["current_context_size"] > small["current_context_size"]


@pytest.mark.asyncio
async def test_load_failure_does_not_break_prompt(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat_id = await _chat(authenticated_client.seeded_user_id, doc=_doc())

    async def boom(*args: object) -> TaskMemoryDoc:
        raise SQLAlchemyError("db down")

    monkeypatch.setattr(task_memory, "load_doc", boom)
    async with async_session_factory() as session:
        assert await build_system_prompt(session, chat_id) == "Base prompt"
