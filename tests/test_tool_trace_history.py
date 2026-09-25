"""Tool trace persistence on assistant messages and structured replay in later-turn context."""

import json
from typing import Any

import pytest
import respx
from sqlalchemy import text
from starlette.testclient import TestClient

from agent.context_engine import (
    RECENT_MESSAGE_COUNT,
    TOOL_TRACE_ARGS_CHARS,
    TOOL_TRACE_RESULT_CHARS,
    _load_branch_messages,
    _message_tokens,
    build_llm_context,
    compute_chat_stats,
    serialize_tool_trace,
)
from agent.llm_client import llm_client
from agent.main import app
from agent.tool_guard import TOOL_TRACE_HEADER
from shared.database import async_session_factory, engine, init_db
from shared.models import Chat, ContextStrategy, Message, Settings
from tests.conftest import login_test_client
from tests.test_memory_ws import (
    BASE_URL,
    WS_ORIGIN,
    _get_message,
    _plain_content_response,
    _queue_responses,
    _send_and_drain,
    _tool_calls_response,
)

MODEL = "test-model"


def _stream_bodies(route: respx.Route) -> list[dict]:
    """Return the JSON bodies of streaming chat requests only."""
    bodies = [json.loads(call.request.content) for call in route.calls]
    return [body for body in bodies if body.get("stream") is True]


