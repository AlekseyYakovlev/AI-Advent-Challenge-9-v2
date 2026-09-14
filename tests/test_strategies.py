"""Context compression strategy tests."""

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from agent.context_engine import (
    RECENT_MESSAGE_COUNT,
    ContextOverflowError,
    build_llm_context,
)
from agent.main import app
from agent.llm_client import llm_client
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Chat, ContextStrategy, Message, Settings

BASE_URL = settings.LM_STUDIO_BASE_URL
MODEL = "test-model"
TRUNCATE_INITIAL_COUNT = 2


def _history_only(messages: list[dict]) -> list[dict]:
    """Strip system prompt from build_llm_context output."""
    return [msg for msg in messages if msg["role"] != "system"]


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


async def _count_messages(session, chat_id: int) -> int:
    """Return total message rows stored for a chat."""
    result = await session.exec(
        select(Message).where(Message.chat_id == chat_id),
    )
    return len(result.all())


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
    """TRUNCATE_MIDDLE should send first two and last messages to LLM."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "Should not be called."}},
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
        second_msg = "SECOND_MESSAGE_IMPORTANT " + "a" * 200
        middle_msg = "MIDDLE_MESSAGE_CUT " + "y" * 200
        last_msg = "LAST_MESSAGE_RECENT " + "z" * 200
        padding = " word" * 80

        async with async_session_factory() as session:
            chat = await session.get(Chat, chat_id)
            assert chat is not None
            contents: list[str] = []
            contents.extend(
                (first_msg if i == 0 else second_msg if i == 1 else f"first_{i}{padding}")
                for i in range(5)
            )
            contents.extend(
                (middle_msg if i == 0 else f"middle_{i}{padding}") for i in range(10)
            )
            contents.extend(
                (last_msg if i == 4 else f"recent_{i}{padding}") for i in range(5)
            )
            await _append_messages(session, chat, contents)

            assert len(await _load_message_dicts(session, chat)) >= 20

            llm_context = _history_only(
                await build_llm_context(session, chat_id, MODEL),
            )

        assert llm_context[0]["content"] == first_msg
        assert llm_context[1]["content"] == second_msg
        assert llm_context[-1]["content"] == last_msg
        assert middle_msg not in {msg["content"] for msg in llm_context}
        assert len(respx.calls) == 0


@respx.mock
@pytest.mark.asyncio
async def test_truncate_middle_keeps_initial_and_recent_counts() -> None:
    """TRUNCATE_MIDDLE keeps first 2 and last 10 messages for LLM."""
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
        llm_context = _history_only(
            await build_llm_context(session, chat.id, MODEL),
        )

    padding = " word" * 50
    user_contents = [
        msg["content"] for msg in llm_context if msg["role"] == "user"
    ]
    assert user_contents[:TRUNCATE_INITIAL_COUNT] == [
        f"msg_{i}{padding}" for i in range(TRUNCATE_INITIAL_COUNT)
    ]
    assert user_contents[-RECENT_MESSAGE_COUNT:] == [
        f"msg_{i}{padding}" for i in range(15, 25)
    ]
    assert len(llm_context) == TRUNCATE_INITIAL_COUNT + RECENT_MESSAGE_COUNT


@pytest.mark.asyncio
async def test_branching_strategy_fallback() -> None:
    """Legacy branching strategy should migrate to sliding and trim to recent messages."""
    from sqlalchemy import text

    from migrate_strategies import migrate

    async with async_session_factory() as session:
        chat = Chat(title="Branching legacy")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        chat_id = chat.id
        await session.execute(
            text(
                "INSERT INTO settings (chat_id, system_prompt, temperature, "
                "context_length, max_tokens, strategy, facts_json, summary_text) "
                "VALUES (:chat_id, 'You are a helpful assistant.', 0.7, 512, 4096, "
                "'branching', '{}', '')"
            ),
            {"chat_id": chat_id},
        )
        await session.commit()

    await migrate()

    async with async_session_factory() as session:
        settings_row = (
            await session.exec(select(Settings).where(Settings.chat_id == chat_id))
        ).one()
        assert settings_row.strategy == ContextStrategy.SLIDING_WINDOW

        chat = (await session.exec(select(Chat).where(Chat.id == chat_id))).one()
        contents = [f"msg_{i}" + " word" * 50 for i in range(20)]
        await _append_messages(session, chat, contents)
        llm_context = _history_only(
            await build_llm_context(session, chat_id, MODEL),
        )

    assert len(llm_context) == RECENT_MESSAGE_COUNT


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
        llm_context = _history_only(
            await build_llm_context(session, chat.id, MODEL),
        )

    assert len(llm_context) == RECENT_MESSAGE_COUNT
    assert llm_context[-1]["content"].startswith("msg_19")


@pytest.mark.asyncio
async def test_no_compression_sends_all_messages() -> None:
    """NO_COMPRESSION should send the full history when under context window."""
    async with async_session_factory() as session:
        chat = Chat(title="No compression")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        session.add(
            Settings(
                chat_id=chat.id,
                strategy=ContextStrategy.NO_COMPRESSION.value,
                context_length=8192,
            ),
        )
        await session.commit()

        contents = [f"msg_{i}" + " word" * 50 for i in range(20)]
        await _append_messages(session, chat, contents)
        all_messages = await _load_message_dicts(session, chat)

        llm_context = _history_only(
            await build_llm_context(session, chat.id, MODEL),
        )

    assert len(llm_context) == len(all_messages)


@pytest.mark.asyncio
async def test_sticky_facts_strategy() -> None:
    """STICKY_FACTS should keep the first message and recent messages."""
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
        llm_context = _history_only(
            await build_llm_context(session, chat.id, MODEL),
        )

    assert llm_context[0]["content"].startswith("msg_0")
    assert llm_context[-1]["content"].startswith("msg_19")
    assert len(llm_context) == RECENT_MESSAGE_COUNT + 1
    assert len(llm_context) == len({msg["content"] for msg in llm_context})


@pytest.mark.asyncio
async def test_sticky_facts_no_duplication_small_chat() -> None:
    """STICKY_FACTS with <=10 messages should not duplicate the first message."""
    async with async_session_factory() as session:
        chat = Chat(title="Sticky small")
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

        contents = [f"msg_{i}" + " word" * 50 for i in range(5)]
        await _append_messages(session, chat, contents)
        llm_context = _history_only(
            await build_llm_context(session, chat.id, MODEL),
        )

    assert len(llm_context) == 5
    assert len(llm_context) == len({msg["content"] for msg in llm_context})


@pytest.mark.asyncio
async def test_no_compression_preserves_all_messages(client: AsyncClient) -> None:
    """NO_COMPRESSION: all messages preserved in DB."""
    chat_resp = await client.post("/api/v1/chats", json={"title": "Test"})
    chat_id = chat_resp.json()["id"]

    await client.put(
        "/api/v1/settings",
        json={
            "chat_id": chat_id,
            "strategy": "no_compression",
            "context_length": 4096,
        },
    )

    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        await _append_messages(session, chat, ["x" * 200] * 10)

    tree_resp = await client.get(f"/api/v1/chats/{chat_id}/tree")
    assert len(tree_resp.json()) >= 10


@pytest.mark.asyncio
async def test_sliding_window_preserves_database(client: AsyncClient) -> None:
    """SLIDING_WINDOW: old messages preserved in DB but not sent to LLM."""
    chat_resp = await client.post("/api/v1/chats", json={"title": "Test"})
    chat_id = chat_resp.json()["id"]

    await client.put(
        "/api/v1/settings",
        json={
            "chat_id": chat_id,
            "strategy": "sliding",
            "context_length": 1000,
        },
    )

    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        await _append_messages(session, chat, ["x" * 200] * 20)
        before = await _count_messages(session, chat_id)
        await build_llm_context(session, chat_id, MODEL)
        after = await _count_messages(session, chat_id)

    assert before == after
    tree_resp = await client.get(f"/api/v1/chats/{chat_id}/tree")
    assert len(tree_resp.json()) >= 20


@pytest.mark.asyncio
async def test_sticky_facts_preserves_database(client: AsyncClient) -> None:
    """STICKY_FACTS: all messages preserved in DB."""
    chat_resp = await client.post("/api/v1/chats", json={"title": "Test"})
    chat_id = chat_resp.json()["id"]

    await client.put(
        "/api/v1/settings",
        json={
            "chat_id": chat_id,
            "strategy": "sticky",
            "context_length": 1000,
        },
    )

    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        await _append_messages(session, chat, ["x" * 200] * 20)
        before = await _count_messages(session, chat_id)
        await build_llm_context(session, chat_id, MODEL)
        after = await _count_messages(session, chat_id)

    assert before == after
    tree_resp = await client.get(f"/api/v1/chats/{chat_id}/tree")
    assert len(tree_resp.json()) >= 20


@pytest.mark.asyncio
async def test_strategy_setting_persists(client: AsyncClient) -> None:
    """Strategy setting persists in database."""
    chat_resp = await client.post("/api/v1/chats", json={"title": "Test"})
    chat_id = chat_resp.json()["id"]

    await client.put(
        "/api/v1/settings",
        json={"chat_id": chat_id, "strategy": "no_compression"},
    )

    settings_resp = await client.get(f"/api/v1/settings?chat_id={chat_id}")
    assert settings_resp.json()["strategy"] == "no_compression"


@pytest.mark.asyncio
async def test_context_length_setting_persists(client: AsyncClient) -> None:
    """Context length setting persists in database."""
    chat_resp = await client.post("/api/v1/chats", json={"title": "Test"})
    chat_id = chat_resp.json()["id"]

    await client.put(
        "/api/v1/settings",
        json={"chat_id": chat_id, "context_length": 2048},
    )

    settings_resp = await client.get(f"/api/v1/settings?chat_id={chat_id}")
    assert settings_resp.json()["context_length"] == 2048


@pytest.mark.asyncio
async def test_no_compression_raises_context_overflow() -> None:
    """NO_COMPRESSION raises ContextOverflowError when context exceeds window."""
    async with async_session_factory() as session:
        chat = Chat(title="Overflow")
        session.add(chat)
        session.add(
            Settings(
                chat_id=chat.id,
                strategy=ContextStrategy.NO_COMPRESSION.value,
                context_length=500,
            ),
        )
        await session.commit()
        await session.refresh(chat)

        contents = [f"msg_{i}" + " word" * 80 for i in range(20)]
        await _append_messages(session, chat, contents)
        with pytest.raises(ContextOverflowError):
            await build_llm_context(session, chat.id, MODEL)
