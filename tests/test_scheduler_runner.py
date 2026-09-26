"""Tests for the headless scheduler runner and the dispatcher tool allowlist."""

import json
from collections.abc import Callable, Coroutine, Iterator
from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlmodel import select

from agent import headless
from agent import state as agent_state
from agent import ws as agent_ws
from agent.headless import (
    HEADLESS_PREFACE,
    HEADLESS_TOOL_ALLOWLIST,
    MCP_UNAVAILABLE_NOTE,
    HeadlessRunError,
    run_headless_turn,
)
from agent.mcp_tools import McpToolset
from agent.tools import dispatch_tool_calls
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Chat, LongTermMemory, McpServerConfig, Message, Settings, Task
from tests.test_memory_ws import _plain_content_response, _tool_calls_response
from tests.test_tool_rounds_ws import _stream_queue

ALLOWLIST = frozenset({"save_long_term_memory"})
MODEL = "test-model"
LLM_URL = f"{settings.LM_STUDIO_BASE_URL}/v1/chat/completions"
FORBIDDEN_TOOLS = {
    "create_task",
    "transition_task",
    "pause_task",
    "resume_task",
    "save_working_memory",
}


async def _create_chat(user_id: int) -> int:
    async with async_session_factory() as session:
        chat = Chat(title="Allowlist chat", user_id=user_id)
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        return chat.id


def _create_task_call() -> dict:
    return {
        "id": "call_create",
        "type": "function",
        "function": {
            "name": "create_task",
            "arguments": json.dumps({"title": "T", "description": "D", "goal": "G"}),
        },
    }


async def _count_tasks() -> int:
    async with async_session_factory() as session:
        return len((await session.exec(select(Task))).all())


@pytest.mark.asyncio
async def test_allowlist_rejects_non_allowed_builtin_tool(
    authenticated_client: AsyncClient,
) -> None:
    """A create_task call outside the allowlist is an unknown tool and writes nothing."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        results = await dispatch_tool_calls(
            session, user_id, chat_id, [_create_task_call()], allowed_tools=ALLOWLIST,
        )

    assert len(results) == 1
    assert results[0]["ok"] is False
    assert json.loads(results[0]["content"]) == {"error": "unknown tool create_task"}
    assert results[0]["write"] is None
    assert await _count_tasks() == 0


@pytest.mark.asyncio
async def test_no_allowlist_keeps_dispatching_every_tool(
    authenticated_client: AsyncClient,
) -> None:
    """With allowed_tools=None the same call is dispatched as before."""
    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)

    async with async_session_factory() as session:
        results = await dispatch_tool_calls(
            session, user_id, chat_id, [_create_task_call()], allowed_tools=None,
        )

    assert results[0]["ok"] is True
    assert await _count_tasks() == 1


@pytest.mark.asyncio
async def test_allowlist_does_not_restrict_mcp_bindings(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP-bound names are routed to MCP first, even when an allowlist is set."""
    from agent import tools
    from agent.mcp_tools import McpToolBinding

    user_id = authenticated_client.seeded_user_id
    chat_id = await _create_chat(user_id)
    binding = McpToolBinding(
        exposed_name="mcp_files_read",
        user_id=user_id,
        server_id=1,
        server_name="files",
        tool_name="read",
    )
    seen: list[str] = []

    async def fake_dispatch_mcp(bound: McpToolBinding, call_id: str, raw: str) -> dict:
        seen.append(bound.exposed_name)
        return {
            "tool_call_id": call_id,
            "name": bound.exposed_name,
            "ok": True,
            "content": "{}",
            "write": None,
            "arguments": raw,
            "mcp": {"server_name": bound.server_name, "tool": bound.tool_name},
        }

    monkeypatch.setattr(tools, "_dispatch_mcp_call", fake_dispatch_mcp)
    call = {
        "id": "call_mcp",
        "type": "function",
        "function": {"name": "mcp_files_read", "arguments": "{}"},
    }

    async with async_session_factory() as session:
        results = await dispatch_tool_calls(
            session,
            user_id,
            chat_id,
            [call],
            mcp_bindings={"mcp_files_read": binding},
            allowed_tools=ALLOWLIST,
        )

    assert seen == ["mcp_files_read"]
    assert results[0]["ok"] is True


