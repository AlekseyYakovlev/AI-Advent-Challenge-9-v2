"""Source guards for the Day 30 documentation (API keys, HTTP chat and limits)."""

import importlib.util
import re
from pathlib import Path

import pytest

ROOT_DIR: Path = Path(__file__).resolve().parent.parent
DOCS_DIR: Path = ROOT_DIR / "docs"
TESTS_DIR: Path = ROOT_DIR / "tests"

LIMIT_SETTINGS: tuple[str, ...] = (
    "RATE_LIMIT_MESSAGES_PER_MINUTE",
    "LOGIN_MAX_FAILED_ATTEMPTS",
    "LOGIN_LOCKOUT_SECONDS",
    "MAX_CONCURRENT_GENERATIONS",
)
UI_STRINGS: tuple[str, ...] = (
    "Слишком много сообщений. Повторите через N с",
    "В очереди, ожидание свободного места…",
    "Слишком много неудачных попыток. Повторите через N мин",
)


def _read_doc(name: str) -> str:
    """Return a documentation file as UTF-8 text."""
    return (DOCS_DIR / name).read_text(encoding="utf-8")


def test_api_spec_day30_sections_in_order() -> None:
    """The Day 30 sections follow the Network section and precede the environment table."""
    text = _read_doc("API_SPEC.md")
    headings = [
        "## Network (Day 30)",
        "## API keys (Day 30)",
        "## HTTP chat (Day 30)",
        "## Limits (Day 30)",
        "## Environment Settings",
    ]
    positions = [text.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert "### curl scenario" in text
    assert "### Statuses" in text


@pytest.mark.parametrize(
    "needle",
    [
        "/api/v1/api-keys",
        "/api/v1/chats/$CHAT_ID/messages",
        "Authorization: Bearer $KEY",
        "/api/v1/auth/login",
        "/api/v1/lm-studio/load-model",
        "/api/v1/kb",
        "/api/v1/mcp/servers/$MCP_ID",
        '"kb_id": null',
    ],
)
def test_api_spec_curl_scenario_strings(needle: str) -> None:
    """The curl scenario mentions every route and header it needs."""
    assert needle in _read_doc("API_SPEC.md")


@pytest.mark.parametrize(
    "needle",
    [
        "CHAT_BUSY",
        "CONTEXT_OVERFLOW",
        "suggested_strategy",
        "LM_STUDIO_UNAVAILABLE",
        "LLM_UNREACHABLE",
        "PROVIDER_UNAVAILABLE",
        "user_message_kept",
        "chat_updated",
        "provider_kind",
        "WWW-Authenticate",
    ],
)
def test_api_spec_error_contract_strings(needle: str) -> None:
    """The error contract of the HTTP chat endpoint is spelled out."""
    assert needle in _read_doc("API_SPEC.md")


def test_api_spec_status_table_rows() -> None:
    """The status table lists every status the endpoint can answer."""
    text = _read_doc("API_SPEC.md")
    start = text.index("### Statuses")
    end = text.index("\n### ", start + 1)
    table = text[start:end]
    for status in ("200", "401", "403", "404", "409", "413", "415", "422", "429", "500", "502", "503"):
        assert f"| {status} |" in table, status


def test_api_spec_scenario_routes_exist() -> None:
    """Every route path of the curl scenario is registered in the Agent app."""
    from agent.main import app

    paths = {getattr(route, "path", None) for route in app.routes}
    paths |= set(app.openapi()["paths"])
    expected = {
        "/api/v1/auth/login",
        "/api/v1/api-keys",
        "/api/v1/api-keys/{key_id}",
        "/api/v1/auth/me",
        "/api/v1/chats",
        "/api/v1/lm-studio/models",
        "/api/v1/lm-studio/load-model",
        "/api/v1/kb",
        "/api/v1/mcp/servers",
        "/api/v1/mcp/servers/{server_id}",
        "/api/v1/chats/{chat_id}/messages",
        "/api/v1/chats/{chat_id}/tree",
    }
    assert expected <= paths, expected - paths


def test_docs_have_no_placeholder_path() -> None:
    """No document uses the early placeholder path of the HTTP chat endpoint."""
    for path in DOCS_DIR.rglob("*.md"):
        assert "http-message" not in path.read_text(encoding="utf-8"), path.name


def test_testing_guide_lists_day30_files() -> None:
    """The testing guide has the Day 30 section and names every Day 30 test file and script."""
    text = _read_doc("TESTING_GUIDE.md")
    assert "## API keys and HTTP chat (Day 30)" in text
    for name in (
        "test_api_keys.py",
        "test_bearer_auth.py",
        "test_ws_turn_failures.py",
        "test_http_chat.py",
        "test_http_chat_rag.py",
        "test_api_keys_ui.py",
        "test_docs_day30.py",
        "e2e_api_keys_playwright.py",
    ):
        assert name in text, name


def test_architecture_day30_section() -> None:
    """The architecture guide describes the key seam and the HTTP chat turn."""
    text = _read_doc("ARCHITECTURE.md")
    for needle in (
        "## API keys and HTTP chat (Day 30)",
        "get_session_user",
        "_run_chat_turn",
        "chat_updated",
        "CHAT_BUSY",
    ):
        assert needle in text, needle


def test_user_guide_day30_section() -> None:
    """The user guide explains how to create a key and call the service."""
    text = _read_doc("USER_GUIDE.md")
    for needle in (
        "## API keys and HTTP access",
        "API ключи",
        "Authorization: Bearer",
        "/messages",
        "kb_id",
    ):
        assert needle in text, needle


def _section(text: str, heading: str) -> str:
    """Return the text from a heading up to the next heading of the same level."""
    level = heading.split(" ", 1)[0]
    start = text.index(heading)
    match = re.search(rf"\n{re.escape(level)} (?!#)", text[start + len(heading):])
    end = start + len(heading) + match.start() if match else len(text)
    return text[start:end]


@pytest.mark.parametrize(
    "needle",
    [
        "RATE_LIMITED",
        "Retry-After",
        "retry_after",
        "LOGIN_BLOCKED",
        "queue_wait_seconds",
        '"type": "queued"',
        "Access-Control-Expose-Headers",
        *LIMIT_SETTINGS,
    ],
)
def test_api_spec_limits_strings(needle: str) -> None:
    """The API specification spells out the limits contract."""
    assert needle in _read_doc("API_SPEC.md")


def test_api_spec_has_no_stale_statements() -> None:
    """Statements that stopped being true are gone from the documents."""
    assert "There is no rate limit in this version" not in _read_doc("API_SPEC.md")
    assert "context_usage_percent" not in _read_doc("API_SPEC.md")
    architecture = _read_doc("ARCHITECTURE.md")
    assert "login attempts are unlimited" not in architecture
    assert "context_usage_percent" not in architecture
    assert "reachable without login in this version" not in _read_doc("USER_GUIDE.md")


def test_api_spec_stats_keys_match_code() -> None:
    """Every key compute_chat_stats returns is named in the API specification."""
    source = (ROOT_DIR / "agent" / "context_engine.py").read_text(encoding="utf-8")
    start = source.index("def compute_chat_stats")
    block_start = source.index("default_stats", start)
    block = source[block_start : source.index("}", block_start)]
    keys = set(re.findall(r'"(\w+)":', block))
    assert "usage_percent" in keys
    text = _read_doc("API_SPEC.md")
    for key in keys:
        assert key in text, key


def test_api_spec_env_defaults_match_settings() -> None:
    """The environment table rows hold the defaults of shared.config.Settings."""
    from shared.config import Settings

    text = _read_doc("API_SPEC.md")
    table = text[text.index("## Environment Settings"):]
    for name in LIMIT_SETTINGS:
        default = Settings.model_fields[name].default
        row = next(line for line in table.splitlines() if line.startswith(f"| {name} |"))
        assert row.split("|")[2].strip() == str(default), name


def test_testing_guide_limits_section() -> None:
    """The testing guide has the limits section with its coverage map and files."""
    text = _read_doc("TESTING_GUIDE.md")
    assert "## Limits (Day 30)" in text
    assert "### Coverage map for Day 30" in text
    for name in (
        "test_limits_config.py",
        "test_limits_core.py",
        "test_rate_limit.py",
        "test_generation_queue.py",
        "test_login_lockout.py",
        "test_limits_ui.py",
        "test_verify_service.py",
        "e2e_limits_playwright.py",
        "verify_service.py",
        "test_bind_host.py",
        "test_origins.py",
        "test_api_keys.py",
        "test_bearer_auth.py",
        "test_http_chat.py",
    ):
        assert name in text, name


def test_testing_guide_day30_files_exist() -> None:
    """Every test file named in the Day 30 sections of the guide exists."""
    text = _read_doc("TESTING_GUIDE.md")
    names: set[str] = set()
    for heading in (
        "## LAN exposure (Day 30)",
        "## API keys and HTTP chat (Day 30)",
        "## Limits (Day 30)",
    ):
        names |= set(re.findall(r"test_\w+\.py", _section(text, heading)))
    assert names
    missing = sorted(name for name in names if not (TESTS_DIR / name).is_file())
    assert not missing, missing


def test_architecture_limits_section() -> None:
    """The architecture guide describes where the limits live and their known limits."""
    text = _read_doc("ARCHITECTURE.md")
    for needle in (
        "## Limits and the generation queue (Day 30)",
        "agent/limits.py",
        "run_limited_turn",
        "_agent_routes_ready",
        "MAX_ENTRIES",
    ):
        assert needle in text, needle


def _load_verify_parser():  # type: ignore[no-untyped-def]
    """Load scripts/verify_service.py by path and return its argument parser."""
    path = ROOT_DIR / "scripts" / "verify_service.py"
    spec = importlib.util.spec_from_file_location("verify_service_docs_guard", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_parser()


def test_user_guide_limits_and_script() -> None:
    """The user guide explains the limits and only names real flags of the script."""
    text = _read_doc("USER_GUIDE.md")
    for needle in (
        "## Limits",
        "## Checking the service from another machine",
        "verify_service.py",
        "AIA_API_KEY",
        *LIMIT_SETTINGS,
        *UI_STRINGS,
    ):
        assert needle in text, needle
    section = _section(text, "## Checking the service from another machine")
    flags = set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", section))
    assert {"--rate-mode", "--keep"} <= flags
    known = {opt for action in _load_verify_parser()._actions for opt in action.option_strings}
    assert flags <= known, flags - known


def test_env_example_lists_limits() -> None:
    """.env.example carries the four settings with their defaults."""
    from shared.config import Settings

    text = (ROOT_DIR / ".env.example").read_text(encoding="utf-8")
    for name in LIMIT_SETTINGS:
        default = Settings.model_fields[name].default
        assert f"{name}={default}" in text, name
