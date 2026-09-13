"""WebSocket security: origin, rate limiting, and idle timeout tests."""

import json
import time

import httpx
import pytest
import respx
from starlette.testclient import TestClient

import agent.ws as ws_module
from agent.main import app
from agent.state import ws_rate_limiter
from shared.config import settings

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
    return httpx.Response(200, text="\n".join(lines))


def test_ws_rejects_invalid_origin() -> None:
    """WebSocket connections from unknown origins should be rejected."""
    with TestClient(app) as client:
        chat_resp = client.post("/api/v1/chats", json={"title": "Secure"})
        chat_id = chat_resp.json()["id"]

        with pytest.raises(Exception):
            with client.websocket_connect(
                f"/ws/chat/{chat_id}",
                headers={"Origin": "http://evil.example.com"},
            ) as ws:
                ws.receive_json()


@respx.mock
def test_ws_rate_limiting() -> None:
    """More than 10 messages per minute should return a rate-limit error."""
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        side_effect=lambda _request: _stream_response("ok"),
    )

    with TestClient(app) as client:
        chat_resp = client.post("/api/v1/chats", json={"title": "Rate limit"})
        chat_id = chat_resp.json()["id"]
        ws_rate_limiter.pop(chat_id, None)

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            for i in range(10):
                ws.send_json({"content": f"msg {i}", "model": MODEL})
                while True:
                    payload = ws.receive_json()
                    if payload.get("type") in {"done", "error"}:
                        break

            ws.send_json({"content": "one too many", "model": MODEL})
            payload = ws.receive_json()
            assert payload["type"] == "error"
            assert "Rate limit" in payload["detail"]


@respx.mock
def test_ws_idle_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Idle connections should close after the configured timeout."""
    monkeypatch.setattr(ws_module, "IDLE_TIMEOUT_SECONDS", 0.1)

    with TestClient(app) as client:
        chat_resp = client.post("/api/v1/chats", json={"title": "Idle"})
        chat_id = chat_resp.json()["id"]

        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            time.sleep(0.3)
            with pytest.raises(Exception):
                ws.receive_json()