@pytest.fixture
def llm_route() -> Iterator[respx.Route]:
    """Mocked LM Studio chat-completions route; each test sets its own side effect."""
    with respx.mock(assert_all_called=False) as router:
        yield router.post(LLM_URL)


@pytest.fixture
async def owner_id(seed_user: Callable[[str, str], Coroutine[Any, Any, Any]]) -> int:
    """Id of the user that owns the headless runs."""
    user = await seed_user("jobowner", "jobpass")
    return user.id


def _empty_response() -> httpx.Response:
    """SSE response with no content and no tool calls."""
    body = 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\ndata: [DONE]\n'
    return httpx.Response(200, content=body.encode())


def _bodies(route: respx.Route) -> list[dict[str, Any]]:
    """Return the JSON body of every request the route received."""
    return [json.loads(call.request.content) for call in route.calls]


def _save_call(call_id: str, key: str, content: str) -> tuple[str, str, str]:
    """Build one save_long_term_memory tool call tuple."""
    return (call_id, "save_long_term_memory", json.dumps({"key": key, "content": content}))


async def _run(user_id: int, prompt: str = "сделай отчёт") -> headless.HeadlessResult:
    async with async_session_factory() as session:
        return await run_headless_turn(session, user_id, prompt, MODEL)


async def _seed_global_settings(user_id: int, temperature: float, max_tokens: int) -> None:
    async with async_session_factory() as session:
        session.add(
            Settings(chat_id=None, user_id=user_id, temperature=temperature, max_tokens=max_tokens),
        )
        await session.commit()


async def _seed_mcp_server(user_id: int, enabled: bool) -> None:
    async with async_session_factory() as session:
        session.add(McpServerConfig(user_id=user_id, name="files", command="noop", enabled=enabled))
        await session.commit()


async def _rows(model: type) -> list[Any]:
    async with async_session_factory() as session:
        return list((await session.exec(select(model))).all())


async def _no_mcp_tools(*_args: Any, **_kwargs: Any) -> McpToolset:
    return McpToolset.empty()


async def test_plain_answer_needs_no_chat_or_lock(llm_route: respx.Route, owner_id: int) -> None:
    """A content-only answer is returned with no trace, no chat rows and no chat lock."""
    llm_route.mock(side_effect=_stream_queue([_plain_content_response("Готово: 42")]))

    result = await _run(owner_id)

    assert result.text == "Готово: 42"
    assert result.results == []
    assert result.tool_trace is None
    assert await _rows(Chat) == []
    assert await _rows(Message) == []
    assert agent_state.chat_locks == {}


async def test_tool_round_saves_long_term_memory_for_owner(
    llm_route: respx.Route, owner_id: int,
) -> None:
    """A model-issued save_long_term_memory call stores a row for the job owner."""
    llm_route.mock(
        side_effect=_stream_queue(
            [
                _tool_calls_response([_save_call("c1", "user_name", "Alex")]),
                _plain_content_response("Сохранено: имя"),
            ],
        ),
    )

    result = await _run(owner_id)

    rows = await _rows(LongTermMemory)
    assert [(r.user_id, r.key, r.value) for r in rows] == [(owner_id, "user_name", "Alex")]
    assert result.text == "Сохранено: имя"
    assert "save_long_term_memory" in (result.tool_trace or "")
    assert [r["name"] for r in result.results] == ["save_long_term_memory"]


async def test_only_allowlisted_and_mcp_tools_are_offered(
    llm_route: respx.Route, owner_id: int,
) -> None:
    """The first request offers save_long_term_memory and none of the chat-bound built-ins."""
    llm_route.mock(side_effect=_stream_queue([_plain_content_response("ok")]))

    await _run(owner_id)

    offered = {tool["function"]["name"] for tool in _bodies(llm_route)[0]["tools"]}
    assert "save_long_term_memory" in offered
    assert offered.isdisjoint(FORBIDDEN_TOOLS)


