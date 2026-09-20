"""End-to-end WebSocket tests: the self-critique conflict-check loop (INV-04)."""

import json

import httpx
import respx
from sqlmodel import select
from starlette.testclient import TestClient

from agent.main import app
from shared.config import settings
from shared.database import async_session_factory
from shared.models import InvariantConflict, Message
from tests.conftest import login_test_client

BASE_URL = settings.LM_STUDIO_BASE_URL
MODEL = "test-model"
WS_ORIGIN = "http://localhost:8000"


def _sse_body(lines: list[str]) -> bytes:
    """Join raw SSE `data: ...` lines into a byte body."""
    return ("\n".join(lines) + "\n").encode()


def _plain_content_response(text: str) -> httpx.Response:
    """Build a plain-content-only SSE response (no tool calls), one word-plus-space per chunk."""
    words = text.split(" ")
    chunks = [word + (" " if i < len(words) - 1 else "") for i, word in enumerate(words)]
    lines = [
        f'data: {json.dumps({"choices": [{"delta": {"content": chunk}, "finish_reason": None}]})}'
        for chunk in chunks
    ]
    lines.append('data: {"choices":[{"delta":{},"finish_reason":"stop"}]}')
    lines.append("data: [DONE]")
    return httpx.Response(200, content=_sse_body(lines))


def _tool_calls_response(calls: list[tuple[str, str, str]]) -> httpx.Response:
    """Build an SSE response that ends in finish_reason=tool_calls.

    `calls` is a list of (tool_call_id, function_name, raw_arguments_json_string).
    """
    tool_calls_delta = [
        {
            "index": i,
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": raw_args},
        }
        for i, (call_id, name, raw_args) in enumerate(calls)
    ]
    chunk1 = {"choices": [{"delta": {"tool_calls": tool_calls_delta}, "finish_reason": None}]}
    chunk2 = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
    lines = [f"data: {json.dumps(chunk1)}", f"data: {json.dumps(chunk2)}", "data: [DONE]"]
    return httpx.Response(200, content=_sse_body(lines))


def _plain_json_response(content: str) -> httpx.Response:
    """Build a plain (non-SSE) JSON body matching what `complete_chat()` parses."""
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _queue_responses(responses: list[httpx.Response]):
    """Return a respx side_effect callable serving responses in order, one per request."""
    queue = list(responses)

    def _side_effect(_request: httpx.Request) -> httpx.Response:
        return queue.pop(0)

    return _side_effect


def _send_and_drain(ws, content: str) -> list[dict]:
    """Send one chat message and collect every frame up to and including `done`."""
    ws.send_json({"content": content, "model": MODEL})
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") == "done":
            break
    return frames


def _create_global_invariant(client: TestClient, title: str, rule_text: str) -> int:
    """Create a global invariant via REST and return its id."""
    resp = client.post("/api/v1/invariants", json={"title": title, "rule_text": rule_text})
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_chat_invariant(
    client: TestClient,
    chat_id: int,
    title: str,
    rule_text: str,
    overrides_id: int | None = None,
) -> int:
    """Create a per-chat invariant via REST and return its id."""
    resp = client.post(
        f"/api/v1/chats/{chat_id}/invariants",
        json={"title": title, "rule_text": rule_text, "overrides_id": overrides_id},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


@respx.mock
def test_turn_without_active_invariants_makes_no_critique_call() -> None:
    """A chat with zero active invariants must never pay for a critique call (OQ2)."""
    route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_queue_responses([_plain_content_response("Обычный ответ.")]),
    )

    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "No invariants"}).json()["id"]

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "hello")

        done_frame = frames[-1]
        assert done_frame["type"] == "done"
        assert route.call_count == 1


