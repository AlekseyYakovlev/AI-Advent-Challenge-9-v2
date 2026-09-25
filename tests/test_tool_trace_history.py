"""Tool trace persistence on assistant messages and replay in later-turn context."""

import json

import respx
from sqlalchemy import text
from starlette.testclient import TestClient

from agent.context_engine import (
    TOOL_TRACE_ARGS_CHARS,
    TOOL_TRACE_HEADER,
    TOOL_TRACE_RESULT_CHARS,
    _load_branch_messages,
    render_tool_trace,
    serialize_tool_trace,
)
from agent.llm_client import llm_client
from agent.main import app
from shared.database import async_session_factory, engine, init_db
from shared.models import Chat, Message
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


def _stream_bodies(route: respx.Route) -> list[dict]:
    """Return the JSON bodies of streaming chat requests only."""
    bodies = [json.loads(call.request.content) for call in route.calls]
    return [body for body in bodies if body.get("stream") is True]


def test_serialize_empty_results_returns_none() -> None:
    """No tool results means no trace."""
    assert serialize_tool_trace([]) is None


def test_serialize_truncates_arguments_and_result() -> None:
    """Arguments and results are cut and marked with an ellipsis."""
    results = [
        {
            "name": "mcp__fs__write_file",
            "arguments": "a" * 500,
            "ok": True,
            "content": "ignored",
            "result_text": "r" * 1000,
        },
    ]
    trace = serialize_tool_trace(results)
    assert trace is not None
    entry = json.loads(trace)[0]
    assert entry["arguments"] == "a" * TOOL_TRACE_ARGS_CHARS + "…"
    assert entry["result"] == "r" * TOOL_TRACE_RESULT_CHARS + "…"
    assert entry["name"] == "mcp__fs__write_file"
    assert entry["ok"] is True


def test_serialize_falls_back_to_content_without_result_text() -> None:
    """Built-in tools have no result_text, so content is used."""
    trace = serialize_tool_trace(
        [{"name": "save_working_memory", "arguments": "{}", "ok": True, "content": '{"ok":1}'}],
    )
    assert trace is not None
    assert json.loads(trace)[0]["result"] == '{"ok":1}'


def test_render_none_and_bad_json_are_empty() -> None:
    """Missing or corrupt traces render as nothing without raising."""
    assert render_tool_trace(None) == ""
    assert render_tool_trace("not json") == ""
    assert render_tool_trace(json.dumps([{"name": "x"}])) == ""


def test_render_lists_each_call() -> None:
    """A valid trace renders a header plus one line per call."""
    trace = serialize_tool_trace(
        [
            {"name": "a", "arguments": "{}", "ok": True, "content": "done"},
            {"name": "b", "arguments": "{}", "ok": False, "content": "boom"},
        ],
    )
    lines = render_tool_trace(trace).split("\n")
    assert lines[0] == TOOL_TRACE_HEADER
    assert lines[1] == "- a({}) -> ok: done"
    assert lines[2] == "- b({}) -> error: boom"


async def test_load_branch_messages_replays_trace_only_for_traced_rows() -> None:
    """Traced assistant rows carry the rendered log; untraced rows are unchanged."""
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

    rendered = render_tool_trace(trace)
    assert history[1]["content"] == "Done.\n\n" + rendered
    assert history[1]["token_count"] == 2 + llm_client.count_tokens(rendered)
    assert history[0] == {"role": "user", "content": "make file", "token_count": 3}
    assert history[3] == {"role": "assistant", "content": "Welcome.", "token_count": 2}


async def test_migration_is_idempotent() -> None:
    """Running init_db again keeps the tool_trace column and does not fail."""
    await init_db()
    await init_db()
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA table_info(message)"))
        columns = [row[1] for row in result.fetchall()]
    assert "tool_trace" in columns


@respx.mock
def test_tool_turn_persists_trace_and_next_turn_replays_it() -> None:
    """A tool turn stores the trace; the next turn's request history includes it."""
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
    assistant_contents = [m["content"] for m in bodies[2]["messages"] if m["role"] == "assistant"]
    assert any(
        "save_working_memory" in content and TOOL_TRACE_HEADER in content
        for content in assistant_contents
    )


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
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in bodies[1]["messages"]
        if m["role"] != "system"
    ]
    assert history == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Just a reply."},
        {"role": "user", "content": "again"},
    ]