async def test_hallucinated_builtin_tool_is_rejected(
    llm_route: respx.Route, owner_id: int,
) -> None:
    """A create_task call is reported as an unknown tool, writes nothing, and the run continues."""
    call = ("c1", "create_task", json.dumps({"title": "T", "description": "D", "goal": "G"}))
    llm_route.mock(
        side_effect=_stream_queue(
            [_tool_calls_response([call]), _plain_content_response("Итог: задача не нужна")],
        ),
    )

    result = await _run(owner_id)

    assert result.results[0]["ok"] is False
    assert json.loads(result.results[0]["content"]) == {"error": "unknown tool create_task"}
    assert result.text == "Итог: задача не нужна"
    assert await _rows(Task) == []


async def test_lm_studio_unreachable_maps_to_russian_message(
    llm_route: respx.Route, owner_id: int,
) -> None:
    """A refused connection means LM Studio is not running."""
    llm_route.mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(HeadlessRunError) as excinfo:
        await _run(owner_id)

    assert excinfo.value.message == "Модель недоступна: LM Studio не запущен"


async def test_http_error_maps_to_status_message(llm_route: respx.Route, owner_id: int) -> None:
    """A non-2xx response reports the status code."""
    llm_route.mock(return_value=httpx.Response(404))

    with pytest.raises(HeadlessRunError) as excinfo:
        await _run(owner_id)

    assert excinfo.value.message == "Модель недоступна: HTTP 404"


async def test_read_timeout_maps_to_timeout_message(llm_route: respx.Route, owner_id: int) -> None:
    """An LLM read timeout is reported as such."""
    llm_route.mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(HeadlessRunError) as excinfo:
        await _run(owner_id)

    assert excinfo.value.message == "Тайм-аут ответа модели"


