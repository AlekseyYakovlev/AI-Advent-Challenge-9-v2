"""Tests for the /help command building blocks: parsing, notices and tool selection."""

import json
import sys
from pathlib import Path

import pytest

from agent import help_command, mcp_config
from agent.help_command import (
    HELP_BASE_NOT_READY_TEXT,
    HELP_MCP_WARNING_TEXT,
    HELP_NO_BASE_TEXT,
    HELP_USAGE_TEXT,
    TOOL_GROUPS,
    build_help_tools,
    is_help_command,
    load_help_config,
    parse_help,
    resolve_help_notice,
)
from agent.mcp_tools import build_mcp_toolset
from agent.tools import TOOL_REGISTRY
from agent.ws import SCHEDULER_TOOL_NAMES, TASK_TOOL_NAMES
from kb_helpers import seed_kb
from shared.database import async_session_factory
from shared.models import HelpConfig, KbStatus, KnowledgeBase
from tests.conftest import _create_user

FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")


async def _add_server(user_id: int, name: str, enabled: bool = True) -> int:
    async with async_session_factory() as session:
        row = await mcp_config.create_server(
            session, user_id, name, sys.executable, [FIXTURE, "tools_extra"], {}, None, enabled
        )
        return row.id


async def _save_config(user_id: int, **fields: object) -> HelpConfig:
    async with async_session_factory() as session:
        row = HelpConfig(user_id=user_id, **fields)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def _tools(user_id: int, config: HelpConfig) -> help_command.HelpTools:
    async with async_session_factory() as session:
        return await build_help_tools(session, user_id, config)


async def _notice(user_id: int | None, question: str = "как запустить?") -> dict | None:
    async with async_session_factory() as session:
        return await resolve_help_notice(session, user_id, question)


def test_parse_help_extracts_question() -> None:
    """The question after /help is returned stripped, across whitespace and case."""
    assert parse_help("/help как запустить?") == "как запустить?"
    assert parse_help("  /HELP  x ") == "x"
    assert parse_help("/help\nвопрос") == "вопрос"


def test_parse_help_empty_question() -> None:
    """A bare /help yields an empty question, not None."""
    assert parse_help("/help") == ""
    assert parse_help("/help   ") == ""


@pytest.mark.parametrize("text", ["/helpme", "/ help x", "help x", "скажи /help", "/helpx y", ""])
def test_parse_help_rejects_other_text(text: str) -> None:
    """Anything that is not the whole word /help is an ordinary message."""
    assert parse_help(text) is None


def test_is_help_command() -> None:
    """Only a dict with a string content that parses as /help is a command."""
    assert is_help_command({"content": "/help x"}) is True
    assert is_help_command({"content": "обычный текст"}) is False
    assert is_help_command({"content": 5}) is False
    assert is_help_command({"text": "/help x"}) is False
    assert is_help_command("/help x") is False
    assert is_help_command(None) is False


def test_tool_groups_match_registry() -> None:
    """Groups mirror the websocket tool name sets and name only registered tools."""
    assert TOOL_GROUPS["tasks"] == TASK_TOOL_NAMES
    assert TOOL_GROUPS["scheduler"] == SCHEDULER_TOOL_NAMES
    for names in TOOL_GROUPS.values():
        assert set(names) <= set(TOOL_REGISTRY)


async def test_notice_usage_for_empty_question() -> None:
    """An empty question returns the usage frame that is not stored."""
    user_id = await _create_user("h1", "pw")
    frame = await _notice(user_id, "")
    assert frame == {
        "type": "help_notice", "kind": "usage", "text": HELP_USAGE_TEXT, "stored": False,
    }


async def test_notice_no_base_without_row() -> None:
    """With no stored settings the no-base notice is returned."""
    user_id = await _create_user("h2", "pw")
    frame = await _notice(user_id)
    assert frame is not None
    assert frame["kind"] == "no_base" and frame["text"] == HELP_NO_BASE_TEXT


async def test_notice_no_base_when_kb_not_marked() -> None:
    """A row without a marked base gives the no-base notice."""
    user_id = await _create_user("h3", "pw")
    await _save_config(user_id)
    frame = await _notice(user_id)
    assert frame is not None and frame["kind"] == "no_base"


async def test_notice_no_base_for_foreign_kb() -> None:
    """A base of another user never counts as the documentation base."""
    owner = await _create_user("h4", "pw")
    other = await _create_user("h4b", "pw")
    kb_id = await seed_kb(other, status=KbStatus.READY)
    await _save_config(owner, kb_id=kb_id)
    frame = await _notice(owner)
    assert frame is not None
    assert frame["kind"] == "no_base" and frame["text"] == HELP_NO_BASE_TEXT


@pytest.mark.parametrize("status", [KbStatus.QUEUED, KbStatus.INDEXING, KbStatus.FAILED])
async def test_notice_no_base_when_kb_not_ready(status: KbStatus) -> None:
    """A marked base that is not ready names itself in the notice."""
    user_id = await _create_user(f"h5{status.value}", "pw")
    kb_id = await seed_kb(user_id, status=status)
    await _save_config(user_id, kb_id=kb_id)
    frame = await _notice(user_id)
    assert frame is not None
    assert frame["text"] == HELP_BASE_NOT_READY_TEXT.format(name="Тестовая база")


