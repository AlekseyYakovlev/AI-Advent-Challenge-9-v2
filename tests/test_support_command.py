"""Tests for the /support command building blocks: grammar, notices, tools, history and turn."""

import sys
from pathlib import Path

import pytest

from agent import mcp_config
from agent.support_command import (
    DEFAULT_SUPPORT_THRESHOLD,
    SUPPORT_BASE_NOT_READY_TEXT,
    SUPPORT_MCP_WARNING_TEXT,
    SUPPORT_NO_BASE_TEXT,
    SUPPORT_USAGE_TEXT,
    SupportCommand,
    build_support_tools,
    is_support_command,
    load_support_config,
    parse_support,
    resolve_support_notice,
    support_label,
)
from kb_helpers import seed_kb
from shared.database import async_session_factory
from shared.models import HelpConfig, KbStatus, SupportConfig
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