async def test_failure_in_later_round_is_mapped_too(
    llm_route: respx.Route, owner_id: int,
) -> None:
    """A transport error after a tool round is mapped, not leaked as a raw exception."""

    requests_seen: list[httpx.Request] = []

    def side_effect(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if len(requests_seen) == 1:
            return _tool_calls_response([_save_call("c1", "k", "v")])
        raise httpx.ConnectError("gone")

    llm_route.mock(side_effect=side_effect)

    with pytest.raises(HeadlessRunError) as excinfo:
        await _run(owner_id)

    assert excinfo.value.message == "Модель недоступна: LM Studio не запущен"


async def test_empty_answer_without_tool_results_fails(
    llm_route: respx.Route, owner_id: int,
) -> None:
    """No content and no tool calls, even after the empty-retry, is an error."""
    llm_route.mock(side_effect=_stream_queue([_empty_response(), _empty_response()]))

    with pytest.raises(HeadlessRunError) as excinfo:
        await _run(owner_id)

    assert excinfo.value.message == "Модель вернула пустой ответ"
    assert llm_route.call_count == 2


async def test_round_cap_bounds_dispatched_rounds(
    llm_route: respx.Route, owner_id: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With MAX_TOOL_ROUNDS=2 the run stops after two rounds and one tools-less answer."""
    monkeypatch.setattr("agent.ws.MAX_TOOL_ROUNDS", 2)
    llm_route.mock(
        side_effect=_stream_queue(
            [
                _tool_calls_response([_save_call("c1", "k1", "v1")]),
                _tool_calls_response([_save_call("c2", "k2", "v2")]),
                _plain_content_response("Финал"),
            ],
        ),
    )

    result = await _run(owner_id)

    assert len(result.results) == 2
    assert llm_route.call_count == 3
    assert "tools" not in _bodies(llm_route)[2]
    assert result.text == "Финал"


async def test_sampling_comes_from_global_settings(
    llm_route: respx.Route, owner_id: int,
) -> None:
    """Temperature and max_tokens equal the owner's global settings row."""
    await _seed_global_settings(owner_id, temperature=0.2, max_tokens=777)
    llm_route.mock(side_effect=_stream_queue([_plain_content_response("ok")]))

    await _run(owner_id)

    body = _bodies(llm_route)[0]
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 777


async def test_missing_settings_row_uses_defaults_without_creating_it(
    llm_route: respx.Route, owner_id: int,
) -> None:
    """Without a global row the Settings defaults apply and no row is created."""
    llm_route.mock(side_effect=_stream_queue([_plain_content_response("ok")]))

    await _run(owner_id)

    defaults = Settings()
    body = _bodies(llm_route)[0]
    assert body["temperature"] == defaults.temperature
    assert body["max_tokens"] == defaults.max_tokens
    assert [row for row in await _rows(Settings) if row.user_id == owner_id] == []


async def test_system_message_has_preface_clock_and_long_term_memory(
    llm_route: respx.Route, owner_id: int,
) -> None:
    """The system prompt carries the headless preface, the clock line and saved memory."""
    async with async_session_factory() as session:
        session.add(LongTermMemory(user_id=owner_id, key="user_name", value="Alex"))
        await session.commit()
    llm_route.mock(side_effect=_stream_queue([_plain_content_response("ok")]))

    await _run(owner_id, prompt="сделай отчёт")

    messages = _bodies(llm_route)[0]["messages"]
    system = messages[0]
    assert system["role"] == "system"
    assert HEADLESS_PREFACE in system["content"]
    assert "Current local date and time:" in system["content"]
    assert json.dumps({"user_name": "Alex"}, ensure_ascii=False) in system["content"]
    assert messages[1] == {"role": "user", "content": "сделай отчёт"}


async def test_mcp_unavailable_note_is_appended_to_answer(
    llm_route: respx.Route, owner_id: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled MCP servers with an empty toolset add a Russian note to the answer."""
    await _seed_mcp_server(owner_id, enabled=True)
    monkeypatch.setattr("agent.headless.build_mcp_toolset", _no_mcp_tools)
    llm_route.mock(side_effect=_stream_queue([_plain_content_response("Готово: 42")]))

    result = await _run(owner_id)

    assert result.text.endswith(MCP_UNAVAILABLE_NOTE)
    assert result.text.startswith("Готово: 42")
    assert result.mcp_tool_count == 0


async def test_mcp_unavailable_with_empty_answer_names_mcp_in_error(
    llm_route: respx.Route, owner_id: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty answer while MCP tools were missing says so in the failure message."""
    await _seed_mcp_server(owner_id, enabled=True)
    monkeypatch.setattr("agent.headless.build_mcp_toolset", _no_mcp_tools)
    llm_route.mock(side_effect=_stream_queue([_empty_response(), _empty_response()]))

    with pytest.raises(HeadlessRunError) as excinfo:
        await _run(owner_id)

    assert excinfo.value.message == "Модель вернула пустой ответ (MCP-инструменты недоступны)"


@pytest.mark.parametrize("enabled_row", [None, False])
async def test_no_note_without_enabled_mcp_servers(
    llm_route: respx.Route,
    owner_id: int,
    monkeypatch: pytest.MonkeyPatch,
    enabled_row: bool | None,
) -> None:
    """No MCP servers, or only disabled ones, means no note even with an empty toolset."""
    if enabled_row is not None:
        await _seed_mcp_server(owner_id, enabled=enabled_row)
    monkeypatch.setattr("agent.headless.build_mcp_toolset", _no_mcp_tools)
    llm_route.mock(side_effect=_stream_queue([_plain_content_response("Готово: 42")]))

    result = await _run(owner_id)

    assert result.text == "Готово: 42"


async def test_mcp_toolset_failure_does_not_fail_the_run(
    llm_route: respx.Route, owner_id: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash while building the MCP toolset degrades to an empty toolset."""

    async def broken(*_args: Any, **_kwargs: Any) -> McpToolset:
        raise RuntimeError("boom")

    monkeypatch.setattr("agent.headless.build_mcp_toolset", broken)
    llm_route.mock(side_effect=_stream_queue([_plain_content_response("Готово: 42")]))

    result = await _run(owner_id)

    assert result.text == "Готово: 42"


def test_allowlist_contains_only_long_term_memory() -> None:
    """Scheduler and chat-bound tools stay outside the headless allowlist."""
    assert HEADLESS_TOOL_ALLOWLIST == frozenset({"save_long_term_memory"})


@pytest.mark.parametrize(
    "name",
    [
        "_ToolTurn",
        "_ToolRoundsResult",
        "_stream_follow_up_with_empty_retry",
        "_pick_nudge",
        "_stream_nudge",
        "_run_tool_rounds",
        "MAX_TOOL_ROUNDS",
    ],
)
def test_reused_chat_helpers_still_exist(name: str) -> None:
    """Coupling guard: the headless runner depends on these agent.ws internals."""
    assert hasattr(agent_ws, name)
