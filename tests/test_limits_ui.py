"""Node-free source guard for the limit messages in the chat and on the login form."""

import re
from pathlib import Path

from tests.test_static_js_syntax import check_js_balance

STATIC_DIR: Path = Path(__file__).resolve().parent.parent / "ui" / "static"

_RATE_TEXT: str = "Слишком много сообщений. Повторите через "
_RATE_FALLBACK: str = "Слишком много сообщений. Повторите чуть позже"
_QUEUE_TEXT: str = "В очереди, ожидание свободного места…"
_LOCK_TEXT: str = "Слишком много неудачных попыток. Повторите через "
_LOCK_FALLBACK: str = "Слишком много неудачных попыток. Повторите позже"


def _app_js_source() -> str:
    """Return app.js without full-line // comments."""
    raw: str = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    return "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("//"))


def _index_html() -> str:
    """Return index.html."""
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _login_html() -> str:
    """Return login.html."""
    return (STATIC_DIR / "login.html").read_text(encoding="utf-8")


def _login_script() -> str:
    """Return the text of the last inline script block of login.html."""
    blocks: list[str] = re.findall(r"<script>(.*?)</script>", _login_html(), re.DOTALL)
    assert blocks, "no inline script in login.html"
    return blocks[-1]


def _function_source(src: str, name: str) -> str:
    """Return the source of a top-level function up to the next top-level function."""
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(", src, re.MULTILINE)
    assert match is not None, f"function {name} not found"
    rest: str = src[match.end() :]
    nxt = re.search(r"^(?:async )?function ", rest, re.MULTILINE)
    return src[match.start() : match.end() + (nxt.start() if nxt else len(rest))]


def test_rate_limit_copy() -> None:
    """The toast text is built on the client from retry_after."""
    body: str = _function_source(_app_js_source(), "rateLimitMessage")
    assert _RATE_TEXT in body
    assert _RATE_FALLBACK in body
    assert "Math.ceil" in body


def test_rate_limit_handling_restores_input_without_locking() -> None:
    """A refused message is removed, restored to the input, and nothing is locked."""
    body: str = _function_source(_app_js_source(), "handleRateLimited")
    for needle in ("state.lastFailedMessage", "'warning'", "data-optimistic", ".remove()"):
        assert needle in body, needle
    for forbidden in ("disabled", "setInterval", "setTimeout", "innerHTML", "data.detail"):
        assert forbidden not in body, forbidden


def test_rate_limited_branch_is_wired_before_generic_fallback() -> None:
    """handleWsMessage routes RATE_LIMITED between PROVIDER_UNAVAILABLE and the generic branch."""
    body: str = _function_source(_app_js_source(), "handleWsMessage")
    provider: int = body.index("PROVIDER_UNAVAILABLE")
    rate: int = body.index("data.code === 'RATE_LIMITED'")
    generic: int = body.index("showToast(data.detail || 'Ошибка', 'error')")
    assert provider < rate < generic


def test_optimistic_marker_on_user_bubble() -> None:
    """The user bubble is marked optimistic and stays sanitized."""
    body: str = _function_source(_app_js_source(), "appendUserBubble")
    assert "dataset.optimistic = 'true'" in body
    assert "DOMPurify.sanitize(" in body


def test_queue_caption() -> None:
    """The queue caption is built with textContent under the typing dots."""
    body: str = _function_source(_app_js_source(), "showQueueNote")
    assert _QUEUE_TEXT in body
    assert "queue-note mt-1 text-sm text-slate-400" in body
    for needle in ("createElement", "textContent", "appendLoadingBubble()"):
        assert needle in body, needle
    assert "innerHTML" not in body


def test_queue_wiring() -> None:
    """The queued frame shows the caption and the first token removes it."""
    src: str = _app_js_source()
    assert "case 'queued':" in _function_source(src, "handleWsMessage")
    token: str = _function_source(src, "appendTokenToStream")
    assert "removeQueueNote()" in token
    assert token.index("removeQueueNote()") < token.index("contentEl.innerHTML")


def test_no_limit_values_on_screen() -> None:
    """No screen shows the limits, the queue length or a queue position."""
    app: str = _app_js_source()
    html: str = _index_html()
    for name in (
        "RATE_LIMIT_MESSAGES_PER_MINUTE",
        "MAX_CONCURRENT_GENERATIONS",
        "LOGIN_MAX_FAILED_ATTEMPTS",
        "LOGIN_LOCKOUT_SECONDS",
    ):
        assert name not in html
        assert name not in app
    assert "queue_wait_seconds" not in app
    assert "queue_position" not in app


def test_no_stop_control() -> None:
    """No Stop or cancel control was added to the chat UI."""
    assert re.search(r'id="stop', _index_html()) is None
    app: str = _app_js_source()
    for name in ("stopGeneration", "stopTurn", "abortTurn", "cancelTurn"):
        assert f"function {name}(" not in app


def test_login_lockout_copy() -> None:
    """The lockout message is built in minutes, rounded up, minimum 1."""
    script: str = _login_script()
    assert "function lockoutMessage(" in script
    assert _LOCK_TEXT in script
    assert _LOCK_FALLBACK in script
    assert "Math.max(1, Math.ceil(" in script
    assert "/ 60" in script


def test_login_wiring() -> None:
    """The 429 branch reads Retry-After, never the username, and uses no innerHTML."""
    script: str = _login_script()
    for needle in ("resp.status === 429", "resp.headers.get('Retry-After')", "body.retry_after"):
        assert needle in script, needle
    branch_start: int = script.index("resp.status === 429")
    generic: int = script.index("body.detail || 'Ошибка входа'")
    assert branch_start < generic
    assert "innerHTML" not in script
    assert "username" not in script[branch_start:generic]


def test_login_error_is_alert() -> None:
    """The login error element is announced to assistive technology."""
    assert re.search(r'<div id="login-error"[^>]*role="alert"', _login_html())


def test_login_inline_script_balanced() -> None:
    """The inline script has balanced brackets and strings."""
    assert check_js_balance(_login_script()) == []


def test_login_port_constant_untouched() -> None:
    """The agent port constant appears exactly once."""
    assert _login_html().count("const AGENT_PORT = 8001;") == 1
