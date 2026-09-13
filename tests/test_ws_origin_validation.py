"""WebSocket Origin header validation tests."""

import pytest
from starlette.testclient import TestClient

from agent.main import app
from agent.state import CORS_ORIGINS


def _create_chat(client: TestClient) -> int:
    resp = client.post("/api/v1/chats", json={"title": "Origin test"})
    return resp.json()["id"]


def test_ws_accepts_none_origin() -> None:
    """WebSocket without Origin header should be accepted."""
    with TestClient(app) as client:
        chat_id = _create_chat(client)
        with client.websocket_connect(f"/ws/chat/{chat_id}") as ws:
            assert ws is not None


def test_ws_accepts_null_origin() -> None:
    """WebSocket with Origin: null should be accepted."""
    with TestClient(app) as client:
        chat_id = _create_chat(client)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": "null"},
        ) as ws:
            assert ws is not None


def test_ws_accepts_valid_origin() -> None:
    """WebSocket with valid UI origin should be accepted."""
    with TestClient(app) as client:
        chat_id = _create_chat(client)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": "http://localhost:8000"},
        ) as ws:
            assert ws is not None


def test_ws_rejects_invalid_origin() -> None:
    """WebSocket from unknown origin should be rejected with code 1008."""
    with TestClient(app) as client:
        chat_id = _create_chat(client)
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(
                f"/ws/chat/{chat_id}",
                headers={"Origin": "http://evil.com"},
            ) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008


@pytest.mark.parametrize("origin", CORS_ORIGINS)
def test_ws_accepts_all_cors_origins(origin: str) -> None:
    """Every configured CORS origin should be accepted."""
    with TestClient(app) as client:
        chat_id = _create_chat(client)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": origin},
        ) as ws:
            assert ws is not None