def _history_only(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip the system prompt from build_llm_context output."""
    return [msg for msg in messages if msg["role"] != "system"]


async def _make_chain(
    session: Any,
    specs: list[tuple[str, str, str | None]],
) -> tuple[Chat, list[Message]]:
    """Insert a linear chain of (role, content, tool_trace) messages and point the leaf at it."""
    chat = Chat(title="chain")
    session.add(chat)
    await session.commit()
    await session.refresh(chat)
    rows: list[Message] = []
    parent_id: int | None = None
    for role, content, trace in specs:
        row = Message(
            chat_id=chat.id,
            parent_id=parent_id,
            role=role,
            content=content,
            token_count=llm_client.count_tokens(content),
            tool_trace=trace,
        )
        session.add(row)
        await session.flush()
        parent_id = row.id
        rows.append(row)
    chat.current_leaf_message_id = parent_id
    session.add(chat)
    await session.commit()
    return chat, rows


async def _set_strategy(
    session: Any,
    chat_id: int,
    strategy: ContextStrategy,
    context_length: int,
) -> None:
    """Insert a per-chat settings row with the given strategy and context length."""
    session.add(
        Settings(chat_id=chat_id, strategy=strategy.value, context_length=context_length),
    )
    await session.commit()


def _trace(*names: str) -> str:
    """Serialize a trace with one successful call per name."""
    trace = serialize_tool_trace(
        [
            {"name": name, "arguments": json.dumps({"n": name}), "ok": True, "content": f"{name} ok"}
            for name in names
        ],
    )
    assert trace is not None
    return trace


def test_serialize_empty_results_returns_none() -> None:
    """No tool results means no trace."""
    assert serialize_tool_trace([]) is None


def test_serialize_drops_over_limit_arguments_and_cuts_result() -> None:
    """Over-limit arguments are stored as {} while the result is still cut with an ellipsis."""
    results = [
        {
            "name": "mcp__fs__write_file",
            "arguments": json.dumps({"content": "a" * (TOOL_TRACE_ARGS_CHARS + 10)}),
            "ok": True,
            "content": "ignored",
            "result_text": "r" * 1000,
        },
    ]
    trace = serialize_tool_trace(results)
    assert trace is not None
    entry = json.loads(trace)[0]
    assert entry["arguments"] == "{}"
    assert entry["result"] == "r" * TOOL_TRACE_RESULT_CHARS + "…"
    assert entry["name"] == "mcp__fs__write_file"
    assert entry["ok"] is True


def test_serialize_keeps_valid_arguments_and_neutralizes_bad_ones() -> None:
    """Valid JSON arguments are stored byte-identical; empty or invalid ones become {}."""
    small = json.dumps({"path": "a.txt", "content": "hi"})
    trace = serialize_tool_trace(
        [
            {"name": "a", "arguments": small, "ok": True, "content": "r"},
            {"name": "b", "arguments": "", "ok": True, "content": "r"},
            {"name": "c", "arguments": "{bad", "ok": True, "content": "r"},
        ],
    )
    assert trace is not None
    arguments = [entry["arguments"] for entry in json.loads(trace)]
    assert arguments == [small, "{}", "{}"]


def test_serialize_falls_back_to_content_without_result_text() -> None:
    """Built-in tools have no result_text, so content is used."""
    trace = serialize_tool_trace(
        [{"name": "save_working_memory", "arguments": "{}", "ok": True, "content": '{"ok":1}'}],
    )
    assert trace is not None
    assert json.loads(trace)[0]["result"] == '{"ok":1}'


async def test_load_branch_messages_keeps_stored_text_and_raw_trace() -> None:
    """Traced rows keep stored content and carry id and raw trace; untraced rows carry None."""
    async with async_session_factory() as session:
        chat = Chat(title="trace")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)

        user = Message(chat_id=chat.id, role="user", content="make file", token_count=3)
        session.add(user)
        await session.flush()
        trace = serialize_tool_trace(
            [{"name": "tool_x", "arguments": "{}", "ok": True, "content": "created"}],
        )
        traced = Message(
            chat_id=chat.id,
            parent_id=user.id,
            role="assistant",
            content="Done.",
            token_count=2,
            tool_trace=trace,
        )
        session.add(traced)
        await session.flush()
        user2 = Message(
            chat_id=chat.id, parent_id=traced.id, role="user", content="thanks", token_count=1,
        )
        session.add(user2)
        await session.flush()
        plain = Message(
            chat_id=chat.id,
            parent_id=user2.id,
            role="assistant",
            content="Welcome.",
            token_count=2,
        )
        session.add(plain)
        await session.flush()
        chat.current_leaf_message_id = plain.id
        session.add(chat)
        await session.commit()

        history = await _load_branch_messages(session, chat.id)

    assert history[1]["content"] == "Done."
    assert history[1]["tool_trace"] == trace
    assert history[1]["id"] == traced.id
    assert history[1]["token_count"] > 2
    assert history[0] == {
        "role": "user", "content": "make file", "token_count": 3,
        "id": user.id, "tool_trace": None,
    }
    assert history[3] == {
        "role": "assistant", "content": "Welcome.", "token_count": 2,
        "id": plain.id, "tool_trace": None,
    }


async def test_migration_is_idempotent() -> None:
    """Running init_db again keeps the tool_trace column and does not fail."""
    await init_db()
    await init_db()
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA table_info(message)"))
        columns = [row[1] for row in result.fetchall()]
    assert "tool_trace" in columns


async def test_expansion_structure_and_id_pairing() -> None:
    """A traced assistant expands to assistant(tool_calls) -> tool per call -> assistant(text)."""
    trace = _trace("first_tool", "second_tool")
    async with async_session_factory() as session:
        chat, rows = await _make_chain(
            session,
            [("user", "do it", None), ("assistant", "Done.", trace), ("user", "thanks", None)],
        )
        context = _history_only(await build_llm_context(session, chat.id, MODEL))

    assert [m["role"] for m in context] == ["user", "assistant", "tool", "tool", "assistant", "user"]
    call_message = context[1]
    ids = [call["id"] for call in call_message["tool_calls"]]
    assert ids == [f"call_{rows[1].id}_0", f"call_{rows[1].id}_1"]
    assert call_message["content"] == ""
    assert [call["function"]["name"] for call in call_message["tool_calls"]] == [
        "first_tool", "second_tool",
    ]
    assert context[2]["tool_call_id"] == ids[0]
    assert context[3]["tool_call_id"] == ids[1]
    assert [context[2]["content"], context[3]["content"]] == ["first_tool ok", "second_tool ok"]
    assert context[4]["content"] == "Done."
    assert context[4]["token_count"] == rows[1].token_count


@pytest.mark.parametrize(
    "strategy", [ContextStrategy.SLIDING_WINDOW, ContextStrategy.TRUNCATE_MIDDLE],
)
async def test_window_cut_on_traced_message_never_orphans_tool(
    strategy: ContextStrategy,
) -> None:
    """Compression cuts on stored positions, so no tool message loses its tool_calls parent."""
    total = RECENT_MESSAGE_COUNT + 5
    cut_index = total - RECENT_MESSAGE_COUNT
    traced_indexes = {1, cut_index, cut_index + 2}
    specs: list[tuple[str, str, str | None]] = []
    for i in range(total):
        role = "user" if i % 2 == 0 else "assistant"
        trace = _trace(f"tool_{i}") if i in traced_indexes else None
        specs.append((role, "word " * 200, trace))
    assert specs[cut_index][0] == "assistant" and specs[cut_index][2] is not None

    async with async_session_factory() as session:
        chat, _ = await _make_chain(session, specs)
        await _set_strategy(session, chat.id, strategy, 2000)
        context = _history_only(await build_llm_context(session, chat.id, MODEL))

    assert context[0]["role"] != "tool"
    assert any(m["role"] == "tool" for m in context)
    for index, message in enumerate(context):
        if message["role"] != "tool":
            continue
        back = index
        while context[back]["role"] == "tool":
            back -= 1
        parent = context[back]
        assert parent["role"] == "assistant"
        assert message["tool_call_id"] in [call["id"] for call in parent["tool_calls"]]


async def test_stats_count_expanded_messages() -> None:
    """Stats include tool_calls arguments and tool results and never crash on empty content."""
    trace = _trace("tool_x")
    async with async_session_factory() as session:
        traced_chat, _ = await _make_chain(
            session, [("user", "go", None), ("assistant", "Done.", trace)],
        )
        plain_chat, _ = await _make_chain(
            session, [("user", "go", None), ("assistant", "Done.", None)],
        )
        traced_stats = await compute_chat_stats(session, traced_chat.id, MODEL)
        plain_stats = await compute_chat_stats(session, plain_chat.id, MODEL)
        context = await build_llm_context(session, traced_chat.id, MODEL)

    assert "error" not in traced_stats
    assert "error" not in plain_stats
    assert traced_stats["current_context_size"] > plain_stats["current_context_size"]
    system_tokens = llm_client.count_tokens(context[0]["content"])
    assert traced_stats["current_context_size"] == system_tokens + sum(
        m["token_count"] for m in context[1:]
    )

    calls_only = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "c", "type": "function", "function": {"name": "tool_x", "arguments": '{"a": 1}'}},
        ],
    }
    assert _message_tokens([calls_only]) > 0
    assert _message_tokens([{"role": "assistant", "content": None, "tool_calls": []}]) == 0


async def test_legacy_and_corrupt_traces_replay_safely() -> None:
    """Cut legacy arguments become {}; corrupt or nameless traces replay as plain messages."""
    legacy = json.dumps(
        [
            {
                "name": "mcp__fs__write_file",
                "arguments": '{"path": "' + "a" * 200 + "…",
                "ok": True,
                "result": "created",
            },
        ],
    )
    nameless = json.dumps([{"arguments": "{}"}])
    async with async_session_factory() as session:
        chat, _ = await _make_chain(
            session,
            [
                ("user", "u1", None),
                ("assistant", "legacy reply", legacy),
                ("user", "u2", None),
                ("assistant", "corrupt reply", "not json"),
                ("user", "u3", None),
                ("assistant", "nameless reply", nameless),
            ],
        )
        context = _history_only(await build_llm_context(session, chat.id, MODEL))

    call_messages = [m for m in context if m.get("tool_calls")]
    assert len(call_messages) == 1
    assert call_messages[0]["tool_calls"][0]["function"]["arguments"] == "{}"
    for message in call_messages:
        for call in message["tool_calls"]:
            json.loads(call["function"]["arguments"])
    assert sum(1 for m in context if m["role"] == "tool") == 1
    plain = {m["content"]: m for m in context if m["role"] == "assistant" and m["content"]}
    for content in ("corrupt reply", "nameless reply"):
        assert set(plain[content]) == {"role", "content", "token_count"}


@respx.mock
def test_tool_turn_persists_trace_and_next_turn_replays_structured() -> None:
    """A tool turn stores the trace; the next request replays it as tool_calls and tool messages."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_queue_responses(
            [
                _tool_calls_response(
                    [("call_1", "save_working_memory", json.dumps({"key": "k", "content": "v"}))],
                ),
                _plain_content_response("Saved."),
                _plain_content_response("Hi."),
            ],
        ),
    )

    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Trace"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "note k")
        stored = client.portal.call(_get_message, frames[-1]["message_id"])
        assert stored.tool_trace is not None
        assert "save_working_memory" in stored.tool_trace

        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            _send_and_drain(ws, "hello")

    bodies = _stream_bodies(route)
    assert len(bodies) == 3
    messages = [m for m in bodies[2]["messages"] if m["role"] != "system"]
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant", "user"]
    call_message = messages[1]
    assert call_message["content"] == ""
    assert call_message["tool_calls"][0]["function"]["name"] == "save_working_memory"
    assert json.loads(call_message["tool_calls"][0]["function"]["arguments"]) == {
        "key": "k", "content": "v",
    }
    assert call_message["tool_calls"][0]["id"] == f"call_{stored.id}_0" == messages[2]["tool_call_id"]
    assert messages[3]["content"] == "Saved."
    for message in bodies[2]["messages"]:
        assert not (isinstance(message.get("content"), str) and TOOL_TRACE_HEADER in message["content"])
        if message["role"] == "assistant" and message.get("content") == "":
            assert "tool_calls" in message


@respx.mock
def test_plain_turn_has_no_trace_and_history_is_unchanged() -> None:
    """A turn without tool calls stores NULL and replays exactly the stored text."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_queue_responses(
            [_plain_content_response("Just a reply."), _plain_content_response("Again.")],
        ),
    )

    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Plain"}).json()["id"]
        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "hi")
        stored = client.portal.call(_get_message, frames[-1]["message_id"])
        assert stored.tool_trace is None

        with client.websocket_connect(
            f"/ws/chat/{chat_id}", headers={"Origin": WS_ORIGIN},
        ) as ws:
            _send_and_drain(ws, "again")

    bodies = _stream_bodies(route)
    history = [m for m in bodies[1]["messages"] if m["role"] != "system"]
    assert [{"role": m["role"], "content": m["content"]} for m in history] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Just a reply."},
        {"role": "user", "content": "again"},
    ]
    assert all(set(m) == {"role", "content", "token_count"} for m in history)
