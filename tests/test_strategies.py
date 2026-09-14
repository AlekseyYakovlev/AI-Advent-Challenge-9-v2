"""Context compression strategy tests."""

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from agent.context_engine import (
    INITIAL_CONTEXT_COUNT,
    RECENT_CONTEXT_COUNT,
    build_llm_context,
)
from agent.main import app
from agent.llm_client import llm_client
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Chat, ContextStrategy, Message, Settings

BASE_URL = settings.LM_STUDIO_BASE_URL
MODEL = "test-model"


async def _append_messages(
    session,
    chat: Chat,
    contents: list[str],
    role: str = "user",
) -> None:
    """Append a linear chain of messages and advance the chat leaf."""
    parent_id = chat.current_leaf_message_id
    for content in contents:
        msg = Message(
            chat_id=chat.id,
            parent_id=parent_id,
            role=role,
            content=content,
            token_count=llm_client.count_tokens(content),
        )
        session.add(msg)
        await session.flush()
        parent_id = msg.id
    chat.current_leaf_message_id = parent_id
    session.add(chat)
    await session.commit()


async def _load_message_dicts(session, chat: Chat) -> list[dict[str, str]]:
    """Return active-branch messages as role/content dicts."""
    if chat.current_leaf_message_id is None:
        return []
    path: list[Message] = []
    current_id: int | None = chat.current_leaf_message_id
    while current_id is not None:
        message = await session.get(Message, current_id)
        if message is None or message.chat_id != chat.id:
            break
        path.append(message)
        current_id = message.parent_id
    return [
        {"role": msg.role, "content": msg.content}
        for msg in reversed(path)
    ]


@pytest.mark.asyncio
async def test_truncate_middle_preserves_database() -> None:
    """TRUNCATE_MIDDLE: middle cut from LLM context but all messages preserved in DB."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        chat_resp = await client.post("/api/v1/chats", json={"title": "Test"})
        chat_id = chat_resp.json()["id"]

        await client.put(
            "/api/v1/settings",
            json={
                "chat_id": chat_id,
                "strategy": "truncate_middle",
                "context_length": 500,
            },
        )

        async with async_session_factory() as session:
            chat = await session.get(Chat, chat_id)
            assert chat is not None
            contents = [f"Message {i}: " + "x" * 200 for i in range(25)]
            await _append_messages(session, chat, contents)

        tree_resp = await client.get(f"/api/v1/chats/{chat_id}/tree")
        messages = tree_resp.json()
        assert len(messages) >= 25, "Database must preserve all messages"


@respx.mock
@pytest.mark.asyncio
async def test_truncate_middle_sends_beginning_and_end() -> None:
    """TRUNCATE_MIDDLE should send first and last messages to LLM."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "Middle part was about testing."}},
                ],
            },
        ),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        chat_resp = await client.post("/api/v1/chats", json={"title": "Test"})
        chat_id = chat_resp.json()["id"]

        await client.put(
            "/api/v1/settings",
            json={
                "chat_id": chat_id,
                "strategy": "truncate_middle",
                "context_length": 512,
            },
        )

        first_msg = "FIRST_MESSAGE_IMPORTANT_CONTEXT " + "x" * 200
        middle_msg = "MIDDLE_MESSAGE_CUT " + "y" * 200
        last_msg = "LAST_MESSAGE_RECENT " + "z" * 200
        padding = " word" * 80

        async with async_session_factory() as session:
            chat = await session.get(Chat, chat_id)
            assert chat is not None
            contents: list[str] = []
            contents.extend(
                (first_msg if i == 0 else f"first_{i}{padding}") for i in range(5)
            )
            contents.extend(
                (middle_msg if i == 0 else f"middle_{i}{padding}") for i in range(10)
            )
            contents.extend(
                (last_msg if i == 4 else f"recent_{i}{padding}") for i in range(5)
            )
            await _append_messages(session, chat, contents)

            all_messages = await _load_message_dicts(session, chat)
            assert len(all_messages) >= 20

            llm_context = await build_llm_context(
                session, chat_id, all_messages, MODEL,
            )

        assert llm_context[0]["content"] == first_msg
        assert llm_context[-1]["content"] == last_msg
        assert any(
            msg["role"] == "system" and "Middle of conversation summary" in msg["content"]
            for msg in llm_context
        )
        assert middle_msg not in {msg["content"] for msg in llm_context}


