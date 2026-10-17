"""Tests for the /support command building blocks: grammar, notices, tools, history and turn."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent import kb_search, mcp_config
from agent.kb_indexer import run_index_job
from agent.llm_client import count_tokens
from agent.rag import BLOCK_OPEN
from agent.rag_turn import RagTurn
from agent.support_command import (
    DEFAULT_SUPPORT_THRESHOLD,
    SUPPORT_BASE_NOT_READY_TEXT,
    SUPPORT_HISTORY_ANSWER_CHARS,
    SUPPORT_HISTORY_MAX_TOKENS,
    SUPPORT_MCP_WARNING_TEXT,
    SUPPORT_NO_BASE_TEXT,
    SUPPORT_NO_FRAGMENTS_NOTE,
    SUPPORT_SYSTEM_PROMPT,
    SUPPORT_USAGE_TEXT,
    SupportCommand,
    build_support_messages,
    build_support_tools,
    is_support_command,
    load_support_config,
    load_support_history,
    parse_support,
    prepare_support_turn,
    resolve_support_notice,
    support_label,
    support_payload_extra,
)
from kb_helpers import install_fake_embedder, seed_kb, seed_user, vector_for
from shared.database import async_session_factory
from shared.models import Chat, HelpConfig, KbStatus, KnowledgeBase, Message, SupportConfig
from tests.conftest import _create_user

FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")


async def _add_server(user_id: int, name: str, enabled: bool = True) -> int:
    async with async_session_factory() as session:
        row = await mcp_config.create_server(
            session, user_id, name, sys.executable, [FIXTURE, "tools_extra"], {}, None, enabled
        )
        return row.id


async def _mark_base(user_id: int, status: KbStatus) -> None:
    kb_id = await seed_kb(user_id, status=status)
    async with async_session_factory() as session:
        session.add(HelpConfig(user_id=user_id, kb_id=kb_id))
        await session.commit()


async def _notice(user_id: int | None, question: str = "почему?") -> dict | None:
    async with async_session_factory() as session:
        return await resolve_support_notice(session, user_id, question)


async def _tools(user_id: int, config: SupportConfig):
    async with async_session_factory() as session:
        return await build_support_tools(session, user_id, config)


def test_parse_plain_question() -> None:
    assert parse_support("/support Почему?") == SupportCommand("Почему?", None, None)


@pytest.mark.parametrize("token", ["#T-1042", "#t-1042", "#T1042", "#1042"])
def test_parse_ticket_forms(token: str) -> None:
    assert parse_support(f"/support {token} Почему?") == SupportCommand("Почему?", "T-1042", None)


def test_parse_user() -> None:
    assert parse_support("/support @olga Почему?") == SupportCommand("Почему?", None, "olga")


@pytest.mark.parametrize("text", ["#T-12x Почему?", "@a@b x", "#1234567 q", "@-x q"])
def test_parse_bad_identifier_stays_in_question(text: str) -> None:
    cmd = parse_support(f"/support {text}")
    assert cmd is not None
    assert cmd.ticket is None and cmd.user is None
    assert cmd.question == text


def test_parse_identifier_only_after_command() -> None:
    cmd = parse_support("/support Почему #T-1001?")
    assert cmd == SupportCommand("Почему #T-1001?", None, None)


def test_parse_case_insensitive() -> None:
    assert parse_support("/SUPPORT  q") == SupportCommand("q", None, None)


@pytest.mark.parametrize(
    "text", ["/supportx q", "/ support q", "support q", "скажи /support q", ""]
)
def test_parse_rejects_other_text(text: str) -> None:
    assert parse_support(text) is None


def test_parse_empty_question() -> None:
    assert parse_support("/support") == SupportCommand("", None, None)
    assert parse_support("/support #T-1001") == SupportCommand("", "T-1001", None)


def test_is_support_command() -> None:
    assert is_support_command({"content": "/support q"}) is True
    assert is_support_command({"content": "/help q"}) is False
    assert is_support_command({"content": 5}) is False
    assert is_support_command("/support q") is False
    assert is_support_command(None) is False


def test_support_label() -> None:
    assert support_label(SupportCommand("q", "T-1042")) == "Поддержка · T-1042"
    assert support_label(SupportCommand("q", None, "olga")) == "Поддержка · @olga"
    assert support_label(SupportCommand("q")) == "Поддержка"


def test_support_config_default_threshold_matches_constant() -> None:
    assert SupportConfig(user_id=1).threshold == DEFAULT_SUPPORT_THRESHOLD


async def test_notice_usage_for_empty_question() -> None:
    user_id = await _create_user("s1", "pw")
    frame = await _notice(user_id, "")
    assert frame == {
        "type": "help_notice", "kind": "usage", "text": SUPPORT_USAGE_TEXT,
        "stored": False, "command": "support",
    }


async def test_notice_no_base() -> None:
    user_id = await _create_user("s2", "pw")
    frame = await _notice(user_id)
    assert frame is not None
    assert frame["kind"] == "no_base" and frame["text"] == SUPPORT_NO_BASE_TEXT
    assert frame["command"] == "support" and frame["stored"] is False


async def test_notice_base_not_ready() -> None:
    user_id = await _create_user("s3", "pw")
    await _mark_base(user_id, KbStatus.INDEXING)
    frame = await _notice(user_id)
    assert frame is not None
    assert frame["kind"] == "no_base" and frame["text"] == SUPPORT_BASE_NOT_READY_TEXT


async def test_notice_none_for_ready_base() -> None:
    user_id = await _create_user("s4", "pw")
    await _mark_base(user_id, KbStatus.READY)
    assert await _notice(user_id) is None


async def test_load_support_config_defaults_not_added() -> None:
    user_id = await _create_user("s5", "pw")
    async with async_session_factory() as session:
        config = await load_support_config(session, user_id)
        assert not session.new and config not in session
        assert config.threshold == DEFAULT_SUPPORT_THRESHOLD
        assert not (config.tools_memory or config.tools_tasks or config.tools_scheduler)
        assert config.mcp_servers_json is None
        await session.commit()
        assert await session.get(SupportConfig, user_id) is None


async def test_default_tools_only_support_server() -> None:
    user_id = await _create_user("s6", "pw")
    await _add_server(user_id, "support")
    await _add_server(user_id, "local-git")
    tools = await _tools(user_id, SupportConfig(user_id=user_id))
    names = [schema["function"]["name"] for schema in tools.schemas]
    assert names and all(name.startswith("mcp__support__") for name in names)
    assert not any("local-git" in name for name in names)
    assert tools.warnings == []
    assert tools.allowed_tools == frozenset()


async def test_default_tools_without_support_server_warns() -> None:
    user_id = await _create_user("s7", "pw")
    await _add_server(user_id, "local-git")
    tools = await _tools(user_id, SupportConfig(user_id=user_id))
    assert tools.schemas == []
    assert tools.warnings == [
        {"server": "support", "text": SUPPORT_MCP_WARNING_TEXT.format(name="support")}
    ]


NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
CTX = 8192
DISTINCT = {"doc.txt": " ".join(f"Раздел номер {i} описывает тему {i * 7919}." for i in range(80))}
QUESTION = "О чём раздел номер 3?"
CONTEXT_BLOCK = "<support_data>X</support_data>"


async def _chain(user_id: int, items: list[tuple[str, str, dict | None]]) -> int:
    """Store a linear branch of (role, content, payload) rows; return the last id."""
    async with async_session_factory() as session:
        chat = Chat(title="t", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        parent: int | None = None
        for role, content, payload in items:
            message = Message(
                chat_id=chat.id, parent_id=parent, role=role, content=content,
                rag_sources=json.dumps(payload) if payload is not None else None,
            )
            session.add(message)
            await session.commit()
            await session.refresh(message)
            parent = message.id
        return parent


async def _history(parent_id: int | None) -> list[tuple[str, str]]:
    async with async_session_factory() as session:
        return await load_support_history(session, parent_id)


async def test_history_keeps_only_support_pairs() -> None:
    user_id = await _create_user("s10", "pw")
    last = await _chain(user_id, [
        ("user", "обычный вопрос", None),
        ("assistant", "обычный ответ", None),
        ("user", "/support #T-1001 q1", None),
        ("assistant", "a1", {"support": {"label": "x"}}),
        ("user", "/help q", None),
        ("assistant", "ah", {"help": {"kb_name": "k"}}),
        ("user", "/support q2", None),
        ("assistant", "a2", {"support": {}}),
    ])
    assert await _history(last) == [("Тикет T-1001. q1", "a1"), ("q2", "a2")]


async def test_history_skips_pair_without_support_payload() -> None:
    user_id = await _create_user("s11", "pw")
    last = await _chain(user_id, [
        ("user", "/support q1", None),
        ("assistant", "failed", None),
        ("user", "/support @olga q2", None),
        ("assistant", "a2", {"support": {}}),
    ])
    assert await _history(last) == [("Пользователь @olga. q2", "a2")]


async def test_history_limits_pairs_and_answer_length() -> None:
    user_id = await _create_user("s12", "pw")
    items: list[tuple[str, str, dict | None]] = []
    for i in range(5):
        answer = "я" * 3000 if i == 4 else f"a{i}"
        items += [("user", f"/support q{i}", None), ("assistant", answer, {"support": {}})]
    pairs = await _history(await _chain(user_id, items))
    assert [u for u, _ in pairs] == ["q2", "q3", "q4"]
    assert len(pairs[-1][1]) == SUPPORT_HISTORY_ANSWER_CHARS


async def test_history_drops_oldest_over_token_budget() -> None:
    user_id = await _create_user("s13", "pw")
    big = " ".join(str(i * 7) for i in range(400))
    items: list[tuple[str, str, dict | None]] = []
    for i in range(3):
        items += [("user", f"/support q{i}", None), ("assistant", big, {"support": {}})]
    pairs = await _history(await _chain(user_id, items))
    total = sum(count_tokens(u) + count_tokens(a) for u, a in pairs)
    assert 0 < len(pairs) < 3 and total <= SUPPORT_HISTORY_MAX_TOKENS
    assert pairs[-1][0] == "q2"


async def test_history_without_parent_is_empty() -> None:
    assert await _history(None) == []


def test_build_support_messages_roles_and_no_profile() -> None:
    msgs = build_support_messages("q", False, NOW, [("u", "a")])
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    system = msgs[0]["content"]
    assert system.startswith(SUPPORT_SYSTEM_PROMPT)
    assert "профил" not in system.lower() and "инвариант" not in system.lower()
    assert msgs[-1]["content"] == "q"


def test_support_payload_extra() -> None:
    extra = support_payload_extra(SupportCommand("q", "T-1001"), {"ok": True, "tool": "get_ticket"})
    assert extra == {
        "label": "Поддержка · T-1001", "ticket": "T-1001", "user": None,
        "lookup": {"ok": True, "tool": "get_ticket"},
    }


async def _ready_kb(monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    install_fake_embedder(monkeypatch)
    user_id = await seed_user()
    kb_id = await seed_kb(user_id, DISTINCT, chunk_size=120, chunk_overlap=10)
    await run_index_job(kb_id, user_id)

    async def fake_query(model_id: str, query: str, *args: object) -> list[float]:
        return vector_for("Раздел номер 3 описывает тему")

    monkeypatch.setattr(kb_search, "embed_query", fake_query)
    return user_id, kb_id


def _msgs() -> list[dict]:
    return [
        {"role": "system", "content": "sys", "token_count": 1},
        {"role": "user", "content": QUESTION, "token_count": count_tokens(QUESTION)},
    ]


async def _prepare(user_id: int, kb_id: int, msgs: list[dict], threshold: float) -> RagTurn:
    config = SupportConfig(user_id=user_id, top_k=3, threshold=threshold)
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        return await prepare_support_turn(
            session, None, kb, config, QUESTION, msgs, CTX, 512, 0,
            client=None, model=None, warnings=[],
            context_block=CONTEXT_BLOCK,
            retrieval_query=f"{QUESTION} extra",
            payload_extra=support_payload_extra(SupportCommand(QUESTION, "T-1001"), None),
        )


async def test_prepare_with_fragments_and_context(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id, kb_id = await _ready_kb(monkeypatch)
    msgs = _msgs()
    turn = await _prepare(user_id, kb_id, msgs, 0.0)
    content = msgs[-1]["content"]
    assert turn.payload["support"]["label"] == "Поддержка · T-1001"
    assert turn.payload["support"]["kb_name"] == "Тестовая база"
    assert turn.payload["sources"]
    assert content.index(BLOCK_OPEN) < content.index(CONTEXT_BLOCK)
    assert content.index(CONTEXT_BLOCK) < content.index(f"Вопрос: {QUESTION}")
    assert content.count(f"Вопрос: {QUESTION}") == 1


async def test_prepare_below_threshold_calls_model_with_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, kb_id = await _ready_kb(monkeypatch)
    msgs = _msgs()
    turn = await _prepare(user_id, kb_id, msgs, 1.0)
    content = msgs[-1]["content"]
    assert content.startswith(SUPPORT_NO_FRAGMENTS_NOTE)
    assert CONTEXT_BLOCK in content
    assert turn.payload["sources"] == []
    assert turn.reply_text is None
