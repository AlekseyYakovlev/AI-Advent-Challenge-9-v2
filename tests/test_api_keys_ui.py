"""Node-free source guard for the API keys section, the one-time key modal and chat_updated handling."""

import re
from pathlib import Path

STATIC_DIR: Path = Path(__file__).resolve().parent.parent / "ui" / "static"

_START: str = "// ---- API keys ----"
_END: str = "// ---- end API keys ----"


def _raw_app_js() -> str:
    """Return app.js unmodified."""
    return (STATIC_DIR / "app.js").read_text(encoding="utf-8")


def _app_js_source() -> str:
    """Return app.js without full-line // comments."""
    return "\n".join(
        line for line in _raw_app_js().splitlines() if not line.lstrip().startswith("//")
    )


def _html() -> str:
    """Return index.html."""
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _api_keys_region() -> str:
    """Return the raw app.js text between the API keys markers."""
    raw: str = _raw_app_js()
    assert raw.count(_START) == 1
    assert raw.count(_END) == 1
    start: int = raw.index(_START)
    end: int = raw.index(_END)
    assert start < end
    return raw[start:end]


def _function_source(src: str, name: str) -> str:
    """Return the source of a top-level function up to the next top-level function."""
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(", src, re.MULTILINE)
    assert match is not None, f"function {name} not found"
    rest: str = src[match.end() :]
    nxt = re.search(r"^(?:async )?function ", rest, re.MULTILINE)
    return src[match.start() : match.end() + (nxt.start() if nxt else len(rest))]


def test_section_placement_and_content() -> None:
    """The section sits between LLM providers and Network and has all controls."""
    html: str = _html()
    assert html.count('id="api-keys-section"') == 1
    start: int = html.index('id="api-keys-section"')
    assert html.index('id="llm-providers-section"') < start < html.index('id="network-section"')
    block: str = html[start : html.index("</section>", start)]
    assert "API ключи" in block
    assert "Authorization: Bearer" in block
    for element_id in (
        "btn-api-key-add",
        "api-key-form",
        "api-key-name",
        "api-key-form-error",
        "api-key-empty",
        "api-key-list",
    ):
        assert f'id="{element_id}"' in block


def test_modal_contract() -> None:
    """The one-time modal is an aria dialog closed only by the x button."""
    html: str = _html()
    start: int = html.index('id="api-key-modal"')
    tag: str = html[html.rfind("<div", 0, start) : html.index(">", start)]
    assert 'role="dialog"' in tag
    assert 'aria-modal="true"' in tag
    assert "z-[60]" in tag
    for fragment in ('id="btn-close-api-key"', 'id="api-key-value"', 'id="api-key-curl"', 'role="alert"'):
        assert fragment in html
    assert "btn-close-api-key-modal" not in html


def test_region_never_inserts_html_or_persists() -> None:
    """Key names reach the DOM as text and the key never reaches storage or the console."""
    region: str = _api_keys_region()
    for banned in (
        "innerHTML",
        "insertAdjacentHTML",
        "outerHTML",
        "localStorage",
        "sessionStorage",
        "console.",
    ):
        assert banned not in region


def test_curl_contract() -> None:
    """The curl example uses the page host and the final HTTP chat route."""
    body: str = _function_source(_app_js_source(), "buildApiKeyCurl")
    for needed in (
        "AGENT_BASE",
        "/api/v1/chats/{chat_id}/messages",
        "Authorization: Bearer",
        "Content-Type: application/json",
        '"model"',
    ):
        assert needed in body
    assert "http-message" not in body
    assert "localhost" not in body
    assert re.search(r"800[01]", body) is None


def test_key_lifecycle() -> None:
    """Closing the modal wipes the key; the key is never stored in state."""
    body: str = _function_source(_app_js_source(), "closeApiKeyModal")
    assert "shownApiKey = null" in body
    assert "$('api-key-value').textContent = ''" in body
    assert "$('api-key-curl').textContent = ''" in body
    assert re.search(r"state\.\w+\s*=\s*[^;]*\.key\b", _api_keys_region()) is None


def test_cookie_only_calls() -> None:
    """Key routes go through apiFetch only; there is no bare fetch call."""
    region: str = _api_keys_region()
    lines: list[str] = [line for line in region.splitlines() if "/api/v1/api-keys" in line]
    assert lines
    assert all("apiFetch(" in line for line in lines)
    assert re.search(r"(?<![A-Za-z])fetch\(", region) is None


def test_list_rendering() -> None:
    """Rows show the prefix with an ellipsis, last use and a revoke button."""
    src: str = _app_js_source()
    row: str = _function_source(src, "renderApiKeyRow")
    for needed in ("key_prefix", "…", "использован: ", "Отозвать"):
        assert needed in row
    assert "никогда" in _function_source(src, "formatApiKeyDate")
    assert ">= 20" in _function_source(src, "renderApiKeyList")


def test_revoke() -> None:
    """Revoking asks for confirmation, deletes and reloads the list."""
    body: str = _function_source(_app_js_source(), "revokeApiKey")
    for needed in ("confirm(", "method: 'DELETE'", "отозван", "loadApiKeys("):
        assert needed in body


def test_create() -> None:
    """Creating validates the name, guards double submit and opens the modal."""
    body: str = _function_source(_app_js_source(), "createApiKey")
    for needed in ("Введите название ключа", "state.savingApiKey", "method: 'POST'", "openApiKeyModal("):
        assert needed in body


def test_settings_modal_loads_keys() -> None:
    """Opening settings loads the key list."""
    assert "loadApiKeys(" in _function_source(_app_js_source(), "openSettingsModal")


def test_chat_updated_events() -> None:
    """chat_updated refreshes the list and the open chat without touching the socket."""
    src: str = _app_js_source()
    handler: str = _function_source(src, "handleEventFrame")
    marker: str = "frame.type === 'chat_updated'"
    assert marker in handler
    assert handler.index(marker) < handler.index("applySchedulerEvent(frame)")
    updated: str = _function_source(src, "onChatUpdatedEvent")
    for needed in ("loadChats(", "state.isStreaming", "chatRefreshPendingId"):
        assert needed in updated
    refresh: str = _function_source(src, "refreshOpenChat")
    for needed in ("loadChatTree(", "loadChatStats(", "loadChatRag("):
        assert needed in refresh
    for banned in ("selectChat(", "connectWs(", "disconnectWs("):
        assert banned not in refresh
    assert "chatRefreshPendingId" in _function_source(src, "setStreaming")


def test_no_http_marker_in_messages() -> None:
    """HTTP-originated messages render exactly like typed ones."""
    src: str = _app_js_source()
    assert "API-badge" not in src
    assert "http-origin" not in src
    assert "chat_updated" not in _function_source(src, "renderMessages")


def test_russian_copy() -> None:
    """The UI-SPEC strings are present."""
    text: str = _html() + _api_keys_region()
    for phrase in (
        "+ Создать ключ",
        "Ключей пока нет",
        "Ключ создан",
        "Скопируйте ключ сейчас",
        "Ключ скопирован",
        "Пример запроса скопирован",
        "Не удалось загрузить список ключей",
        "Достигнут лимит: 20 активных ключей",
    ):
        assert phrase in text
