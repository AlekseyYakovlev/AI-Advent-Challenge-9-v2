"""Source guards for the Day 30 documentation (API keys and HTTP chat)."""

from pathlib import Path

import pytest

DOCS_DIR: Path = Path(__file__).resolve().parent.parent / "docs"


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
    for status in ("200", "401", "403", "404", "409", "413", "415", "422", "500", "502", "503"):
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