@respx.mock
def test_critique_reporting_no_conflict_persists_nothing() -> None:
    """A critique that reports no conflict leaves nothing persisted."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_queue_responses(
            [
                _plain_content_response("Ответ"),
                _plain_json_response('{"conflict": false}'),
            ],
        ),
    )

    with TestClient(app) as client:
        login_test_client(client)
        _create_global_invariant(client, "Без Docker", "Никогда не предлагай Docker")
        chat_id = client.post("/api/v1/chats", json={"title": "No conflict"}).json()["id"]

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "hello")

        done_frame = frames[-1]
        assert done_frame["type"] == "done"
        assert done_frame["invariant_conflict"] is None

        rows = client.portal.call(_list_conflicts, chat_id)
        assert rows == []


@respx.mock
def test_flagged_conflict_triggers_justify_retract_and_persists() -> None:
    """A flagged conflict runs the justify/retract round-trip and persists the record (INV-04)."""

    with TestClient(app) as client:
        login_test_client(client)
        global_id = _create_global_invariant(
            client, "Без Docker", "Никогда не предлагай Docker",
        )
        chat_id = client.post("/api/v1/chats", json={"title": "Conflict"}).json()["id"]

        respx.post(f"{BASE_URL}/v1/chat/completions").mock(
            side_effect=_queue_responses(
                [
                    _plain_content_response("Разверни через Docker"),
                    _plain_json_response(
                        json.dumps(
                            {
                                "conflict": True,
                                "invariant_scope": "global",
                                "invariant_id": global_id,
                                "explanation": "предложен Docker",
                            },
                        ),
                    ),
                    _plain_content_response(
                        "Беру свои слова назад — Docker запрещён правилом.",
                    ),
                ],
            ),
        )

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "разверни приложение")

        token_texts = "".join(
            f.get("content", "") for f in frames if f.get("type") == "token"
        )
        assert "Беру свои слова назад" in token_texts

        done_frame = frames[-1]
        assert done_frame["type"] == "done"
        conflict = done_frame["invariant_conflict"]
        assert conflict is not None
        assert conflict["invariant_scope"] == "global"
        assert conflict["invariant_id"] == global_id
        assert conflict["invariant_title"] == "Без Docker"
        assert conflict["note"]
        assert conflict["message_id"] == done_frame["message_id"]

        rows = client.portal.call(_list_conflicts, chat_id)
        assert len(rows) == 1
        assert rows[0].message_id == done_frame["message_id"]
        assert "Беру свои слова назад" in rows[0].note
        assert rows[0].invariant_title == "Без Docker"

        assistant_msg = client.portal.call(_get_message, done_frame["message_id"])
        assert "Разверни через Docker" in assistant_msg.content
        assert "Беру свои слова назад" in assistant_msg.content


@respx.mock
def test_critique_http_failure_fails_open() -> None:
    """A critique-call HTTP failure must never abort the turn or delete the user message."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_queue_responses(
            [
                _plain_content_response("Ответ"),
                httpx.Response(500),
            ],
        ),
    )

    with TestClient(app) as client:
        login_test_client(client)
        _create_global_invariant(client, "Без Docker", "Никогда не предлагай Docker")
        chat_id = client.post("/api/v1/chats", json={"title": "Critique fails"}).json()["id"]

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "hello")

        done_frame = frames[-1]
        assert done_frame["type"] == "done"
        assert done_frame["invariant_conflict"] is None

        messages = client.portal.call(_list_messages, chat_id)
        user_messages = [m for m in messages if m.role == "user"]
        assert len(user_messages) == 1

        rows = client.portal.call(_list_conflicts, chat_id)
        assert rows == []


