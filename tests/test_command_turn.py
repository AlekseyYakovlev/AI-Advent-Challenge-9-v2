"""Tests for the generic command-turn mechanism, driven by a non-help spec."""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from agent import kb_search, mcp_config
from agent.command_turn import (
    CommandSpec,
    build_command_messages,
    notice_frame,
    prepare_command_turn,
    resolve_command_notice,
    select_tools,
)
from agent.kb_indexer import run_index_job
from agent.llm_client import count_tokens
from agent.tool_guard import build_clock_line
from kb_helpers import get_kb, install_fake_embedder, seed_kb, seed_user, vector_for
from shared.database import async_session_factory
from shared.models import HelpConfig, KbStatus, KnowledgeBase

_SPEC = CommandSpec(
    name="demo",
    payload_key="demo",
    default_server_name="demo-srv",
    usage_text="usage!",
    no_base_text="no base!",
    base_not_ready_text="not ready {name}",
    mcp_warning_text="srv {name} down",
    system_prompt="SYS",
    fragments_instruction="FRAG",
    no_fragments_note="NONE",
    notice_command="demo",
)

DISTINCT = {"doc.txt": " ".join(f"Раздел номер {i} описывает тему {i * 7919}." for i in range(80))}
QUESTION = "О чём раздел номер 3?"
CTX = 8192
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")


async def _ready_kb(monkeypatch: pytest.MonkeyPatch, seen: list[str] | None = None) -> tuple[int, int]:
    install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    kb_id = await seed_kb(user_id, DISTINCT, chunk_size=120, chunk_overlap=10)
    await run_index_job(kb_id, user_id)
    assert (await get_kb(kb_id)).status == KbStatus.READY

    async def fake_query(model_id: str, query: str, *args: object) -> list[float]:
        if seen is not None:
            seen.append(query)
        return vector_for("Раздел номер 3 описывает тему")

    monkeypatch.setattr(kb_search, "embed_query", fake_query)
    return user_id, kb_id


def _messages() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "sys", "token_count": 1},
        {"role": "user", "content": QUESTION, "token_count": count_tokens(QUESTION)},
    ]


async def _prepare(
    user_id: int, kb_id: int, msgs: list[dict[str, Any]], threshold: float, **kwargs: Any
) -> Any:
    config = HelpConfig(user_id=user_id, kb_id=kb_id, top_k=3, threshold=threshold)
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        return await prepare_command_turn(
            _SPEC, session, None, kb, config, QUESTION, msgs, CTX, 512, 0,
            client=None, model=None, warnings=[], **kwargs,
        )


def test_notice_frame_command_key() -> None:
    assert notice_frame("usage", "x") == {
        "type": "help_notice", "kind": "usage", "text": "x", "stored": False,
    }
    assert notice_frame("usage", "x", "demo")["command"] == "demo"


async def test_resolve_notice_usage_and_no_base() -> None:
    user_id = await seed_user()
    async with async_session_factory() as session:
        usage = await resolve_command_notice(session, user_id, "", _SPEC)
        no_base = await resolve_command_notice(session, user_id, "q", _SPEC)
    assert usage is not None and usage["kind"] == "usage" and usage["command"] == "demo"
    assert no_base is not None and no_base["kind"] == "no_base" and no_base["text"] == "no base!"


async def test_select_tools_warns_when_default_server_missing() -> None:
    user_id = await seed_user()
    async def never(*args: object, **kwargs: object) -> Any:
        raise AssertionError("no server is chosen")

    async with async_session_factory() as session:
        tools = await select_tools(
            session, user_id, HelpConfig(user_id=user_id), _SPEC, toolset_builder=never
        )
    assert tools.schemas == []
    assert tools.warnings == [{"server": "demo-srv", "text": "srv demo-srv down"}]


async def test_select_tools_builder_failure_becomes_warning() -> None:
    user_id = await seed_user()
    async with async_session_factory() as session:
        await mcp_config.create_server(
            session, user_id, "demo-srv", sys.executable, [FIXTURE, "tools_extra"], {}, None, True
        )

    async def boom(*args: object, **kwargs: object) -> Any:
        raise RuntimeError("down")

    async with async_session_factory() as session:
        tools = await select_tools(
            session, user_id, HelpConfig(user_id=user_id), _SPEC, toolset_builder=boom
        )
    assert tools.schemas == []
    assert tools.warnings == [{"server": "demo-srv", "text": "srv demo-srv down"}]


def test_build_command_messages_with_history() -> None:
    msgs = build_command_messages(_SPEC, "q", False, NOW, [("u1", "a1")])
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert [m["content"] for m in msgs] == [f"SYS\n\n{build_clock_line(NOW)}", "u1", "a1", "q"]
    assert all(m["token_count"] == count_tokens(m["content"]) for m in msgs)


async def test_prepare_context_block_after_rag_block(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _ready_kb(monkeypatch)
    msgs = _messages()
    turn = await _prepare(
        user_id, kb_id, msgs, 0.0, context_block="CTX", payload_extra={"ticket": 7}
    )
    content = msgs[-1]["content"]
    assert content.index("FRAG") < content.index("CTX") < content.index(f"Вопрос: {QUESTION}")
    assert content.count("Вопрос: ") == 1
    assert turn.payload["demo"]["ticket"] == 7
    assert turn.payload["demo"]["kb_name"] == "Тестовая база"


async def test_prepare_context_block_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _ready_kb(monkeypatch)
    msgs = _messages()
    await _prepare(user_id, kb_id, msgs, 1.0, context_block="CTX")
    content = msgs[-1]["content"]
    assert content.startswith("NONE\n\nCTX")
    assert content.count(f"Вопрос: {QUESTION}") == 1


async def test_prepare_uses_retrieval_query(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    user_id, kb_id = await _ready_kb(monkeypatch, seen)
    await _prepare(user_id, kb_id, _messages(), 0.0, retrieval_query="other text")
    assert "other text" in seen
    assert QUESTION not in seen
