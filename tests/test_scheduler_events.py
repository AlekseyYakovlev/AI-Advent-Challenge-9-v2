"""Tests for the per-user EventHub and the /ws/events WebSocket endpoint."""

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlmodel import delete, select
from starlette.testclient import TestClient

from agent.events import EVENTS_QUEUE_MAXSIZE, EventHub, hub
from agent.main import app
from shared.auth import SESSION_COOKIE_NAME
from shared.database import async_session_factory
from shared.models import Session as SessionRow
from tests.conftest import login_test_client

WS_ORIGIN = "http://localhost:8000"
EVIL_ORIGIN = "http://evil.example"
EVENTS_PATH = "/ws/events"


async def _publish(user_id: int, frame: dict[str, Any]) -> None:
    """Publish on the app loop so queue wakeups happen on the loop that owns them."""
    hub.publish(user_id, frame)


async def _subscriber_count(user_id: int) -> int:
    """Read the live subscriber count from the app loop."""
    return hub.subscriber_count(user_id)


def _wait_for_no_subscribers(client: TestClient, user_id: int) -> int:
    """Poll for up to one second until the user has no subscribers; return the last count."""
    deadline = time.monotonic() + 1.0
    count = client.portal.call(_subscriber_count, user_id)
    while count != 0 and time.monotonic() < deadline:
        time.sleep(0.02)
        count = client.portal.call(_subscriber_count, user_id)
    return count


def _wait_for_subscribers(client: TestClient, user_id: int, expected: int) -> int:
    """Poll for up to one second until the user has the expected subscribers."""
    deadline = time.monotonic() + 1.0
    count = client.portal.call(_subscriber_count, user_id)
    while count != expected and time.monotonic() < deadline:
        time.sleep(0.02)
        count = client.portal.call(_subscriber_count, user_id)
    return count


def test_events_ws_rejects_no_cookie() -> None:
    """A handshake with no session cookie is closed with 1008."""
    with TestClient(app) as client:
        login_test_client(client)
        client.cookies.clear()
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(EVENTS_PATH, headers={"Origin": WS_ORIGIN}) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008


def test_events_ws_rejects_garbage_cookie() -> None:
    """A handshake with an unrecognised session cookie is closed with 1008."""
    with TestClient(app) as client:
        login_test_client(client)
        client.cookies.set(SESSION_COOKIE_NAME, "not-a-real-token")
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(EVENTS_PATH, headers={"Origin": WS_ORIGIN}) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008


def test_events_ws_rejects_foreign_origin() -> None:
    """A valid session with a foreign Origin is closed with 1008."""
    with TestClient(app) as client:
        login_test_client(client)
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(EVENTS_PATH, headers={"Origin": EVIL_ORIGIN}) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008


def test_events_ws_delivers_published_frame_to_owner() -> None:
    """A frame published for the logged-in user arrives unchanged on their socket."""
    frame = {"type": "task_updated", "task": {"id": 7, "title": "Digest"}}
    with TestClient(app) as client:
        user_id = login_test_client(client)
        with client.websocket_connect(EVENTS_PATH, headers={"Origin": WS_ORIGIN}) as ws:
            assert _wait_for_subscribers(client, user_id, 1) == 1
            client.portal.call(_publish, user_id, frame)
            assert ws.receive_json() == frame


def test_events_ws_isolates_users() -> None:
    """A frame published for user A never reaches user B's socket."""
    with TestClient(app) as client:
        user_a = login_test_client(client, "alice", "pw1")
        with client.websocket_connect(EVENTS_PATH, headers={"Origin": WS_ORIGIN}) as ws_a:
            user_b = login_test_client(client, "bob", "pw2")
            with client.websocket_connect(EVENTS_PATH, headers={"Origin": WS_ORIGIN}) as ws_b:
                assert user_a != user_b
                assert _wait_for_subscribers(client, user_a, 1) == 1
                assert _wait_for_subscribers(client, user_b, 1) == 1

                client.portal.call(_publish, user_a, {"type": "task_deleted", "task_id": 1})
                client.portal.call(_publish, user_b, {"type": "task_deleted", "task_id": 999})

                assert ws_a.receive_json() == {"type": "task_deleted", "task_id": 1}
                # The sentinel being B's first frame proves A's frame never reached B.
                assert ws_b.receive_json() == {"type": "task_deleted", "task_id": 999}


def test_events_ws_unsubscribes_on_close() -> None:
    """Closing the socket removes its queue from the hub."""
    with TestClient(app) as client:
        user_id = login_test_client(client)
        with client.websocket_connect(EVENTS_PATH, headers={"Origin": WS_ORIGIN}):
            assert _wait_for_subscribers(client, user_id, 1) == 1
        assert _wait_for_no_subscribers(client, user_id) == 0


def test_events_ws_fans_out_to_every_socket_of_a_user() -> None:
    """Two sockets of the same user each receive the published frame."""
    frame = {"type": "task_deleted", "task_id": 5}
    with TestClient(app) as client:
        user_id = login_test_client(client)
        with client.websocket_connect(EVENTS_PATH, headers={"Origin": WS_ORIGIN}) as ws_1:
            with client.websocket_connect(EVENTS_PATH, headers={"Origin": WS_ORIGIN}) as ws_2:
                assert _wait_for_subscribers(client, user_id, 2) == 2
                client.portal.call(_publish, user_id, frame)
                assert ws_1.receive_json() == frame
                assert ws_2.receive_json() == frame