@respx.mock
def test_critique_unparseable_output_fails_open() -> None:
    """Unparseable critique JSON must never abort the turn or delete the user message."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_queue_responses(
            [
                _plain_content_response("Ответ"),
                _plain_json_response("я не знаю, что тут в JSON"),
            ],
        ),
    )

    with TestClient(app) as client:
        login_test_client(client)
        _create_global_invariant(client, "Без Docker", "Никогда не предлагай Docker")
        chat_id = client.post("/api/v1/chats", json={"title": "Unparseable"}).json()["id"]

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "hello")

        done_frame = frames[-1]
        assert done_frame["type"] == "done"
        assert done_frame["invariant_conflict"] is None

        messages = client.portal.call(_list_messages, chat_id)
        user_messages = [m for m in messages if m.role == "user"]
        assert len(user_messages) == 1

        rows = client.portal.call(_list_conflicts, chat_id)
        assert rows == []


@respx.mock
def test_critique_hallucinated_invariant_id_is_ignored() -> None:
    """A citation that matches nothing in the resolved set must never write a row (T-05-12)."""
    with TestClient(app) as client:
        login_test_client(client)
        _create_global_invariant(client, "Без Docker", "Никогда не предлагай Docker")
        chat_id = client.post("/api/v1/chats", json={"title": "Hallucinated"}).json()["id"]

        route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
            side_effect=_queue_responses(
                [
                    _plain_content_response("Ответ"),
                    _plain_json_response(
                        json.dumps(
                            {
                                "conflict": True,
                                "invariant_scope": "global",
                                "invariant_id": 999999,
                                "explanation": "выдуманный конфликт",
                            },
                        ),
                    ),
                ],
            ),
        )

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "hello")

        assert route.call_count == 2

        done_frame = frames[-1]
        assert done_frame["type"] == "done"
        assert done_frame["invariant_conflict"] is None

        rows = client.portal.call(_list_conflicts, chat_id)
        assert rows == []


@respx.mock
def test_critique_prompt_uses_the_resolved_override_set() -> None:
    """The critique prompt must reflect the override-resolved set, not raw table rows (Pitfall 3)."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Override"}).json()["id"]
        global_id = _create_global_invariant(
            client, "Без Docker", "Никогда не предлагай Docker",
        )
        _create_chat_invariant(
            client,
            chat_id,
            "Тут можно Docker",
            "В этом чате Docker разрешён",
            overrides_id=global_id,
        )

        route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
            side_effect=_queue_responses(
                [
                    _plain_content_response("Ответ"),
                    _plain_json_response('{"conflict": false}'),
                ],
            ),
        )

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            _send_and_drain(ws, "hello")

        critique_request_body = json.loads(route.calls[1].request.content)
        critique_prompt = critique_request_body["messages"][0]["content"]
        assert "overridden for this chat — see below" in critique_prompt
        assert "overrides the above" in critique_prompt


@respx.mock
def test_critique_prompt_includes_tool_calls() -> None:
    """The critique prompt must include tool calls alongside prose (D-08)."""
    with TestClient(app) as client:
        login_test_client(client)
        _create_global_invariant(client, "Без Docker", "Никогда не предлагай Docker")
        chat_id = client.post("/api/v1/chats", json={"title": "Tool call scope"}).json()["id"]

        route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
            side_effect=_queue_responses(
                [
                    _tool_calls_response(
                        [
                            (
                                "call_1",
                                "save_working_memory",
                                json.dumps({"key": "step", "content": "note"}),
                            ),
                        ],
                    ),
                    _plain_content_response("Saved."),
                    _plain_json_response('{"conflict": false}'),
                ],
            ),
        )

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            _send_and_drain(ws, "remember the step")

        assert route.call_count == 3
        critique_request_body = json.loads(route.calls[2].request.content)
        critique_prompt = critique_request_body["messages"][0]["content"]
        assert "save_working_memory" in critique_prompt


async def _get_message(message_id: int) -> Message:
    """Fetch a Message row by id."""
    async with async_session_factory() as session:
        return await session.get(Message, message_id)


async def _list_messages(chat_id: int) -> list[Message]:
    """Fetch all Message rows for a chat."""
    async with async_session_factory() as session:
        result = await session.exec(select(Message).where(Message.chat_id == chat_id))
        return list(result.all())


async def _list_conflicts(chat_id: int) -> list[InvariantConflict]:
    """Fetch all InvariantConflict rows for a chat."""
    async with async_session_factory() as session:
        result = await session.exec(
            select(InvariantConflict).where(InvariantConflict.chat_id == chat_id),
        )
        return list(result.all())