@respx.mock
@pytest.mark.asyncio
async def test_truncate_middle_keeps_initial_and_recent_counts() -> None:
    """TRUNCATE_MIDDLE keeps first 5 and last 10 messages for LLM."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Summary."}}]},
        ),
    )

    async with async_session_factory() as session:
        chat = Chat(title="Truncate counts")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        session.add(
            Settings(
                chat_id=chat.id,
                strategy=ContextStrategy.TRUNCATE_MIDDLE.value,
                context_length=512,
            ),
        )
        await session.commit()

        contents = [f"msg_{i}" + " word" * 50 for i in range(25)]
        await _append_messages(session, chat, contents)
        all_messages = await _load_message_dicts(session, chat)

        llm_context = await build_llm_context(session, chat.id, all_messages, MODEL)

    padding = " word" * 50
    user_contents = [
        msg["content"] for msg in llm_context if msg["role"] == "user"
    ]
    assert user_contents[:INITIAL_CONTEXT_COUNT] == [
        f"msg_{i}{padding}" for i in range(INITIAL_CONTEXT_COUNT)
    ]
    assert user_contents[-RECENT_CONTEXT_COUNT:] == [
        f"msg_{i}{padding}" for i in range(15, 25)
    ]


@pytest.mark.asyncio
async def test_branching_strategy_fallback() -> None:
    """Legacy branching strategy should fall back to recent messages."""
    async with async_session_factory() as session:
        chat = Chat(title="Branching legacy")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        session.add(
            Settings(
                chat_id=chat.id,
                strategy="branching",
                context_length=512,
            ),
        )
        await session.commit()

        contents = [f"msg_{i}" + " word" * 50 for i in range(20)]
        await _append_messages(session, chat, contents)
        all_messages = await _load_message_dicts(session, chat)

        llm_context = await build_llm_context(session, chat.id, all_messages, MODEL)

    assert len(llm_context) == RECENT_CONTEXT_COUNT


@pytest.mark.asyncio
async def test_sliding_window_strategy() -> None:
    """SLIDING_WINDOW should return only the most recent messages."""
    async with async_session_factory() as session:
        chat = Chat(title="Sliding")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        session.add(
            Settings(
                chat_id=chat.id,
                strategy=ContextStrategy.SLIDING_WINDOW.value,
                context_length=512,
            ),
        )
        await session.commit()

        contents = [f"msg_{i}" + " word" * 50 for i in range(20)]
        await _append_messages(session, chat, contents)
        all_messages = await _load_message_dicts(session, chat)

        llm_context = await build_llm_context(session, chat.id, all_messages, MODEL)

    assert len(llm_context) == RECENT_CONTEXT_COUNT
    assert llm_context[-1]["content"].startswith("msg_19")


@pytest.mark.asyncio
async def test_no_compression_sends_all_messages() -> None:
    """NO_COMPRESSION should send the full history even above threshold."""
    async with async_session_factory() as session:
        chat = Chat(title="No compression")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        session.add(
            Settings(
                chat_id=chat.id,
                strategy=ContextStrategy.NO_COMPRESSION.value,
                context_length=512,
            ),
        )
        await session.commit()

        contents = [f"msg_{i}" + " word" * 50 for i in range(20)]
        await _append_messages(session, chat, contents)
        all_messages = await _load_message_dicts(session, chat)

        llm_context = await build_llm_context(session, chat.id, all_messages, MODEL)

    assert len(llm_context) == len(all_messages)


@respx.mock
@pytest.mark.asyncio
async def test_sticky_facts_strategy() -> None:
    """STICKY_FACTS should summarize old messages and keep recent ones."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Sticky summary."}}]},
        ),
    )

    async with async_session_factory() as session:
        chat = Chat(title="Sticky")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        session.add(
            Settings(
                chat_id=chat.id,
                strategy=ContextStrategy.STICKY_FACTS.value,
                context_length=512,
            ),
        )
        await session.commit()

        contents = [f"msg_{i}" + " word" * 50 for i in range(20)]
        await _append_messages(session, chat, contents)
        all_messages = await _load_message_dicts(session, chat)

        llm_context = await build_llm_context(session, chat.id, all_messages, MODEL)

        result = await session.exec(
            select(Settings).where(Settings.chat_id == chat.id),
        )
        row = result.first()
        assert row is not None

    assert any(
        msg["role"] == "system" and "Conversation summary" in msg["content"]
        for msg in llm_context
    )
    assert len([msg for msg in llm_context if msg["role"] == "user"]) == RECENT_CONTEXT_COUNT
    assert row.summary_text == ""
