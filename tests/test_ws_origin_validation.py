"""WebSocket Origin header validation tests."""

import pytest
from starlette.testclient import TestClient

from agent.main import app
from tests.conftest import login_test_client


def _create_chat(client: TestClient) -> int:
    resp = client.post("/api/v1/chats", json={"title": "Origin test"})
    return resp.json()["id"]


def test_ws_accepts_none_origin() -> None:
    """WebSocket without Origin header should be accepted."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with client.websocket_connect(f"/ws/chat/{chat_id}") as ws:
            assert ws is not None


def test_ws_accepts_null_origin() -> None:
    """WebSocket with Origin: null should be accepted."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": "null"},
        ) as ws:
            assert ws is not None


def test_ws_accepts_localhost_8000() -> None:
    """WebSocket with localhost:8000 origin should be accepted."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": "http://localhost:8000"},
        ) as ws:
            assert ws is not None


def test_ws_accepts_127_0_0_1_8000() -> None:
    """WebSocket with 127.0.0.1:8000 origin should be accepted."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": "http://127.0.0.1:8000"},
        ) as ws:
            assert ws is not None


def test_ws_accepts_localhost_8001() -> None:
    """WebSocket with localhost:8001 origin should be accepted."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": "http://localhost:8001"},
        ) as ws:
            assert ws is not None


def test_ws_accepts_127_0_0_1_8001() -> None:
    """WebSocket with 127.0.0.1:8001 origin should be accepted."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": "http://127.0.0.1:8001"},
        ) as ws:
            assert ws is not None


def test_ws_rejects_invalid_origin() -> None:
    """WebSocket from unknown origin should be rejected with code 1008."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(
                f"/ws/chat/{chat_id}",
                headers={"Origin": "http://evil.com"},
            ) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008
