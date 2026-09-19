"""Concurrent WebSocket message handling tests."""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import pytest
import respx
from sqlmodel import select
from starlette.testclient import TestClient

from agent.main import app
from shared.config import settings
from shared.database import async_session_factory
from shared.models import Message
from tests.conftest import login_test_client

BASE_URL = settings.LM_STUDIO_BASE_URL
MODEL = "test-model"
WS_ORIGIN = "http://localhost:8000"


def _stream_response(text: str) -> httpx.Response:
    """Build an SSE streaming response for chat completions."""
    lines = [
        f'data: {json.dumps({"choices": [{"delta": {"content": chunk}}]})}'
        for chunk in text.split()
    ]
    lines.append("data: [DONE]")
    body = "\n".join(lines)
    return httpx.Response(200, text=body)


@respx.mock
@pytest.mark.asyncio
async def test_five_parallel_ws_messages_no_integrity_error() -> None:
    """Five concurrent WS sends on one chat must not raise IntegrityError."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=lambda _request: _stream_response("ok response"),
    )

    with TestClient(app) as client:
        login_test_client(client)
        chat_resp = client.post("/api/v1/chats", json={"title": "Concurrent"})
        chat_id = chat_resp.json()["id"]

        def send_message(index: int) -> None:
            with client.websocket_connect(
                f"/ws/chat/{chat_id}",
                headers={"Origin": WS_ORIGIN},
            ) as ws:
                ws.send_json({"content": f"message {index}", "model": MODEL})
                while True:
                    payload = ws.receive_json()
                    if payload.get("type") == "done":
                        break

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(send_message, i) for i in range(5)]
            for future in as_completed(futures):
                future.result()

    async with async_session_factory() as session:
        result = await session.exec(
            select(Message).where(Message.chat_id == chat_id),
        )
        messages = result.all()
        assert len(messages) == 10
