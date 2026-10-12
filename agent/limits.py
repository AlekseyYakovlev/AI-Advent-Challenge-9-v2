"""In-memory abuse limits: per-chat message rate, login lockout and the generation gate."""

import asyncio
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from agent.state import ws_rate_limiter
from shared.config import settings
from shared.logger import get_logger

logger = get_logger(__name__)

RATE_WINDOW_SECONDS: float = 60.0
CODE_RATE_LIMITED = "RATE_LIMITED"
MSG_RATE_LIMITED = "Rate limit exceeded"
CODE_LOGIN_BLOCKED = "LOGIN_BLOCKED"
MSG_LOGIN_BLOCKED = "Слишком много неудачных попыток входа. Повторите позже"


def _now() -> float:
    """Return the monotonic clock; the single time source so tests can replace it."""
    return time.monotonic()


def check_message_rate(chat_id: int) -> int | None:
    """Record a message for the chat, or return whole seconds until a slot frees.

    Synchronous on purpose: the check and the append cannot interleave with another task.
    """
    limit = settings.RATE_LIMIT_MESSAGES_PER_MINUTE
    if limit <= 0:
        return None
    now = _now()
    window = [
        ts for ts in ws_rate_limiter.get(chat_id, []) if now - ts < RATE_WINDOW_SECONDS
    ]
    ws_rate_limiter[chat_id] = window
    if len(window) >= limit:
        return max(1, math.ceil(window[0] + RATE_WINDOW_SECONDS - now))
    window.append(now)
    return None


@dataclass
class _Entry:
    """Failure state of one (username, client IP) pair."""

    failures: int
    last_failure: float
    blocked_until: float = 0.0


class LoginThrottle:
    """Counts wrong passwords per (username, client IP) and blocks a pair at the threshold."""

    MAX_ENTRIES: int = 10_000
    # Failure pairs one address may hold; beyond it new pairs are refused so a flood of
    # throwaway usernames cannot evict that address's real counters.
    MAX_PAIRS_PER_ADDRESS: int = 50

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], _Entry] = {}

    def blocked_seconds(self, key: tuple[str, str]) -> int | None:
        """Return whole seconds left of the key's block, or None when it may try."""
        if settings.LOGIN_MAX_FAILED_ATTEMPTS <= 0:
            return None
        entry = self._entries.get(key)
        if entry is None:
            return None
        remaining = entry.blocked_until - _now()
        if remaining > 0:
            return max(1, math.ceil(remaining))
        if entry.blocked_until > 0:
            del self._entries[key]
        return None

    def record_failure(self, key: tuple[str, str]) -> bool:
        """Count a wrong password; return True when this failure started a block."""
        threshold = settings.LOGIN_MAX_FAILED_ATTEMPTS
        if threshold <= 0:
            return False
        now = _now()
        entry = self._entries.get(key)
        if entry is not None and 0 < entry.blocked_until <= now:
            entry = None
        if entry is None:
            entry = _Entry(failures=0, last_failure=now)
            self._entries[key] = entry
        entry.failures += 1
        entry.last_failure = now
        started = False
        if entry.failures >= threshold:
            entry.blocked_until = now + settings.LOGIN_LOCKOUT_SECONDS
            started = True
        self._evict(key, now)
        return started

    def register_attempt(self, key: tuple[str, str]) -> tuple[int | None, bool]:
        """Return (seconds blocked, block started); count the attempt when it may proceed.

        Check and count happen in one synchronous step so concurrent requests cannot
        all pass the block check before the first failure is recorded.
        """
        blocked = self.blocked_seconds(key)
        if blocked is not None:
            return blocked, False
        if settings.LOGIN_MAX_FAILED_ATTEMPTS > 0 and key not in self._entries:
            held = sum(1 for k in self._entries if k[1] == key[1])
            if held >= self.MAX_PAIRS_PER_ADDRESS:
                return max(1, settings.LOGIN_LOCKOUT_SECONDS), False
        return None, self.record_failure(key)

    # Memory bound. Step 1 drops entries whose block has expired (no information lost).
    # Step 2 drops the unblocked entry with the oldest failure while the table is over
    # MAX_ENTRIES (never the key just recorded); only if all others are blocked, the
    # block that ends first. A pair evicted in step 2 loses its failure count and starts
    # again from zero; this happens once more than MAX_ENTRIES (10,000) different
    # username+address pairs hold failures at the same time. That is cheap to cause:
    # failed logins under unknown usernames skip password hashing, so a client on the
    # LAN can flood them and push its own counter out after a few guesses. The lockout
    # therefore slows casual guessing but does not stop a determined attacker on the LAN.
    def _evict(self, current: tuple[str, str], now: float) -> None:
        """Keep the table within MAX_ENTRIES."""
        expired = [k for k, e in self._entries.items() if 0 < e.blocked_until <= now]
        for k in expired:
            del self._entries[k]
        while len(self._entries) > self.MAX_ENTRIES:
            others = [(k, e) for k, e in self._entries.items() if k != current]
            if not others:
                break
            unblocked = [(k, e) for k, e in others if e.blocked_until <= now]
            if unblocked:
                victim = min(unblocked, key=lambda item: item[1].last_failure)[0]
            else:
                victim = min(others, key=lambda item: item[1].blocked_until)[0]
            del self._entries[victim]

    def reset(self, key: tuple[str, str]) -> None:
        """Forget the key's failures (a successful login)."""
        self._entries.pop(key, None)

    def clear(self) -> None:
        """Forget every entry."""
        self._entries.clear()


