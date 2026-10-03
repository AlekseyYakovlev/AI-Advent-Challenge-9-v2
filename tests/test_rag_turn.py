"""Tests for the fail-soft RAG pre-step of a chat turn."""

import asyncio
from typing import Any

import pytest

from agent import kb_search, rag_turn
from agent.kb_indexer import run_index_job
from agent.llm_client import count_tokens
from agent.rag import BLOCK_OPEN, RagFailure
from agent.rag_turn import prepare_rag_turn
from kb_helpers import get_kb, install_fake_embedder, seed_kb, seed_user, vector_for
from shared.database import async_session_factory
from shared.models import Chat, ChatRagConfig, KbStatus

DISTINCT = {"doc.txt": " ".join(f"Раздел номер {i} описывает тему {i * 7919}." for i in range(80))}
QUESTION = "О чём раздел номер 3?"
CTX = 8192


async def _ready_kb(monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    kb_id = await seed_kb(user_id, DISTINCT, chunk_size=120, chunk_overlap=10)
    await run_index_job(kb_id, user_id)
    assert (await get_kb(kb_id)).status == KbStatus.READY

    async def fake_query(model_id: str, query: str, *args: object) -> list[float]:
        return vector_for("Раздел номер 3 описывает тему")

    monkeypatch.setattr(kb_search, "embed_query", fake_query)
    return user_id, kb_id


async def _chat(user_id: int, kb_id: int | None, mode: str = "rag", top_k: int = 3) -> int:
    async with async_session_factory() as session:
        chat = Chat(title="t", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        session.add(ChatRagConfig(chat_id=chat.id, kb_id=kb_id, mode=mode, top_k=top_k))
        await session.commit()
        return chat.id


def _messages() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "sys", "token_count": 1},
        {"role": "user", "content": QUESTION, "token_count": count_tokens(QUESTION)},
    ]


async def _run(
    chat_id: int, msgs: list[dict[str, Any]], ctx: int = CTX, max_tokens: int = 512
) -> rag_turn.RagTurn:
    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        return await prepare_rag_turn(session, chat, QUESTION, msgs, ctx, max_tokens)


async def test_no_config_is_off() -> None:
    user_id = await seed_user()
    async with async_session_factory() as session:
        chat = Chat(title="t", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        chat_id = chat.id
    msgs = _messages()
    turn = await _run(chat_id, msgs)
    assert turn.mode == "off"
    assert msgs == _messages()
    assert turn.payload["sources"] == []
    assert turn.payload["warning"] is None


async def test_mode_off_with_kb_does_not_retrieve(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _ready_kb(monkeypatch)
    chat_id = await _chat(user_id, kb_id, mode="off")

    async def boom(*args: object) -> list[dict[str, Any]]:
        raise AssertionError("retrieve must not run")

    monkeypatch.setattr(rag_turn, "retrieve", boom)
    msgs = _messages()
    turn = await _run(chat_id, msgs)
    assert turn.mode == "off"
    assert turn.payload["kb_id"] == kb_id
    assert turn.payload["kb_name"] == "Тестовая база"
    assert msgs == _messages()


async def test_rag_merges_block(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _ready_kb(monkeypatch)
    chat_id = await _chat(user_id, kb_id, top_k=3)
    msgs = _messages()
    turn = await _run(chat_id, msgs)
    content = msgs[-1]["content"]
    assert content.startswith(BLOCK_OPEN)
    assert content.endswith(f"Вопрос: {QUESTION}")
    assert turn.mode == "rag"
    assert turn.payload["top_k"] == 3
    sources = turn.payload["sources"]
    assert [s["rank"] for s in sources] == list(range(1, len(sources) + 1))
    assert sources and all("text" not in s for s in sources)
    assert turn.payload["context_tokens"] > 0
    assert turn.payload["warning"] is None
    assert turn.sources_json
    assert turn.done_payload is turn.payload


async def test_kb_null_is_kb_deleted() -> None:
    user_id = await seed_user()
    chat_id = await _chat(user_id, None)
    msgs = _messages()
    turn = await _run(chat_id, msgs)
    assert turn.payload["warning"]["code"] == "kb_deleted"
    assert msgs == _messages()


async def test_foreign_kb_is_kb_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    _, kb_id = await _ready_kb(monkeypatch)
    other = await seed_user("other")
    chat_id = await _chat(other, kb_id)
    msgs = _messages()
    turn = await _run(chat_id, msgs)
    assert turn.payload["warning"]["code"] == "kb_deleted"
    assert turn.payload["sources"] == []
    assert msgs == _messages()


async def test_rag_failure_becomes_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _ready_kb(monkeypatch)
    chat_id = await _chat(user_id, kb_id)

    async def failing(*args: object) -> list[dict[str, Any]]:
        raise RagFailure("embedder_unavailable", "нет модели")

    monkeypatch.setattr(rag_turn, "retrieve", failing)
    msgs = _messages()
    turn = await _run(chat_id, msgs)
    assert turn.payload["warning"] == {"code": "embedder_unavailable", "text": "нет модели"}
    assert msgs == _messages()


async def test_unexpected_error_becomes_retrieval_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, kb_id = await _ready_kb(monkeypatch)
    chat_id = await _chat(user_id, kb_id)

    async def failing(*args: object) -> list[dict[str, Any]]:
        raise RuntimeError("boom")

    monkeypatch.setattr(rag_turn, "retrieve", failing)
    turn = await _run(chat_id, _messages())
    assert turn.payload["warning"]["code"] == "retrieval_failed"


async def test_cancellation_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _ready_kb(monkeypatch)
    chat_id = await _chat(user_id, kb_id)

    async def cancelled(*args: object) -> list[dict[str, Any]]:
        raise asyncio.CancelledError

    monkeypatch.setattr(rag_turn, "retrieve", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await _run(chat_id, _messages())


async def test_empty_result_is_rag_without_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _ready_kb(monkeypatch)
    chat_id = await _chat(user_id, kb_id)

    async def empty(*args: object) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(rag_turn, "retrieve", empty)
    msgs = _messages()
    turn = await _run(chat_id, msgs)
    assert turn.mode == "rag"
    assert turn.payload["sources"] == []
    assert turn.payload["warning"] is None
    assert msgs == _messages()


async def test_zero_budget_is_context_full(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _ready_kb(monkeypatch)
    chat_id = await _chat(user_id, kb_id)
    msgs = _messages()
    turn = await _run(chat_id, msgs, ctx=100, max_tokens=100)
    assert turn.payload["warning"]["code"] == "context_full"
    assert msgs == _messages()


async def test_partial_fit_reports_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _ready_kb(monkeypatch)
    chat_id = await _chat(user_id, kb_id, top_k=10)
    msgs = _messages()
    turn = await _run(chat_id, msgs, ctx=1500, max_tokens=256)
    assert turn.payload["dropped"] > 0
    assert turn.payload["warning"] is None
    assert len(turn.payload["sources"]) + turn.payload["dropped"] == 10
    assert turn.payload["context_tokens"] <= int(1500 * 0.30)
