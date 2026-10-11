"""WebSocket Origin header validation tests."""

import pytest
from starlette.testclient import TestClient

from agent.main import app
from shared.config import settings
from tests.conftest import login_test_client

LAN_HOST = "192.168.1.5"
NAME_HOST = "my-pc"


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


def test_ws_rejects_null_origin() -> None:
    """WebSocket with Origin: null should be rejected with code 1008."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(
                f"/ws/chat/{chat_id}",
                headers={"Origin": "null"},
            ) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008


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


def test_ws_rejects_localhost_agent_port() -> None:
    """WebSocket with an Origin on the Agent port should be rejected with code 1008."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(
                f"/ws/chat/{chat_id}",
                headers={"Origin": f"http://localhost:{settings.AGENT_PORT}"},
            ) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008


def test_ws_rejects_127_0_0_1_agent_port() -> None:
    """WebSocket with a 127.0.0.1 Origin on the Agent port should be rejected with code 1008."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(
                f"/ws/chat/{chat_id}",
                headers={"Origin": f"http://127.0.0.1:{settings.AGENT_PORT}"},
            ) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008


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


def test_ws_accepts_lan_ip_host_match() -> None:
    """WebSocket whose Origin host equals the LAN IP Host should be accepted."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={
                "Host": f"{LAN_HOST}:{settings.AGENT_PORT}",
                "Origin": f"http://{LAN_HOST}:{settings.UI_PORT}",
            },
        ) as ws:
            assert ws is not None


def test_ws_accepts_machine_name_host_match() -> None:
    """WebSocket whose Origin host equals the machine name Host should be accepted."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={
                "Host": f"{NAME_HOST}:{settings.AGENT_PORT}",
                "Origin": f"http://{NAME_HOST}:{settings.UI_PORT}",
            },
        ) as ws:
            assert ws is not None


def test_ws_rejects_foreign_origin_with_lan_host() -> None:
    """A foreign Origin on the UI port is rejected even when Host is a LAN IP."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(
                f"/ws/chat/{chat_id}",
                headers={
                    "Host": f"{LAN_HOST}:{settings.AGENT_PORT}",
                    "Origin": f"http://evil.example:{settings.UI_PORT}",
                },
            ) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008


def test_ws_rejects_other_lan_machine_origin() -> None:
    """An Origin of another LAN machine is rejected."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(
                f"/ws/chat/{chat_id}",
                headers={
                    "Host": f"{LAN_HOST}:{settings.AGENT_PORT}",
                    "Origin": f"http://192.168.1.77:{settings.UI_PORT}",
                },
            ) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008
