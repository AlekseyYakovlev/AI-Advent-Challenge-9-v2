"""Shared pytest fixtures and test environment setup."""

import os
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

os.environ.setdefault("DB_PATH", "test_app.db")

from agent import mcp_client
from agent import state as agent_state
from agent.main import app
from shared.config import settings
from shared.database import async_session_factory, engine, init_db


@pytest.fixture(autouse=True)
async def clean_test_db() -> None:
    """Remove and recreate the test database before each test.

    Also clears agent.state's in-memory dicts (chat_locks, active_streams,
    ws_rate_limiter): the DB resets chat ids back to 1 each test, but each
    `with TestClient(app):` block spins its own event loop, so a stale
    `asyncio.Lock` left in `chat_locks` from an earlier test's (now-closed)
    loop would otherwise deadlock a later test that reuses the same chat id.
    """
    db_path = Path(settings.DB_PATH)
    if db_path.exists():
        db_path.unlink()
    await init_db()
    agent_state.active_streams.clear()
    agent_state.ws_rate_limiter.clear()
    agent_state.chat_locks.clear()
    yield
    await mcp_client.cleanup_all_sessions()
    await engine.dispose()
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
async def client() -> AsyncClient:
    """Async HTTP client wired to the Agent FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _create_user(username: str, password: str) -> int:
    """Insert a hashed-password User row and return its id."""
    from shared.auth import hash_password
    from shared.models import User

    async with async_session_factory() as session:
        user = User(username=username, password_hash=hash_password(password))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


def login_test_client(
    client: TestClient,
    username: str = "wsuser",
    password: str = "wspass",
) -> int:
    """Seed a user inside the app's event loop and log the sync TestClient in."""
    user_id = client.portal.call(_create_user, username, password)
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200
    return user_id


@pytest.fixture
def seed_user() -> Callable[[str, str], Coroutine[Any, Any, Any]]:
    """Factory fixture returning an async callable that inserts a hashed-password User row."""

    async def _seed_user(username: str, password: str) -> Any:
        from shared.models import User

        user_id = await _create_user(username, password)
        async with async_session_factory() as session:
            return await session.get(User, user_id)

    return _seed_user


@pytest.fixture
async def authenticated_client(
    seed_user: Callable[[str, str], Coroutine[Any, Any, Any]],
) -> AsyncClient:
    """Async HTTP client pre-authenticated as `testuser` via a valid session cookie."""
    from shared.auth import (
        SESSION_COOKIE_NAME,
        SESSION_TTL_DAYS,
        generate_session_token,
        hash_session_token,
    )
    from shared.models import Session as SessionRow

    user = await seed_user("testuser", "testpass")
    token = generate_session_token()
    async with async_session_factory() as session:
        session_row = SessionRow(
            token_hash=hash_session_token(token),
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS),
        )
        session.add(session_row)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set(SESSION_COOKIE_NAME, token)
        ac.seeded_user_id = user.id
        yield ac


@pytest.fixture
async def second_authenticated_client(
    seed_user: Callable[[str, str], Coroutine[Any, Any, Any]],
) -> AsyncClient:
    """Async HTTP client pre-authenticated as `otheruser`, for cross-user IDOR tests."""
    from shared.auth import (
        SESSION_COOKIE_NAME,
        SESSION_TTL_DAYS,
        generate_session_token,
        hash_session_token,
    )
    from shared.models import Session as SessionRow

    user = await seed_user("otheruser", "otherpass")
    token = generate_session_token()
    async with async_session_factory() as session:
        session_row = SessionRow(
            token_hash=hash_session_token(token),
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS),
        )
        session.add(session_row)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set(SESSION_COOKIE_NAME, token)
        ac.seeded_user_id = user.id
        yield ac