async def test_hub_publish_delivers_to_all_subscribers_of_user_only() -> None:
    """publish reaches every queue of the target user and no other user's queue."""
    event_hub = EventHub()
    first = event_hub.subscribe(1)
    second = event_hub.subscribe(1)
    other = event_hub.subscribe(2)
    frame = {"type": "task_deleted", "task_id": 3}

    event_hub.publish(1, frame)

    assert first.get_nowait() == frame
    assert second.get_nowait() == frame
    assert other.empty()


async def test_hub_publish_drops_oldest_when_queue_full() -> None:
    """A full queue loses its oldest frame and keeps the newest; publish never raises."""
    event_hub = EventHub()
    queue = event_hub.subscribe(1)
    for index in range(EVENTS_QUEUE_MAXSIZE):
        event_hub.publish(1, {"type": "task_deleted", "task_id": index})
    assert queue.full()

    event_hub.publish(1, {"type": "task_deleted", "task_id": 1000})

    frames = [queue.get_nowait() for _ in range(queue.qsize())]
    assert len(frames) == EVENTS_QUEUE_MAXSIZE
    assert frames[0]["task_id"] == 1
    assert frames[-1]["task_id"] == 1000


async def test_hub_publish_without_subscribers_is_noop() -> None:
    """Publishing for a user with no subscribers does nothing and does not raise."""
    event_hub = EventHub()

    event_hub.publish(42, {"type": "task_deleted", "task_id": 1})

    assert event_hub.subscriber_count(42) == 0


async def test_hub_unsubscribe_last_queue_removes_user() -> None:
    """Unsubscribing the final queue drops the user key; unknown queues are ignored."""
    event_hub = EventHub()
    queue = event_hub.subscribe(1)
    other_queue = event_hub.subscribe(1)
    assert event_hub.subscriber_count(1) == 2

    event_hub.unsubscribe(1, queue)
    assert event_hub.subscriber_count(1) == 1
    event_hub.unsubscribe(1, other_queue)
    assert event_hub.subscriber_count(1) == 0

    event_hub.unsubscribe(1, queue)
    event_hub.unsubscribe(99, queue)
    assert event_hub.subscriber_count(1) == 0


async def _delete_sessions(user_id: int) -> None:
    async with async_session_factory() as db:
        await db.exec(delete(SessionRow).where(SessionRow.user_id == user_id))
        await db.commit()


async def _expire_sessions(user_id: int) -> None:
    async with async_session_factory() as db:
        rows = (await db.exec(select(SessionRow).where(SessionRow.user_id == user_id))).all()
        for row in rows:
            row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.add(row)
        await db.commit()


async def _session_expiries(user_id: int) -> list[datetime]:
    async with async_session_factory() as db:
        rows = (await db.exec(select(SessionRow).where(SessionRow.user_id == user_id))).all()
        return [row.expires_at for row in rows]


@pytest.fixture
def fast_recheck(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the events socket re-validate its session every 50 ms."""
    monkeypatch.setattr("agent.events.EVENTS_RECEIVE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("agent.events.EVENTS_SESSION_RECHECK_SECONDS", 0.05)


def _receive_until_close(ws: Any) -> int:
    """Return the close code of the next websocket.close message."""
    while True:
        message = ws.receive()
        if message["type"] == "websocket.close":
            return int(message["code"])


@pytest.mark.parametrize("mutate", [_delete_sessions, _expire_sessions], ids=["deleted", "expired"])
def test_events_ws_closes_1008_when_session_gone(fast_recheck: None, mutate: Any) -> None:
    """A deleted or expired session closes the open socket with 1008 and unsubscribes."""
    with TestClient(app) as client:
        user_id = login_test_client(client)
        with client.websocket_connect(EVENTS_PATH, headers={"Origin": WS_ORIGIN}) as ws:
            assert _wait_for_subscribers(client, user_id, 1) == 1
            client.portal.call(mutate, user_id)
            assert _receive_until_close(ws) == 1008
        assert _wait_for_no_subscribers(client, user_id) == 0


def test_events_ws_binary_frame_closes_1003(fast_recheck: None) -> None:
    """A binary frame closes the socket with 1003 instead of crashing the handler."""
    with TestClient(app) as client:
        user_id = login_test_client(client)
        with client.websocket_connect(EVENTS_PATH, headers={"Origin": WS_ORIGIN}) as ws:
            assert _wait_for_subscribers(client, user_id, 1) == 1
            ws.send_bytes(b"\x00")
            assert _receive_until_close(ws) == 1003
        assert _wait_for_no_subscribers(client, user_id) == 0


def test_events_ws_recheck_keeps_valid_socket_and_does_not_slide_expiry(
    fast_recheck: None,
) -> None:
    """While the session is valid pings and frames work and expires_at is never extended."""
    frame = {"type": "task_deleted", "task_id": 11}
    with TestClient(app) as client:
        user_id = login_test_client(client)
        with client.websocket_connect(EVENTS_PATH, headers={"Origin": WS_ORIGIN}) as ws:
            assert _wait_for_subscribers(client, user_id, 1) == 1
            # The handshake itself slides expiry; only later rechecks must not.
            before = client.portal.call(_session_expiries, user_id)
            for _ in range(4):
                ws.send_text("ping")
                time.sleep(0.08)
            client.portal.call(_publish, user_id, frame)
            assert ws.receive_json() == frame
            after = client.portal.call(_session_expiries, user_id)
        assert after == before
