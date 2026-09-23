"""Logout closes a user's MCP sessions only when their last live web session ends."""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent import mcp_client
from shared.auth import SESSION_COOKIE_NAME, generate_session_token, hash_session_token
from shared.database import async_session_factory
from shared.models import Session as SessionRow

FIXTURE: str = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")
SERVERS: str = "/api/v1/mcp/servers"
LOGOUT: str = "/api/v1/auth/logout"
PROCESS_EXIT_TIMEOUT: float = 5.0


def _own_child_pids() -> set[int]:
    """Return PIDs of the pytest process's descendants (read-only)."""
    return {child.pid for child in psutil.Process().children(recursive=True)}


def _new_fixture_children(before: set[int]) -> list[int]:
    """Return PIDs of fixture-server descendants of pytest that were not present in `before`."""
    found: list[int] = []
    for child in psutil.Process().children(recursive=True):
        if child.pid in before:
            continue
        try:
            cmdline: list[str] = child.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if FIXTURE in cmdline:
            found.append(child.pid)
    return found


async def _wait_no_new_fixture_children(before: set[int]) -> list[int]:
    """Poll until the fixture children spawned since `before` are gone, or time out."""
    deadline: float = asyncio.get_running_loop().time() + PROCESS_EXIT_TIMEOUT
    remaining: list[int] = _new_fixture_children(before)
    while remaining and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.1)
        remaining = _new_fixture_children(before)
    return remaining


async def _connect_fixture_server(client: AsyncClient) -> int:
    """Create a fixture MCP server for the client's user, connect it, and return its id."""
    body = {
        "name": "fixture",
        "command": sys.executable,
        "args": [FIXTURE, "ok"],
        "env": {},
        "cwd": None,
        "enabled": True,
    }
    created = await client.post(SERVERS, json=body)
    assert created.status_code == 201, created.text
    server_id: int = created.json()["id"]
    connected = await client.post(f"{SERVERS}/{server_id}/connect")
    assert connected.json()["connection"]["status"] == "connected", connected.text
    assert mcp_client.is_connected(client.seeded_user_id, server_id)
    return server_id


async def _add_web_session(user_id: int, expires_at: datetime) -> None:
    """Insert an extra web session row for the user directly in the DB."""
    async with async_session_factory() as session:
        session.add(
            SessionRow(
                token_hash=hash_session_token(generate_session_token()),
                user_id=user_id,
                expires_at=expires_at,
            ),
        )
        await session.commit()


def _user_keys(user_id: int) -> list[tuple[int, int]]:
    """Return registry keys belonging to the user."""
    return [key for key in mcp_client._sessions if key[0] == user_id]


async def test_logout_of_last_live_session_closes_mcp_sessions(
    authenticated_client: AsyncClient,
) -> None:
    """The last live web session ending closes the user's MCP session and child process."""
    user_id: int = authenticated_client.seeded_user_id
    before = _own_child_pids()
    server_id = await _connect_fixture_server(authenticated_client)
    assert _new_fixture_children(before)
    # An expired extra session must not count as a live one.
    await _add_web_session(user_id, datetime.now(timezone.utc) - timedelta(days=1))

    resp = await authenticated_client.post(LOGOUT)

    assert resp.status_code == 204
    assert not mcp_client.is_connected(user_id, server_id)
    assert _user_keys(user_id) == []
    assert await _wait_no_new_fixture_children(before) == []


async def test_logout_keeps_mcp_sessions_while_another_web_session_is_live(
    authenticated_client: AsyncClient,
) -> None:
    """Another non-expired web session of the same user keeps the MCP session alive."""
    user_id: int = authenticated_client.seeded_user_id
    server_id = await _connect_fixture_server(authenticated_client)
    await _add_web_session(user_id, datetime.now(timezone.utc) + timedelta(days=1))

    resp = await authenticated_client.post(LOGOUT)

    assert resp.status_code == 204
    assert mcp_client.is_connected(user_id, server_id)


async def test_logout_never_touches_other_users_mcp_sessions(
    authenticated_client: AsyncClient,
    second_authenticated_client: AsyncClient,
) -> None:
    """Closing one user's sessions leaves another user's connected server running."""
    await _connect_fixture_server(authenticated_client)
    other_id: int = second_authenticated_client.seeded_user_id
    other_server_id = await _connect_fixture_server(second_authenticated_client)

    resp = await authenticated_client.post(LOGOUT)

    assert resp.status_code == 204
    assert _user_keys(authenticated_client.seeded_user_id) == []
    assert mcp_client.is_connected(other_id, other_server_id)


async def test_logout_succeeds_when_mcp_cleanup_fails(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing MCP cleanup never breaks logout: 204, cookie cleared, session row deleted."""
    user_id: int = authenticated_client.seeded_user_id

    async def _boom(_user_id: int) -> None:
        raise RuntimeError("cleanup exploded")

    monkeypatch.setattr(mcp_client, "cleanup_user_sessions", _boom)

    resp = await authenticated_client.post(LOGOUT)

    assert resp.status_code == 204
    assert SESSION_COOKIE_NAME in resp.headers["set-cookie"]
    async with async_session_factory() as session:
        remaining = (
            await session.exec(select(SessionRow).where(SessionRow.user_id == user_id))
        ).all()
    assert remaining == []
