"""End-to-end authentication tests: login, session cookie, logout, sliding expiry."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlmodel import select

from shared.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_DAYS,
)
from shared.database import async_session_factory
from shared.models import Session as SessionRow

INVALID_CREDENTIALS_DETAIL = "Неверное имя пользователя или пароль"


@pytest.mark.asyncio
async def test_login_success_returns_user_and_cookie(client: AsyncClient, seed_user) -> None:
    """Correct credentials return 200, the username, and an HttpOnly session cookie."""
    await seed_user("alice", "wonderland")

    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "wonderland"},
    )

    assert resp.status_code == 200
    assert resp.json()["username"] == "alice"
    set_cookie = resp.headers.get("set-cookie")
    assert set_cookie is not None
    assert SESSION_COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()


@pytest.mark.asyncio
async def test_me_returns_username_with_valid_cookie(client: AsyncClient, seed_user) -> None:
    """GET /auth/me returns the logged-in username when the session cookie is valid."""
    await seed_user("bob", "builder1")

    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "bob", "password": "builder1"},
    )
    assert login_resp.status_code == 200

    me_resp = await client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == "bob"


@pytest.mark.asyncio
async def test_me_without_cookie_returns_401(client: AsyncClient) -> None:
    """GET /auth/me with no cookie returns 401."""
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_garbage_cookie_returns_401(client: AsyncClient) -> None:
    """GET /auth/me with a garbage cookie value returns 401."""
    client.cookies.set(SESSION_COOKIE_NAME, "not-a-real-token")
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_wrong_password_returns_generic_401(client: AsyncClient, seed_user) -> None:
    """A wrong password returns 401 with the generic detail and sets no cookie."""
    await seed_user("carol", "correcthorse")

    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "carol", "password": "wrongpassword"},
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == INVALID_CREDENTIALS_DETAIL
    assert resp.headers.get("set-cookie") is None


@pytest.mark.asyncio
async def test_login_unknown_username_returns_generic_401(client: AsyncClient) -> None:
    """An unknown username returns the same generic 401 (no username enumeration)."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "ghost", "password": "whatever"},
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == INVALID_CREDENTIALS_DETAIL
    assert resp.headers.get("set-cookie") is None


@pytest.mark.asyncio
async def test_logout_revokes_session(client: AsyncClient, seed_user) -> None:
    """Logout deletes the server-side session row; the same cookie then 401s (D-04)."""
    user = await seed_user("dave", "davepass1")

    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "dave", "password": "davepass1"},
    )
    assert login_resp.status_code == 200

    logout_resp = await client.post("/api/v1/auth/logout")
    assert logout_resp.status_code == 204

    me_resp = await client.get("/api/v1/auth/me")
    assert me_resp.status_code == 401

    async with async_session_factory() as session:
        result = await session.exec(
            select(SessionRow).where(SessionRow.user_id == user.id),
        )
        assert result.all() == []


@pytest.mark.asyncio
async def test_concurrent_sessions_both_remain_valid(client: AsyncClient, seed_user) -> None:
    """Logging in twice for the same user produces two independent valid sessions (D-03)."""
    await seed_user("erin", "erinpass1")

    first_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "erin", "password": "erinpass1"},
    )
    assert first_login.status_code == 200
    first_cookie = client.cookies.get(SESSION_COOKIE_NAME)

    second_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "erin", "password": "erinpass1"},
    )
    assert second_login.status_code == 200
    second_cookie = client.cookies.get(SESSION_COOKIE_NAME)

    assert first_cookie != second_cookie

    client.cookies.set(SESSION_COOKIE_NAME, first_cookie)
    resp_first = await client.get("/api/v1/auth/me")
    assert resp_first.status_code == 200

    client.cookies.set(SESSION_COOKIE_NAME, second_cookie)
    resp_second = await client.get("/api/v1/auth/me")
    assert resp_second.status_code == 200


@pytest.mark.asyncio
async def test_sliding_expiry_extends_on_authenticated_request(
    client: AsyncClient,
    seed_user,
) -> None:
    """Session.expires_at strictly increases after each authenticated request (D-02)."""
    await seed_user("frank", "frankpass1")

    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "frank", "password": "frankpass1"},
    )
    assert login_resp.status_code == 200

    async with async_session_factory() as session:
        result = await session.exec(select(SessionRow))
        row_before = result.first()
        expires_before = row_before.expires_at
        if expires_before.tzinfo is None:
            expires_before = expires_before.replace(tzinfo=timezone.utc)

    me_resp = await client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200

    async with async_session_factory() as session:
        result = await session.exec(select(SessionRow))
        row_after = result.first()
        expires_after = row_after.expires_at
        if expires_after.tzinfo is None:
            expires_after = expires_after.replace(tzinfo=timezone.utc)

    assert expires_after > expires_before
    expected = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    assert abs((expires_after - expected).total_seconds()) < 60
