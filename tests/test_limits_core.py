"""Unit tests for the in-memory limit primitives."""

import asyncio

import pytest

from agent import limits
from agent import state as agent_state
from agent.limits import (
    LoginThrottle,
    check_message_rate,
    generation_gate,
    limits_in_force,
    login_throttle,
)
from shared.config import settings


class Clock:
    """A controllable replacement for limits._now."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Replace the limits time source with an advanceable clock."""
    c = Clock()
    monkeypatch.setattr(limits, "_now", c)
    return c


def _set(monkeypatch: pytest.MonkeyPatch, name: str, value: int) -> None:
    monkeypatch.setattr(settings, name, value)


# ---------------------------------------------------------------- rate window


def test_rate_window_blocks_after_limit(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    """The fourth call under a limit of 3 is refused with 1..60 seconds to wait."""
    _set(monkeypatch, "RATE_LIMIT_MESSAGES_PER_MINUTE", 3)
    assert [check_message_rate(1) for _ in range(3)] == [None, None, None]
    wait = check_message_rate(1)
    assert isinstance(wait, int)
    assert 1 <= wait <= 60


def test_rate_window_reports_exact_wait(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    """With stamps at 0, 10, 20 a call at 30 must wait exactly 30 seconds."""
    _set(monkeypatch, "RATE_LIMIT_MESSAGES_PER_MINUTE", 3)
    for _ in range(3):
        assert check_message_rate(1) is None
        clock.advance(10)
    assert check_message_rate(1) == 30


def test_rate_window_frees_after_a_minute(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    """After more than 60 seconds the chat may send again."""
    _set(monkeypatch, "RATE_LIMIT_MESSAGES_PER_MINUTE", 2)
    assert check_message_rate(1) is None
    assert check_message_rate(1) is None
    assert check_message_rate(1) is not None
    clock.advance(61)
    assert check_message_rate(1) is None


def test_refused_call_records_nothing(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    """A refused call does not grow the stored window."""
    _set(monkeypatch, "RATE_LIMIT_MESSAGES_PER_MINUTE", 2)
    check_message_rate(1)
    check_message_rate(1)
    for _ in range(5):
        assert check_message_rate(1) is not None
    assert len(agent_state.ws_rate_limiter[1]) == 2


def test_rate_window_chats_are_independent(
    monkeypatch: pytest.MonkeyPatch, clock: Clock
) -> None:
    """One chat reaching the limit does not affect another."""
    _set(monkeypatch, "RATE_LIMIT_MESSAGES_PER_MINUTE", 1)
    assert check_message_rate(1) is None
    assert check_message_rate(1) is not None
    assert check_message_rate(2) is None


def test_rate_limit_zero_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Limit 0 allows everything and stores nothing."""
    _set(monkeypatch, "RATE_LIMIT_MESSAGES_PER_MINUTE", 0)
    assert all(check_message_rate(7) is None for _ in range(100))
    assert 7 not in agent_state.ws_rate_limiter


def test_cleanup_chat_caches_empties_window(
    monkeypatch: pytest.MonkeyPatch, clock: Clock
) -> None:
    """Deleting a chat drops its message window."""
    _set(monkeypatch, "RATE_LIMIT_MESSAGES_PER_MINUTE", 3)
    check_message_rate(5)
    assert 5 in agent_state.ws_rate_limiter
    agent_state.cleanup_chat_caches(5)
    assert 5 not in agent_state.ws_rate_limiter


# -------------------------------------------------------------- login throttle


@pytest.fixture
def throttle_cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Threshold 3, lockout 300."""
    _set(monkeypatch, "LOGIN_MAX_FAILED_ATTEMPTS", 3)
    _set(monkeypatch, "LOGIN_LOCKOUT_SECONDS", 300)


KEY = ("alice", "10.0.0.1")


def test_third_failure_blocks(throttle_cfg: None, clock: Clock) -> None:
    """Two failures leave the key free; the third starts a 300 second block."""
    assert login_throttle.record_failure(KEY) is False
    assert login_throttle.record_failure(KEY) is False
    assert login_throttle.blocked_seconds(KEY) is None
    assert login_throttle.record_failure(KEY) is True
    assert login_throttle.blocked_seconds(KEY) == 300


def test_block_time_shrinks_and_expires(throttle_cfg: None, clock: Clock) -> None:
    """Remaining time shrinks with the clock, stays at least 1, then the key is free."""
    for _ in range(3):
        login_throttle.record_failure(KEY)
    clock.advance(100)
    assert login_throttle.blocked_seconds(KEY) == 200
    clock.advance(199.5)
    assert login_throttle.blocked_seconds(KEY) == 1
    clock.advance(2)
    assert login_throttle.blocked_seconds(KEY) is None
    assert login_throttle.record_failure(KEY) is False


def test_reset_clears_the_count(throttle_cfg: None, clock: Clock) -> None:
    """A success after two failures means two more failures still do not block."""
    login_throttle.record_failure(KEY)
    login_throttle.record_failure(KEY)
    login_throttle.reset(KEY)
    assert login_throttle.record_failure(KEY) is False
    assert login_throttle.record_failure(KEY) is False
    assert login_throttle.blocked_seconds(KEY) is None


def test_pairs_are_independent(throttle_cfg: None, clock: Clock) -> None:
    """A block on one (username, IP) pair leaves other pairs free."""
    for _ in range(3):
        login_throttle.record_failure(KEY)
    assert login_throttle.blocked_seconds(KEY) == 300
    assert login_throttle.blocked_seconds(("alice", "10.0.0.2")) is None
    assert login_throttle.blocked_seconds(("bob", "10.0.0.1")) is None


def test_threshold_zero_never_blocks(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    """Threshold 0 switches the lockout off and stores nothing."""
    _set(monkeypatch, "LOGIN_MAX_FAILED_ATTEMPTS", 0)
    for _ in range(20):
        assert login_throttle.record_failure(KEY) is False
    assert login_throttle.blocked_seconds(KEY) is None
    assert login_throttle._entries == {}


def test_slow_guessing_reaches_the_block(throttle_cfg: None, clock: Clock) -> None:
    """Failures spaced further apart than the lockout time still add up."""
    assert login_throttle.record_failure(KEY) is False
    clock.advance(400)
    assert login_throttle.record_failure(KEY) is False
    clock.advance(400)
    assert login_throttle.record_failure(KEY) is True
    assert login_throttle.blocked_seconds(KEY) == 300


def test_count_restarts_after_expired_block(throttle_cfg: None, clock: Clock) -> None:
    """After a block has expired the count starts from zero."""
    for _ in range(3):
        login_throttle.record_failure(KEY)
    clock.advance(301)
    assert login_throttle.record_failure(KEY) is False
    assert login_throttle.record_failure(KEY) is False
    assert login_throttle.record_failure(KEY) is True


def test_eviction_drops_oldest_failures(
    monkeypatch: pytest.MonkeyPatch, throttle_cfg: None, clock: Clock
) -> None:
    """Over MAX_ENTRIES the oldest unblocked entries go; the newest key stays."""
    monkeypatch.setattr(LoginThrottle, "MAX_ENTRIES", 5)
    keys = [(f"u{i}", "1.1.1.1") for i in range(8)]
    for k in keys:
        login_throttle.record_failure(k)
        clock.advance(1)
    assert len(login_throttle._entries) == 5
    for k in keys[:3]:
        assert k not in login_throttle._entries
    assert keys[-1] in login_throttle._entries


def test_eviction_spares_blocked_key(
    monkeypatch: pytest.MonkeyPatch, throttle_cfg: None, clock: Clock
) -> None:
    """A blocked key survives eviction while unblocked entries exist."""
    monkeypatch.setattr(LoginThrottle, "MAX_ENTRIES", 5)
    for _ in range(3):
        login_throttle.record_failure(KEY)
    for i in range(8):
        clock.advance(1)
        login_throttle.record_failure((f"x{i}", "2.2.2.2"))
    assert isinstance(login_throttle.blocked_seconds(KEY), int)


def test_expired_block_removed_by_later_failure(throttle_cfg: None, clock: Clock) -> None:
    """An entry with an expired block is removed when another key records a failure."""
    for _ in range(3):
        login_throttle.record_failure(KEY)
    clock.advance(301)
    login_throttle.record_failure(("bob", "3.3.3.3"))
    assert KEY not in login_throttle._entries


# ------------------------------------------------------------ generation gate


async def _wait_until(predicate, timeout: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    """Poll a condition on the event loop until it holds."""
    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(_poll(), timeout)


async def test_gate_cap_one_serialises_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """With cap 1 turns never overlap, queue in arrival order and report their wait."""
    _set(monkeypatch, "MAX_CONCURRENT_GENERATIONS", 1)
    assert generation_gate.waiting == 0
    release = [asyncio.Event() for _ in range(3)]
    order: list[int] = []
    waits: dict[int, float] = {}
    on_wait_calls: dict[int, int] = {0: 0, 1: 0, 2: 0}
    active = 0
    max_active = 0

    async def turn(i: int) -> None:
        nonlocal active, max_active

        async def on_wait() -> None:
            on_wait_calls[i] += 1

        async with generation_gate.slot(on_wait) as waited:
            active += 1
            max_active = max(max_active, active)
            waits[i] = waited
            order.append(i)
            await release[i].wait()
            active -= 1

    tasks = [asyncio.create_task(turn(0))]
    await _wait_until(lambda: order == [0])
    tasks.append(asyncio.create_task(turn(1)))
    await _wait_until(lambda: generation_gate.waiting == 1)
    tasks.append(asyncio.create_task(turn(2)))
    await _wait_until(lambda: generation_gate.waiting == 2)
    for ev in release:
        ev.set()
        await asyncio.sleep(0)
    await asyncio.gather(*tasks)
    assert order == [0, 1, 2]
    assert max_active == 1
    assert waits[0] == 0.0
    assert on_wait_calls == {0: 0, 1: 1, 2: 1}
    assert waits[1] >= 0.0 and waits[2] >= 0.0
    assert generation_gate.waiting == 0


async def test_gate_cap_zero_is_uncapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """With cap 0 three turns run together and on_wait is never called."""
    _set(monkeypatch, "MAX_CONCURRENT_GENERATIONS", 0)
    release = asyncio.Event()
    inside = 0
    called = 0

    async def on_wait() -> None:
        nonlocal called
        called += 1

    async def turn() -> None:
        nonlocal inside
        async with generation_gate.slot(on_wait) as waited:
            assert waited == 0.0
            inside += 1
            await release.wait()

    tasks = [asyncio.create_task(turn()) for _ in range(3)]
    await _wait_until(lambda: inside == 3)
    assert generation_gate.waiting == 0
    release.set()
    await asyncio.gather(*tasks)
    assert called == 0


async def test_gate_cancelled_waiter_leaves_gate_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling a waiter drops the count and holds nothing."""
    _set(monkeypatch, "MAX_CONCURRENT_GENERATIONS", 1)
    release = asyncio.Event()
    holding = asyncio.Event()

    async def holder() -> None:
        async with generation_gate.slot():
            holding.set()
            await release.wait()

    async def waiter() -> None:
        async with generation_gate.slot():
            pass

    h = asyncio.create_task(holder())
    await holding.wait()
    w = asyncio.create_task(waiter())
    await _wait_until(lambda: generation_gate.waiting == 1)
    w.cancel()
    with pytest.raises(asyncio.CancelledError):
        await w
    assert generation_gate.waiting == 0
    release.set()
    await h
    async with generation_gate.slot() as waited:
        assert waited == 0.0


async def test_gate_body_exception_frees_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A body that raises still releases its slot."""
    _set(monkeypatch, "MAX_CONCURRENT_GENERATIONS", 1)
    with pytest.raises(RuntimeError):
        async with generation_gate.slot():
            raise RuntimeError("boom")
    async with generation_gate.slot() as waited:
        assert waited == 0.0


async def test_gate_cap_change_takes_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raising the cap from 1 to 2 between uses gives two concurrent holders."""
    _set(monkeypatch, "MAX_CONCURRENT_GENERATIONS", 1)
    async with generation_gate.slot():
        pass
    _set(monkeypatch, "MAX_CONCURRENT_GENERATIONS", 2)
    release = asyncio.Event()
    inside = 0

    async def turn() -> None:
        nonlocal inside
        async with generation_gate.slot():
            inside += 1
            await release.wait()

    tasks = [asyncio.create_task(turn()) for _ in range(2)]
    await _wait_until(lambda: inside == 2)
    release.set()
    await asyncio.gather(*tasks)


async def test_gate_waiting_is_zero_after_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """reset() brings the waiter count back to zero."""
    _set(monkeypatch, "MAX_CONCURRENT_GENERATIONS", 1)
    assert generation_gate.waiting == 0
    generation_gate.reset()
    assert generation_gate.waiting == 0


async def test_gate_wait_uses_clock(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    """Advancing the clock while two callers wait is included in the first waiter's wait."""
    _set(monkeypatch, "MAX_CONCURRENT_GENERATIONS", 1)
    release = asyncio.Event()
    holding = asyncio.Event()
    result: list[float] = []

    async def holder() -> None:
        async with generation_gate.slot():
            holding.set()
            await release.wait()

    async def waiter() -> None:
        async with generation_gate.slot() as waited:
            result.append(waited)

    h = asyncio.create_task(holder())
    await holding.wait()
    ws = [asyncio.create_task(waiter()) for _ in range(2)]
    await _wait_until(lambda: generation_gate.waiting == 2)
    clock.advance(2.0)
    release.set()
    await asyncio.gather(h, *ws)
    assert result[0] == 2.0


def _contended_run() -> None:
    """Run one event loop in which a second caller waits for the only slot."""

    async def main() -> None:
        async def first() -> None:
            async with generation_gate.slot():
                await asyncio.sleep(0.01)

        async def second() -> None:
            async with generation_gate.slot():
                pass

        t1 = asyncio.create_task(first())
        await asyncio.sleep(0)
        t2 = asyncio.create_task(second())
        await asyncio.gather(t1, t2)

    asyncio.run(main())


def test_gate_survives_new_event_loops_with_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two asyncio.run calls with an explicit reset in between do not raise."""
    _set(monkeypatch, "MAX_CONCURRENT_GENERATIONS", 1)
    _contended_run()
    generation_gate.reset()
    _contended_run()


def test_gate_survives_new_event_loops_without_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loop check alone rebuilds the semaphore for a new event loop."""
    _set(monkeypatch, "MAX_CONCURRENT_GENERATIONS", 1)
    _contended_run()
    _contended_run()


def test_limits_in_force_reports_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """limits_in_force mirrors the current settings."""
    _set(monkeypatch, "RATE_LIMIT_MESSAGES_PER_MINUTE", 7)
    _set(monkeypatch, "MAX_CONCURRENT_GENERATIONS", 0)
    got = limits_in_force()
    assert got == {
        "rate_limit_per_minute": 7,
        "login_max_failed_attempts": 5,
        "login_lockout_seconds": 300,
        "max_concurrent_generations": 0,
    }