async def test_notice_none_for_ready_kb() -> None:
    """A ready marked base needs no notice."""
    user_id = await _create_user("h6", "pw")
    kb_id = await seed_kb(user_id, status=KbStatus.READY)
    await _save_config(user_id, kb_id=kb_id)
    assert await _notice(user_id) is None


async def test_notice_none_for_ready_kb_being_added_to() -> None:
    """A ready base with phase adding still counts as ready."""
    user_id = await _create_user("h7", "pw")
    kb_id = await seed_kb(user_id, status=KbStatus.READY)
    async with async_session_factory() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        kb.phase = "adding"
        session.add(kb)
        await session.commit()
    await _save_config(user_id, kb_id=kb_id)
    assert await _notice(user_id) is None


async def test_notice_no_base_without_owner() -> None:
    """A chat without an owner gets the no-base notice."""
    frame = await _notice(None)
    assert frame is not None and frame["kind"] == "no_base"


async def test_load_help_config_defaults_not_added() -> None:
    """Missing settings come back as unsaved defaults and nothing is written."""
    user_id = await _create_user("h8", "pw")
    async with async_session_factory() as session:
        config = await load_help_config(session, user_id)
        assert not session.new and config not in session
        assert config.threshold == 0.45
        assert (config.top_k, config.candidate_k) == (5, 20)
        assert not (config.lexical or config.llm_rerank or config.hybrid or config.rewrite)
        assert not (config.tools_memory or config.tools_tasks or config.tools_scheduler)
        assert config.mcp_servers_json is None
        await session.commit()
        assert await session.get(HelpConfig, user_id) is None


async def test_default_tools_without_local_git_warns() -> None:
    """With no local-git server the turn gets no tools and one warning."""
    user_id = await _create_user("h9", "pw")
    await _add_server(user_id, "Other")
    tools = await _tools(user_id, HelpConfig(user_id=user_id))
    assert tools.schemas == []
    assert tools.allowed_tools == frozenset()
    assert tools.warnings == [
        {"server": "local-git", "text": "Сервер local-git недоступен, его инструменты не использовались."}
    ]
    assert tools.warnings[0]["text"] == HELP_MCP_WARNING_TEXT.format(name="local-git")


async def test_default_tools_only_local_git() -> None:
    """By default only the local-git server contributes tools."""
    user_id = await _create_user("h10", "pw")
    await _add_server(user_id, "local-git")
    await _add_server(user_id, "Other")
    tools = await _tools(user_id, HelpConfig(user_id=user_id))
    names = [schema["function"]["name"] for schema in tools.schemas]
    assert names and all(name.startswith("mcp__local-git__") for name in names)
    assert tools.warnings == []
    assert tools.allowed_tools == frozenset()


async def test_builtin_group_enabled_only_that_group() -> None:
    """Enabling the tasks switch exposes exactly the four task tools among built-ins."""
    user_id = await _create_user("h11", "pw")
    await _add_server(user_id, "local-git")
    config = HelpConfig(user_id=user_id, tools_tasks=True)
    tools = await _tools(user_id, config)
    builtin = {
        schema["function"]["name"]
        for schema in tools.schemas
        if not schema["function"]["name"].startswith("mcp__")
    }
    assert builtin == set(TASK_TOOL_NAMES)
    assert tools.allowed_tools == frozenset(TASK_TOOL_NAMES)


async def test_stored_choice_uses_chosen_servers_and_flags_missing() -> None:
    """A stored choice replaces the default; a deleted server warns under its stored name."""
    user_id = await _create_user("h12", "pw")
    await _add_server(user_id, "local-git")
    other_id = await _add_server(user_id, "Other")
    stored = json.dumps([{"id": other_id, "name": "Other"}, {"id": 9999, "name": "Gone"}])
    tools = await _tools(user_id, HelpConfig(user_id=user_id, mcp_servers_json=stored))
    names = [schema["function"]["name"] for schema in tools.schemas]
    assert names and all(name.startswith("mcp__other__") for name in names)
    assert [w["server"] for w in tools.warnings] == ["Gone"]


async def test_disabled_server_warns_without_raising() -> None:
    """A chosen server that is disabled gives a warning and no tools."""
    user_id = await _create_user("h13", "pw")
    await _add_server(user_id, "local-git", enabled=False)
    tools = await _tools(user_id, HelpConfig(user_id=user_id))
    assert tools.schemas == []
    assert [w["server"] for w in tools.warnings] == ["local-git"]


async def test_toolset_failure_falls_back_to_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exception while building the MCP toolset never breaks the turn."""
    user_id = await _create_user("h14", "pw")
    await _add_server(user_id, "local-git")

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(help_command, "build_mcp_toolset", boom)
    tools = await _tools(user_id, HelpConfig(user_id=user_id))
    assert tools.schemas == []
    assert [w["server"] for w in tools.warnings] == ["local-git"]


async def test_build_mcp_toolset_only_server_ids_filters() -> None:
    """An empty id set yields no tools; omitting the keyword keeps every server."""
    user_id = await _create_user("h15", "pw")
    await _add_server(user_id, "Fixture")
    async with async_session_factory() as session:
        none = await build_mcp_toolset(session, user_id, set(TOOL_REGISTRY), only_server_ids=set())
        every = await build_mcp_toolset(session, user_id, set(TOOL_REGISTRY))
    assert none.schemas == []
    assert every.schemas
