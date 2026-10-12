"""Login lockout after consecutive wrong passwords (LIM-03)."""

import inspect
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import agent.main as agent_main
import shared.models as shared_models
from agent import limits
from agent.main import app
from shared.config import settings
from shared.database import engine
from tests.test_origins import FOREIGN, LAN_HOST_HEADER, LAN_ORIGIN

LOGIN_URL = "/api/v1/auth/login"
WRONG_DETAIL = "Неверное имя пользователя или пароль"


def _client(ip: str = "127.0.0.1") -> AsyncClient:
    """Build a client whose socket peer address is the given IP."""
    transport = ASGITransport(app=app, client=(ip, 5555))
    return AsyncClient(transport=transport, base_url="http://test")


async def _login(client: AsyncClient, username: str, password: str, **kwargs: Any) -> Any:
    """Post a login request."""
    return await client.post(
        LOGIN_URL, json={"username": username, "password": password}, **kwargs
    )


@pytest.fixture
def threshold3(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block after three failures for 300 seconds."""
    monkeypatch.setattr(settings, "LOGIN_MAX_FAILED_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "LOGIN_LOCKOUT_SECONDS", 300)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace the limits clock with a mutable one-element list."""
    now = [1000.0]
    monkeypatch.setattr(limits, "_now", lambda: now[0])
    return now


@pytest.mark.asyncio
async def test_threshold_and_block(seed_user, threshold3) -> None:
    """Three wrong passwords answer 401, the fourth attempt answers 429."""
    await seed_user("alice", "wonderland")
    async with _client() as c:
        for _ in range(3):
            resp = await _login(c, "alice", "bad")
            assert resp.status_code == 401
            assert resp.json()["detail"] == WRONG_DETAIL
        assert (await _login(c, "alice", "bad")).status_code == 429


@pytest.mark.asyncio
async def test_429_contract(seed_user, threshold3, clock) -> None:
    """The 429 carries Retry-After, detail, code, retry_after and no cookie."""
    await seed_user("alice", "wonderland")
    async with _client() as c:
        for _ in range(3):
            await _login(c, "alice", "bad")
        resp = await _login(c, "alice", "bad")
    assert resp.status_code == 429
    header = resp.headers["retry-after"]
    assert header.isdigit()
    assert 1 <= int(header) <= 300
    body = resp.json()
    assert set(body) == {"detail", "code", "retry_after"}
    assert body["code"] == "LOGIN_BLOCKED"
    assert body["retry_after"] == int(header)
    assert "set-cookie" not in resp.headers


@pytest.mark.asyncio
async def test_correct_password_refused_during_block(
    seed_user, threshold3, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The right password is refused while blocked and is not even verified."""
    await seed_user("alice", "wonderland")
    async with _client() as c:
        for _ in range(3):
            await _login(c, "alice", "bad")
        calls: list[str] = []
        monkeypatch.setattr(
            agent_main, "verify_password", lambda *a, **k: calls.append("x") or True
        )
        resp = await _login(c, "alice", "wonderland")
    assert resp.status_code == 429
    assert "set-cookie" not in resp.headers
    assert calls == []


@pytest.mark.asyncio
async def test_no_username_enumeration(seed_user, threshold3, clock) -> None:
    """Existing and unknown usernames behave identically."""
    await seed_user("alice", "wonderland")
    seen: dict[str, tuple[list[int], dict[str, Any]]] = {}
    for name in ("alice", "ghost"):
        async with _client() as c:
            codes = []
            for _ in range(4):
                resp = await _login(c, name, "bad")
                codes.append(resp.status_code)
            body = dict(resp.json())
            body.pop("retry_after")
            seen[name] = (codes, body)
    assert seen["alice"] == seen["ghost"]
    assert seen["alice"][0] == [401, 401, 401, 429]


@pytest.mark.asyncio
async def test_per_username(seed_user, threshold3) -> None:
    """Blocking alice leaves bob on the same address able to log in."""
    await seed_user("alice", "wonderland")
    await seed_user("bob", "builder12")
    async with _client() as c:
        for _ in range(4):
            await _login(c, "alice", "bad")
        assert (await _login(c, "bob", "builder12")).status_code == 200


@pytest.mark.asyncio
async def test_per_client_address(seed_user, threshold3) -> None:
    """Blocking alice at one address leaves alice at another address able to log in."""
    await seed_user("alice", "wonderland")
    async with _client("10.0.0.5") as c:
        for _ in range(4):
            await _login(c, "alice", "bad")
        assert (await _login(c, "alice", "wonderland")).status_code == 429
    async with _client("10.0.0.6") as c:
        assert (await _login(c, "alice", "wonderland")).status_code == 200


@pytest.mark.asyncio
async def test_success_resets(seed_user, threshold3) -> None:
    """A successful login resets the failure counter."""
    await seed_user("alice", "wonderland")
    async with _client() as c:
        await _login(c, "alice", "bad")
        await _login(c, "alice", "bad")
        assert (await _login(c, "alice", "wonderland")).status_code == 200
        await _login(c, "alice", "bad")
        assert (await _login(c, "alice", "bad")).status_code == 401


@pytest.mark.asyncio
async def test_expiry(seed_user, threshold3, clock) -> None:
    """After the block ends the right password works; one wrong one answers 401."""
    await seed_user("alice", "wonderland")
    async with _client() as c:
        for _ in range(4):
            await _login(c, "alice", "bad")
        clock[0] += 301
        assert (await _login(c, "alice", "wonderland")).status_code == 200
    async with _client("10.0.0.9") as c:
        for _ in range(4):
            await _login(c, "alice", "bad")
        clock[0] += 301
        assert (await _login(c, "alice", "bad")).status_code == 401


@pytest.mark.asyncio
async def test_remaining_time_shrinks(seed_user, threshold3, clock) -> None:
    """Retry-After reports the time left, not the full duration."""
    await seed_user("alice", "wonderland")
    async with _client() as c:
        for _ in range(3):
            await _login(c, "alice", "bad")
        clock[0] += 100
        resp = await _login(c, "alice", "bad")
    assert resp.headers["retry-after"] == "200"


@pytest.mark.asyncio
async def test_threshold_zero_disables(seed_user, monkeypatch: pytest.MonkeyPatch) -> None:
    """A threshold of 0 switches the lockout off."""
    monkeypatch.setattr(settings, "LOGIN_MAX_FAILED_ATTEMPTS", 0)
    await seed_user("alice", "wonderland")
    async with _client() as c:
        for _ in range(10):
            assert (await _login(c, "alice", "bad")).status_code == 401


@pytest.mark.asyncio
async def test_default_values(seed_user, clock) -> None:
    """Defaults: five failures answer 401, the sixth attempt gets 429 with 300 seconds."""
    await seed_user("alice", "wonderland")
    async with _client() as c:
        for _ in range(5):
            assert (await _login(c, "alice", "bad")).status_code == 401
        resp = await _login(c, "alice", "bad")
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "300"


@pytest.mark.asyncio
async def test_forwarded_header_ignored(seed_user, threshold3) -> None:
    """X-Forwarded-For does not change the counted address."""
    await seed_user("alice", "wonderland")
    async with _client() as c:
        for i in range(3):
            await _login(c, "alice", "bad", headers={"X-Forwarded-For": f"9.9.9.{i}"})
        resp = await _login(c, "alice", "bad", headers={"X-Forwarded-For": "8.8.8.8"})
    assert resp.status_code == 429
    assert "x-forwarded-for" not in inspect.getsource(agent_main).lower()


@pytest.mark.asyncio
async def test_nothing_persisted(seed_user, threshold3) -> None:
    """A block adds no database row and no model."""
    await seed_user("alice", "wonderland")
    async with _client() as c:
        for _ in range(4):
            await _login(c, "alice", "bad")
    async with engine.connect() as conn:
        tables = [
            r[0]
            for r in await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        ]
        for table in tables:
            if table.startswith("sqlite_"):
                continue
            count = (await conn.execute(text(f'SELECT COUNT(*) FROM "{table}"'))).scalar()
            if table == "user":
                assert count == 1
            elif table in ("session",):
                assert count == 0
    names = [n for n in dir(shared_models) if isinstance(getattr(shared_models, n), type)]
    assert not [n for n in names if "Lockout" in n or "LoginAttempt" in n]


@pytest.mark.asyncio
async def test_cors_exposes_retry_after(seed_user, threshold3) -> None:
    """An allowed origin can read Retry-After; a foreign one gets no CORS grant."""
    await seed_user("alice", "wonderland")
    async with _client() as c:
        for _ in range(3):
            await _login(c, "alice", "bad")
        ok = await _login(
            c, "alice", "bad", headers={"Host": LAN_HOST_HEADER, "Origin": LAN_ORIGIN}
        )
        foreign = await _login(
            c, "alice", "bad", headers={"Host": LAN_HOST_HEADER, "Origin": FOREIGN}
        )
    assert ok.status_code == 429
    assert ok.headers["access-control-allow-origin"] == LAN_ORIGIN
    assert "Retry-After" in ok.headers["access-control-expose-headers"]
    assert "access-control-allow-origin" not in foreign.headers


@pytest.mark.asyncio
async def test_limits_in_force_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent start logs the four effective limits once."""
    calls: list[tuple[str, dict[str, Any]]] = []

    class _Rec:
        def info(self, event: str, **kw: Any) -> None:
            calls.append((event, kw))

        def __getattr__(self, name: str) -> Any:
            return lambda *a, **k: None

    monkeypatch.setattr(agent_main, "logger", _Rec())
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", False)
    async with app.router.lifespan_context(app):
        pass
    found = [kw for ev, kw in calls if ev == "limits_in_force"]
    assert len(found) == 1
    assert found[0] == {
        "rate_limit_per_minute": 30,
        "login_max_failed_attempts": 5,
        "login_lockout_seconds": 300,
        "max_concurrent_generations": 2,
    }


@pytest.mark.asyncio
async def test_no_route_exposes_limits(authenticated_client: AsyncClient) -> None:
    """No path or network field reveals the limits."""
    assert not [p for p in app.openapi()["paths"] if "limit" in p]
    resp = await authenticated_client.get("/api/v1/network")
    assert resp.status_code == 200
    for key in resp.json():
        assert not any(w in key for w in ("limit", "lockout", "concurrent"))


@pytest.mark.asyncio
async def test_slow_guessing_still_blocked(seed_user, threshold3, clock) -> None:
    """Failures spaced wider than the lockout time still reach the block."""
    await seed_user("alice", "wonderland")
    async with _client() as c:
        for t in (0, 400, 800):
            clock[0] = 1000.0 + t
            assert (await _login(c, "alice", "bad")).status_code == 401
        resp = await _login(c, "alice", "bad")
        assert resp.status_code == 429
        assert resp.headers["retry-after"] == "300"
        clock[0] += 301
        assert (await _login(c, "alice", "bad")).status_code == 401
        assert (await _login(c, "alice", "wonderland")).status_code == 200


@pytest.mark.asyncio
async def test_parallel_guesses_do_not_bypass_lockout(seed_user, threshold3) -> None:
    """A concurrent burst gets at most the threshold of 401 answers; the rest get 429."""
    import asyncio

    await seed_user("alice", "wonderland")
    async with _client() as c:
        resps = await asyncio.gather(*[_login(c, "alice", "bad") for _ in range(20)])
    codes = [r.status_code for r in resps]
    assert codes.count(401) <= 3
    assert codes.count(429) == 17


@pytest.mark.asyncio
async def test_address_cannot_flood_throttle_table(threshold3) -> None:
    """One address holding the maximum of pairs gets new pairs refused."""
    throttle = limits.LoginThrottle()
    for i in range(throttle.MAX_PAIRS_PER_ADDRESS):
        assert throttle.register_attempt((f"u{i}", "10.0.0.1"))[0] is None
    assert throttle.register_attempt(("extra", "10.0.0.1"))[0] is not None
    assert throttle.register_attempt(("extra", "10.0.0.2"))[0] is None
    assert throttle.register_attempt(("u0", "10.0.0.1"))[0] is None
