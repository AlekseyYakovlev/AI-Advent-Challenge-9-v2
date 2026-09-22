"""End-to-end WebSocket regression test: a create_task turn must complete with `done`.

Regression for a `payload` variable-shadowing bug in `_handle_chat_message`: the
task_writes loop reassigned the function's `payload: MessagePayload` parameter to
the parsed tool-result dict, so the later `extract_and_update_facts(..., payload.content,
payload.model)` call crashed with `AttributeError: 'dict' object has no attribute
'content'` -- after the task was already persisted, but before the `done` frame was
ever sent. The WebSocket connection died mid-turn and the frontend's loading
indicator hung forever (only a page reload, hitting REST instead of WS, revealed the
already-persisted task).
"""

import json

import httpx
import respx
from sqlmodel import select
from starlette.testclient import TestClient

from agent import tasks
from agent.main import app
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Message, Task
from tests.conftest import login_test_client
from tests.test_memory_ws import (
    _plain_content_response,
    _queue_responses,
    _send_and_drain,
    _tool_calls_response,
)

BASE_URL = settings.LM_STUDIO_BASE_URL
MODEL = "test-model"
WS_ORIGIN = "http://localhost:8000"


async def _create_task_directly(user_id: int, chat_id: int) -> int:
    """Insert a Task row via the domain layer and return its id."""
    async with async_session_factory() as session:
        task = await tasks.create_task(session, user_id, chat_id, "Seed task", "desc", "goal")
        return task.id


@respx.mock
def test_create_task_turn_completes_with_done_frame() -> None:
    """A create_task tool call turn must persist the task AND send `done` (#bug repro)."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=_queue_responses(
            [
                _tool_calls_response(
                    [
                        (
                            "call_1",
                            "create_task",
                            json.dumps(
                                {
                                    "title": "Export chat history",
                                    "description": "Add a markdown export button",
                                    "goal": "User can download chat history as markdown",
                                },
                            ),
                        ),
                    ],
                ),
                _plain_content_response("Task created and set to planning."),
            ],
        ),
    )

    with TestClient(app) as client:
        login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Task turn"}).json()["id"]

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "Let's export chat history to markdown. Make a task.")

        done_frames = [f for f in frames if f.get("type") == "done"]
        assert len(done_frames) == 1, (
            "Turn must complete with exactly one `done` frame; a crash before it is "
            "sent leaves the frontend's streaming indicator stuck forever"
        )
        done_frame = done_frames[0]

        assert len(done_frame["task_writes"]) == 1
        write = done_frame["task_writes"][0]
        assert write["title"] == "Export chat history"
        assert write["state"] == "planning"

        rows = client.portal.call(_list_tasks, chat_id)
        assert len(rows) == 1
        assert rows[0].title == "Export chat history"


async def _list_tasks(chat_id: int) -> list[Task]:
    """Fetch all Task rows for a chat."""
    async with async_session_factory() as session:
        result = await session.exec(select(Task).where(Task.chat_id == chat_id))
        return list(result.all())


async def _list_messages(chat_id: int) -> list[Message]:
    """Fetch all Message rows for a chat."""
    async with async_session_factory() as session:
        result = await session.exec(select(Message).where(Message.chat_id == chat_id))
        return list(result.all())


@respx.mock
def test_illegal_transition_triggers_reprompt_and_empty_task_writes() -> None:
    """An illegal transition_task turn emits one TOOL_ERROR frame plus a streamed re-prompt (D-08)."""
    with TestClient(app) as client:
        user_id = login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Illegal transition"}).json()["id"]
        task_id = client.portal.call(_create_task_directly, user_id, chat_id)

        route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
            side_effect=_queue_responses(
                [
                    _tool_calls_response(
                        [
                            (
                                "call_1",
                                "transition_task",
                                json.dumps({"task_id": task_id, "new_state": "done"}),
                            ),
                        ],
                    ),
                    _plain_content_response("I tried to mark it done."),
                    _plain_content_response(
                        "Sorry, that move is not legal yet; the task stays in planning.",
                    ),
                ],
            ),
        )

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "Mark the task done.")

        error_frames = [f for f in frames if f.get("code") == "TOOL_ERROR"]
        assert len(error_frames) == 1

        token_texts = "".join(f.get("content", "") for f in frames if f.get("type") == "token")
        assert "Sorry, that move is not legal yet" in token_texts

        done_frame = frames[-1]
        assert done_frame["type"] == "done"
        assert done_frame["task_writes"] == []
        assert route.call_count == 3


@respx.mock
def test_rejected_resume_produces_tool_error_without_reprompt() -> None:
    """A rejected resume_task turn gets a TOOL_ERROR frame but no extra round trip."""
    with TestClient(app) as client:
        user_id = login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Resume rejection"}).json()["id"]
        task_id = client.portal.call(_create_task_directly, user_id, chat_id)

        route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
            side_effect=_queue_responses(
                [
                    _tool_calls_response(
                        [
                            ("call_1", "resume_task", json.dumps({"task_id": task_id})),
                        ],
                    ),
                    _plain_content_response("It was not paused."),
                ],
            ),
        )

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "Resume the task.")

        error_frames = [f for f in frames if f.get("code") == "TOOL_ERROR"]
        assert len(error_frames) == 1

        done_frame = frames[-1]
        assert done_frame["type"] == "done"
        assert route.call_count == 2


@respx.mock
def test_legal_transition_reports_task_writes_without_reprompt() -> None:
    """A legal transition_task turn makes no extra round trip and reports the task in task_writes."""
    with TestClient(app) as client:
        user_id = login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Legal transition"}).json()["id"]
        task_id = client.portal.call(_create_task_directly, user_id, chat_id)

        route = respx.post(f"{BASE_URL}/v1/chat/completions").mock(
            side_effect=_queue_responses(
                [
                    _tool_calls_response(
                        [
                            (
                                "call_1",
                                "transition_task",
                                json.dumps({"task_id": task_id, "new_state": "execution"}),
                            ),
                        ],
                    ),
                    _plain_content_response("Moved to execution."),
                ],
            ),
        )

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "Start working on it.")

        error_frames = [f for f in frames if f.get("code") == "TOOL_ERROR"]
        assert error_frames == []

        done_frame = frames[-1]
        assert done_frame["type"] == "done"
        assert len(done_frame["task_writes"]) == 1
        assert done_frame["task_writes"][0]["state"] == "execution"
        assert route.call_count == 2


@respx.mock
def test_reprompt_failure_still_completes_turn() -> None:
    """If the re-prompt's stream_chat raises, the turn still completes and the user message stays."""
    with TestClient(app) as client:
        user_id = login_test_client(client)
        chat_id = client.post("/api/v1/chats", json={"title": "Reprompt failure"}).json()["id"]
        task_id = client.portal.call(_create_task_directly, user_id, chat_id)

        respx.post(f"{BASE_URL}/v1/chat/completions").mock(
            side_effect=_queue_responses(
                [
                    _tool_calls_response(
                        [
                            (
                                "call_1",
                                "transition_task",
                                json.dumps({"task_id": task_id, "new_state": "done"}),
                            ),
                        ],
                    ),
                    _plain_content_response("Trying to finish it."),
                    httpx.Response(500),
                ],
            ),
        )

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            frames = _send_and_drain(ws, "Mark it done.")

        done_frame = frames[-1]
        assert done_frame["type"] == "done"

        messages = client.portal.call(_list_messages, chat_id)
        user_messages = [m for m in messages if m.role == "user"]
        assert len(user_messages) == 1
