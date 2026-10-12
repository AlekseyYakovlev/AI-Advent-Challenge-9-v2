"""Bearer API key authentication tests: shared auth seam, no cookie fallback, throttle, WebSocket."""

import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient
from sqlmodel import select
from starlette.testclient import TestClient

from agent.main import app
from shared.database import async_session_factory
from shared.models import ApiKey
from tests.conftest import login_test_client

KEYS: str = "/api/v1/api-keys"
MCP: str = "/api/v1/mcp/servers"
WS_ORIGIN: str = "http://localhost:8000"


def _bearer(key: str) -> dict[str, str]:
    """Build the Authorization header for a key."""
    return {"Authorization": f"Bearer {key}"}


async def _make_key(client: AsyncClient) -> dict[str, Any]:
    """Create a key through the cookie client."""
    resp = await client.post(KEYS, json={"name": "bearer-test"})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _stored_last_used(key_id: int) -> datetime | None:
    """Read last_used_at of a key row straight from the database."""
    async with async_session_factory() as session:
        row = await session.get(ApiKey, key_id)
        return row.last_used_at


async def test_key_authenticates_me_and_chats(
    authenticated_client: AsyncClient,
    client: AsyncClient,
) -> None:
    """A cookie-less client with a valid key acts as the key owner."""
    chat = (await authenticated_client.post("/api/v1/chats", json={"title": "mine"})).json()
    key = (await _make_key(authenticated_client))["key"]

    me = await client.get("/api/v1/auth/me", headers=_bearer(key))
    assert me.status_code == 200
    assert me.json()["username"] == "testuser"

    chats = await client.get("/api/v1/chats", headers=_bearer(key))
    assert chat["id"] in [c["id"] for c in chats.json()]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/chats",
        "/api/v1/settings",
        "/api/v1/kb",
        "/api/v1/mcp/servers",
        "/api/v1/llm-providers",
        "/api/v1/network",
        "/api/v1/scheduler/tasks",
    ],
)
async def test_key_reads_existing_get_routes(
    authenticated_client: AsyncClient,
    client: AsyncClient,
    path: str,
) -> None:
    """Existing GET routes accept a key through the same dependency as the cookie."""
    key = (await _make_key(authenticated_client))["key"]
    resp = await client.get(path, headers=_bearer(key))
    assert resp.status_code == 200, resp.text


async def test_key_can_create_chat_and_toggle_mcp_server(
    authenticated_client: AsyncClient,
    client: AsyncClient,
) -> None:
    """Mutating routes work with a key alone (chat create, MCP server enable flag)."""
    key = (await _make_key(authenticated_client))["key"]
    headers = _bearer(key)

    created = await client.post("/api/v1/chats", json={"title": "via key"}, headers=headers)
    assert created.status_code == 201

    server = await authenticated_client.post(
        MCP,
        json={
            "name": "fixture",
            "command": sys.executable,
            "args": ["-c", "pass"],
            "env": {},
            "cwd": None,
            "enabled": True,
        },
    )
    assert server.status_code == 201, server.text
    server_id = server.json()["id"]

    updated = await client.put(f"{MCP}/{server_id}", json={"enabled": False}, headers=headers)
    assert updated.status_code == 200
    listed = (await client.get(MCP, headers=headers)).json()
    assert [s["enabled"] for s in listed if s["id"] == server_id] == [False]


async def test_key_ownership_matches_cookie(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
    client: AsyncClient,
) -> None:
    """A key cannot read another user's chat: the answer is 404, as with the cookie."""
    chat = (await second_authenticated_client.post("/api/v1/chats", json={"title": "x"})).json()
    key = (await _make_key(authenticated_client))["key"]
    resp = await client.get(f"/api/v1/chats/{chat['id']}/tree", headers=_bearer(key))
    assert resp.status_code == 404


async def test_invalid_keys_give_401_without_echo(
    authenticated_client: AsyncClient,
    client: AsyncClient,
) -> None:
    """Random, tampered and empty keys are 401 with WWW-Authenticate and no token echo."""
    key = (await _make_key(authenticated_client))["key"]
    tampered = key[:-1] + ("A" if key[-1] != "A" else "B")
    random_key = "aia_" + "z" * 43

    for token in (random_key, tampered):
        resp = await client.get("/api/v1/chats", headers=_bearer(token))
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"].lower() == "bearer"
        assert token not in resp.text

    empty = await client.get("/api/v1/chats", headers={"Authorization": "Bearer"})
    assert empty.status_code == 401
    assert empty.headers["www-authenticate"].lower() == "bearer"


async def test_no_cookie_fallback_when_header_present(
    authenticated_client: AsyncClient,
) -> None:
    """With a Bearer header the key decides alone, even beside a valid cookie."""
    created = await _make_key(authenticated_client)
    bad = await authenticated_client.get("/api/v1/chats", headers=_bearer("aia_" + "q" * 43))
    assert bad.status_code == 401

    assert (await authenticated_client.delete(f"{KEYS}/{created['id']}")).status_code == 204
    revoked = await authenticated_client.get("/api/v1/chats", headers=_bearer(created["key"]))
    assert revoked.status_code == 401

    assert (await authenticated_client.get("/api/v1/chats")).status_code == 200


async def test_scheme_handling(
    authenticated_client: AsyncClient,
    client: AsyncClient,
) -> None:
    """A non-Bearer scheme is ignored; the Bearer scheme is case-insensitive."""
    key = (await _make_key(authenticated_client))["key"]
    basic = {"Authorization": "Basic abc"}
    assert (await authenticated_client.get("/api/v1/chats", headers=basic)).status_code == 200
    assert (await client.get("/api/v1/chats", headers=basic)).status_code == 401
    lower = {"authorization": f"bearer {key}"}
    assert (await client.get("/api/v1/chats", headers=lower)).status_code == 200


async def test_last_used_at_is_throttled(
    authenticated_client: AsyncClient,
    client: AsyncClient,
) -> None:
    """last_used_at is set on first use, not rewritten within 60s, refreshed after."""
    created = await _make_key(authenticated_client)
    key_id, headers = created["id"], _bearer(created["key"])
    assert created["last_used_at"] is None

    await client.get("/api/v1/chats", headers=headers)
    first = await _stored_last_used(key_id)
    assert first is not None

    await client.get("/api/v1/chats", headers=headers)
    assert await _stored_last_used(key_id) == first

    async with async_session_factory() as session:
        row = (await session.exec(select(ApiKey).where(ApiKey.id == key_id))).one()
        row.last_used_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        session.add(row)
        await session.commit()
    old = await _stored_last_used(key_id)

    await client.get("/api/v1/chats", headers=headers)
    refreshed = await _stored_last_used(key_id)
    assert refreshed is not None and refreshed > old

    listed = (await authenticated_client.get(KEYS)).json()
    assert listed[0]["last_used_at"] is not None


def test_websocket_stays_cookie_only() -> None:
    """A key alone cannot open /ws/chat or /ws/events: both close with 1008."""
    with TestClient(app) as client:
        login_test_client(client)
        key = client.post(KEYS, json={"name": "ws"}).json()["key"]
        chat_id = client.post("/api/v1/chats", json={"title": "ws"}).json()["id"]
        client.cookies.clear()

        headers = {"Origin": WS_ORIGIN, "Authorization": f"Bearer {key}"}
        for path in (f"/ws/chat/{chat_id}", "/ws/events"):
            with pytest.raises(Exception) as exc_info:
                with client.websocket_connect(path, headers=headers) as ws:
                    ws.receive_json()
            assert getattr(exc_info.value, "code", None) == 1008
