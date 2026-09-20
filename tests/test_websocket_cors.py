"""WebSocket connectivity tests with CORS middleware enabled."""

import pytest
from httpx import AsyncClient
from starlette.testclient import TestClient

from agent.main import app
from tests.conftest import login_test_client


def _create_chat(client: TestClient) -> int:
    resp = client.post("/api/v1/chats", json={"title": "CORS WS test"})
    return resp.json()["id"]


def test_websocket_connect_with_origin() -> None:
    """WebSocket with UI Origin header should connect successfully."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": "http://localhost:8000"},
        ) as ws:
            assert ws is not None


def test_websocket_connect_without_origin() -> None:
    """WebSocket without Origin header should connect successfully."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with client.websocket_connect(f"/ws/chat/{chat_id}") as ws:
            assert ws is not None


def test_websocket_connect_with_null_origin() -> None:
    """WebSocket with Origin: null should connect successfully."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": "null"},
        ) as ws:
            assert ws is not None


@pytest.mark.asyncio
async def test_rest_api_still_works(authenticated_client: AsyncClient) -> None:
    """REST API should respond with CORS headers after middleware change."""
    resp = await authenticated_client.get(
        "/api/v1/chats",
        headers={"Origin": "http://localhost:8000"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:8000"