class GenerationGate:
    """Caps concurrent chat generations; waiters queue in arrival order, never dropped."""

    def __init__(self) -> None:
        self._semaphore: asyncio.Semaphore | None = None
        self._cap: int = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waiting: int = 0

    def _current(self) -> asyncio.Semaphore | None:
        """Return the semaphore for the current cap and loop, or None when uncapped."""
        cap = settings.MAX_CONCURRENT_GENERATIONS
        if cap <= 0:
            return None
        loop = asyncio.get_running_loop()
        if self._semaphore is None or cap != self._cap or loop is not self._loop:
            self._semaphore = asyncio.Semaphore(cap)
            self._cap = cap
            self._loop = loop
        return self._semaphore

    @property
    def waiting(self) -> int:
        """Number of callers waiting for a slot right now."""
        return self._waiting

    @asynccontextmanager
    async def slot(
        self, on_wait: Callable[[], Awaitable[None]] | None = None
    ) -> AsyncIterator[float]:
        """Hold a generation slot for the body; yield the seconds spent waiting."""
        semaphore = self._current()
        if semaphore is None:
            yield 0.0
            return
        waited = 0.0
        if semaphore.locked():
            started = _now()
            self._waiting += 1
            try:
                if on_wait is not None:
                    await on_wait()
                await semaphore.acquire()
            finally:
                self._waiting -= 1
            waited = round(_now() - started, 3)
        else:
            await semaphore.acquire()
        try:
            yield waited
        finally:
            semaphore.release()

    def reset(self) -> None:
        """Forget the semaphore, loop and waiter count."""
        self._semaphore = None
        self._loop = None
        self._cap = 0
        self._waiting = 0


login_throttle = LoginThrottle()
generation_gate = GenerationGate()


def limits_in_force() -> dict[str, int]:
    """Return the four limit values currently in force."""
    return {
        "rate_limit_per_minute": settings.RATE_LIMIT_MESSAGES_PER_MINUTE,
        "login_max_failed_attempts": settings.LOGIN_MAX_FAILED_ATTEMPTS,
        "login_lockout_seconds": settings.LOGIN_LOCKOUT_SECONDS,
        "max_concurrent_generations": settings.MAX_CONCURRENT_GENERATIONS,
    }
