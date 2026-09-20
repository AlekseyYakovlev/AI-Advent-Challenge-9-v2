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

from agent.main import app
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Task
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
