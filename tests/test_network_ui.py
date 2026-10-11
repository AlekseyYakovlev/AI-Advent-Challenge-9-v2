"""Node-free source guard for the Network section of the Settings modal."""

import re
from pathlib import Path

STATIC_DIR: Path = Path(__file__).resolve().parent.parent / "ui" / "static"

_START: str = "// ---- Network ----"
_END: str = "// ---- end Network ----"


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


def _network_region() -> str:
    """Return the raw app.js text between the Network markers."""
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


def test_network_section_is_read_only_and_placed_last() -> None:
    """The section sits in the settings modal after LLM providers and has no controls."""
    html: str = _html()
    assert html.count('id="network-section"') == 1
    start: int = html.index('id="network-section"')
    assert html.index('id="llm-providers-section"') < start < html.index('id="add-user-modal"')
    block: str = html[start : html.index("</section>", start)]
    assert "<h3" in block and "Сеть" in block
    for element_id in ("network-status", "network-hint", "network-address-list"):
        assert f'id="{element_id}"' in block
    for banned in ("<form", "<input", "<button"):
        assert banned not in block


def test_network_region_never_inserts_html() -> None:
    """Addresses must only reach the DOM as text."""
    region: str = _network_region()
    for banned in ("innerHTML", "insertAdjacentHTML", "outerHTML"):
        assert banned not in region


def test_load_network_info_uses_endpoint_and_handles_errors() -> None:
    """The loader calls the endpoint and renders an in-section error on failure."""
    body: str = _function_source(_app_js_source(), "loadNetworkInfo")
    assert "apiFetch('/api/v1/network')" in body
    assert "catch" in body
    assert "renderNetworkError(" in body


def test_render_network_info_covers_both_modes() -> None:
    """The renderer handles loopback and LAN and labels each address button."""
    body: str = _function_source(_app_js_source(), "renderNetworkInfo")
    assert "Доступен только на этом компьютере" in body
    assert "Открыт в локальной сети" in body
    assert "info.mode === 'lan'" in body
    assert "info.addresses" in body
    assert "aria-label" in body


def test_clipboard_helper_has_http_fallback() -> None:
    """The helper tries the async API only in secure contexts and falls back to execCommand."""
    body: str = _function_source(_app_js_source(), "copyTextToClipboard")
    for needle in ("navigator.clipboard", "isSecureContext", "document.execCommand('copy')", "finally"):
        assert needle in body


def test_copy_callers_use_shared_helper() -> None:
    """Both copy entry points go through copyTextToClipboard."""
    src: str = _app_js_source()
    assert "copyTextToClipboard(" in _function_source(src, "copyMcpToolName")
    assert "copyTextToClipboard(" in _function_source(src, "copyNetworkAddress")
    assert "document.execCommand" not in _function_source(src, "copyMcpToolName")


def test_open_settings_modal_loads_network_info() -> None:
    """Opening the settings modal refreshes the network section."""
    assert "loadNetworkInfo(" in _function_source(_app_js_source(), "openSettingsModal")


def test_russian_copy_is_present() -> None:
    """The UI-SPEC strings for this section appear verbatim."""
    src: str = _app_js_source()
    for text in (
        "Доступен только на этом компьютере",
        "Открыт в локальной сети",
        "Не удалось получить сведения о сети. Попробуйте открыть настройки ещё раз.",
        "Сетевые адреса не найдены. Проверьте подключение к сети и откройте настройки ещё раз.",
        "Скопировано: ",
        "BIND_HOST=0.0.0.0",
    ):
        assert text in src


def test_host_derivation_unchanged_and_no_localhost_in_region() -> None:
    """AGENT_BASE and WS_BASE still use window.location.hostname; the region has no localhost."""
    src: str = _app_js_source()
    assert re.search(r"AGENT_BASE\s*=.*window\.location\.hostname", src)
    assert re.search(r"WS_BASE\s*=.*window\.location\.hostname", src)
    assert "localhost" not in _network_region()
