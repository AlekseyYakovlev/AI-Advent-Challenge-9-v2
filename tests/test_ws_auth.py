"""WebSocket pre-accept session-cookie and chat-ownership gate tests."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select
from starlette.testclient import TestClient

from agent.main import app
from shared.auth import SESSION_COOKIE_NAME
from shared.database import async_session_factory
from shared.models import Chat
from shared.models import Session as SessionRow
from tests.conftest import login_test_client

WS_ORIGIN = "http://localhost:8000"


def _create_chat(client: TestClient) -> int:
    resp = client.post("/api/v1/chats", json={"title": "WS auth test"})
    return resp.json()["id"]


async def _expire_session(user_id: int) -> None:
    """Push every session row for user_id one day into the past."""
    async with async_session_factory() as session:
        result = await session.exec(
            select(SessionRow).where(SessionRow.user_id == user_id),
        )
        for row in result.all():
            row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
            session.add(row)
        await session.commit()


async def _delete_sessions(user_id: int) -> None:
    """Delete every session row for user_id."""
    async with async_session_factory() as session:
        result = await session.exec(
            select(SessionRow).where(SessionRow.user_id == user_id),
        )
        for row in result.all():
            await session.delete(row)
        await session.commit()


async def _chat_exists(chat_id: int) -> bool:
    """Return True when a chat row with chat_id still exists."""
    async with async_session_factory() as session:
        chat = await session.get(Chat, chat_id)
        return chat is not None


def test_ws_auth_accepts_logged_in_owner() -> None:
    """A logged-in owner's WebSocket handshake should be accepted."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            assert ws is not None


def test_ws_auth_rejects_no_cookie() -> None:
    """A handshake with no session cookie should be closed with 1008."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        client.cookies.clear()
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(
                f"/ws/chat/{chat_id}",
                headers={"Origin": WS_ORIGIN},
            ) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008


def test_ws_auth_rejects_garbage_cookie() -> None:
    """A handshake with an unrecognized session cookie should be closed with 1008."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)
        client.cookies.set(SESSION_COOKIE_NAME, "not-a-real-token")
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(
                f"/ws/chat/{chat_id}",
                headers={"Origin": WS_ORIGIN},
            ) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008


def test_ws_auth_rejects_expired_session() -> None:
    """A handshake with an expired session cookie should be closed with 1008."""
    with TestClient(app) as client:
        user_id = login_test_client(client)
        chat_id = _create_chat(client)
        client.portal.call(_expire_session, user_id)
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(
                f"/ws/chat/{chat_id}",
                headers={"Origin": WS_ORIGIN},
            ) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008


def test_ws_auth_rejects_wrong_owner() -> None:
    """A logged-in user cannot connect to another user's chat socket."""
    with TestClient(app) as client:
        login_test_client(client)
        chat_id = _create_chat(client)

        login_test_client(client, "otheruser", "otherpass")
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(
                f"/ws/chat/{chat_id}",
                headers={"Origin": WS_ORIGIN},
            ) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008
        assert client.portal.call(_chat_exists, chat_id) is True


def test_ws_auth_no_revalidation_mid_connection() -> None:
    """Deleting the session mid-connection must not kill an already-open socket (D-10)."""
    with TestClient(app) as client:
        user_id = login_test_client(client)
        chat_id = _create_chat(client)
        with client.websocket_connect(
            f"/ws/chat/{chat_id}",
            headers={"Origin": WS_ORIGIN},
        ) as ws:
            client.portal.call(_delete_sessions, user_id)
            ws.send_json({})
            payload = ws.receive_json()
            assert payload["type"] == "error"
