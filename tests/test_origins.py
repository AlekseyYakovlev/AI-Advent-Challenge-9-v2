"""Tests for the host-match Origin rule and its CORS, REST and WebSocket enforcement."""

import inspect
import sys
from typing import Any
from urllib.parse import urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

import shared.origins
from agent.main import app as agent_app
from shared.config import settings
from shared.origins import LOOPBACK_ORIGINS, is_allowed_origin
from tests.conftest import login_test_client
from ui.main import app as ui_app

P = settings.UI_PORT
A = settings.AGENT_PORT

RULE_CASES: list[tuple[str, str | None, bool]] = [
    (f"http://192.168.1.5:{P}", f"192.168.1.5:{A}", True),
    (f"http://192.168.1.5:{P}", "192.168.1.5", True),
    (f"http://my-pc:{P}", f"MY-PC:{A}", True),
    (f"http://MY-PC:{P}", f"my-pc:{A}", True),
    (f"http://[fe80::1]:{P}", f"[fe80::1]:{A}", True),
    (f"http://localhost:{P}", "test", True),
    (f"http://127.0.0.1:{P}", None, True),
    (f"http://localhost:{P}", f"192.168.1.5:{A}", True),
    (f"http://evil.example:{P}", f"192.168.1.5:{A}", False),
    (f"http://192.168.1.77:{P}", f"192.168.1.5:{A}", False),
    (f"http://192.168.1.50:{P}", f"192.168.1.5:{A}", False),
    (f"http://192.168.1.5:{A}", f"192.168.1.5:{A}", False),
    ("http://192.168.1.5", f"192.168.1.5:{A}", False),
    (f"https://192.168.1.5:{P}", f"192.168.1.5:{A}", False),
    (f"http://localhost:{A}", f"localhost:{A}", False),
    ("null", "localhost", False),
    ("null", None, False),
    ("", f"192.168.1.5:{A}", False),
    (f"http://localhost:{P}.evil.example", f"localhost:{A}", False),
    (f"http://192.168.1.5:{P}/", f"192.168.1.5:{A}", False),
    (f"http://192.168.1.5:{P}/path", f"192.168.1.5:{A}", False),
    (f"http://user@192.168.1.5:{P}", f"192.168.1.5:{A}", False),
    (f"http://192.168.1.5:{P}?x=1", f"192.168.1.5:{A}", False),
    (f"http://192.168.1.5:{P}", None, False),
    (f"http://192.168.1.5:{P}", "", False),
    (f"http://192.168.1.5:{P}", "bad host:x:y", False),
]


@pytest.mark.parametrize(("origin", "host", "expected"), RULE_CASES)
def test_is_allowed_origin_rule(origin: str, host: str | None, expected: bool) -> None:
    """The rule accepts a host-matched UI-port origin and rejects everything else."""
    assert is_allowed_origin(origin, host) is expected


def test_loopback_origins_are_a_tuple_of_ui_port_origins() -> None:
    """LOOPBACK_ORIGINS is the two fixed loopback origins on the UI port."""
    assert isinstance(LOOPBACK_ORIGINS, tuple)
    assert LOOPBACK_ORIGINS == (f"http://localhost:{P}", f"http://127.0.0.1:{P}")


def test_rule_has_no_bind_mode_branch() -> None:
    """The rule never depends on the bind mode."""
    source = inspect.getsource(shared.origins)
    assert "BIND_HOST" not in source
    assert "is_lan_mode" not in source


LAN_HOST = "192.168.1.5"
NAME_HOST = "my-pc"
LAN_HOST_HEADER = f"{LAN_HOST}:{A}"
LAN_ORIGIN = f"http://{LAN_HOST}:{P}"
NAME_ORIGIN = f"http://{NAME_HOST}:{P}"
FOREIGN = f"http://evil.example:{P}"
OTHER_LAN = f"http://192.168.1.77:{P}"
MCP_BASE = "/api/v1/mcp/servers"
ACAO = "access-control-allow-origin"


def _mcp_body() -> dict[str, Any]:
    """Build a create body for an MCP server that is never connected."""
    return {
        "name": "origin-check",
        "command": sys.executable,
        "args": ["-c", "pass"],
        "env": {},
        "cwd": None,
        "enabled": False,
    }


async def test_preflight_reflects_lan_origin(client: AsyncClient) -> None:
    """A preflight from the LAN host pair gets the Origin reflected with credentials."""
    resp = await client.options(
        "/health",
        headers={
            "Host": LAN_HOST_HEADER,
            "Origin": LAN_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers[ACAO] == LAN_ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"


async def test_preflight_reflects_machine_name_with_case_difference(
    client: AsyncClient,
) -> None:
    """An upper-case Host matches a lower-case machine-name Origin."""
    resp = await client.options(
        "/health",
        headers={
            "Host": f"{NAME_HOST.upper()}:{A}",
            "Origin": NAME_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers[ACAO] == NAME_ORIGIN


async def test_get_with_lan_pair_carries_cors_header(client: AsyncClient) -> None:
    """A simple GET from the LAN host pair gets the reflected Origin."""
    resp = await client.get("/health", headers={"Host": LAN_HOST_HEADER, "Origin": LAN_ORIGIN})
    assert resp.status_code == 200
    assert resp.headers[ACAO] == LAN_ORIGIN


@pytest.mark.parametrize("bad_origin", [FOREIGN, OTHER_LAN])
async def test_foreign_origin_gets_no_cors_header_and_preflight_fails(
    client: AsyncClient, bad_origin: str
) -> None:
    """A foreign or other-machine Origin is not reflected and its preflight is a 400."""
    simple = await client.get("/health", headers={"Host": LAN_HOST_HEADER, "Origin": bad_origin})
    assert ACAO not in simple.headers
    preflight = await client.options(
        "/health",
        headers={
            "Host": LAN_HOST_HEADER,
            "Origin": bad_origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code == 400
    assert ACAO not in preflight.headers


async def test_request_without_origin_has_no_cors_header(client: AsyncClient) -> None:
    """No Origin means no CORS header and the request still succeeds."""
    resp = await client.get("/health", headers={"Host": LAN_HOST_HEADER})
    assert resp.status_code == 200
    assert ACAO not in resp.headers


async def test_null_origin_gets_no_cors_header(client: AsyncClient) -> None:
    """Origin: null is never reflected."""
    resp = await client.get("/health", headers={"Host": LAN_HOST_HEADER, "Origin": "null"})
    assert ACAO not in resp.headers


async def test_ui_app_reflects_lan_origin_only() -> None:
    """The UI app reflects the LAN origin and not a foreign one (lifespan not entered)."""
    transport = ASGITransport(app=ui_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ui_client:
        host = f"{LAN_HOST}:{P}"
        ok = await ui_client.get("/", headers={"Host": host, "Origin": LAN_ORIGIN})
        assert ok.headers[ACAO] == LAN_ORIGIN
        bad = await ui_client.get("/", headers={"Host": host, "Origin": FOREIGN})
        assert ACAO not in bad.headers


@pytest.mark.parametrize("origin", [LAN_ORIGIN, NAME_ORIGIN])
async def test_mutation_accepts_host_matched_origin(
    authenticated_client: AsyncClient, origin: str
) -> None:
    """A state-changing request with a host-matched Origin is created (201)."""
    host = urlsplit(origin).hostname
    resp = await authenticated_client.post(
        MCP_BASE, json=_mcp_body(), headers={"Host": f"{host}:{A}", "Origin": origin}
    )
    assert resp.status_code == 201, resp.text


async def test_mutation_without_origin_is_allowed(authenticated_client: AsyncClient) -> None:
    """A state-changing request with no Origin stays allowed (cookie-gated)."""
    resp = await authenticated_client.post(
        MCP_BASE, json=_mcp_body(), headers={"Host": LAN_HOST_HEADER}
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        (FOREIGN, LAN_HOST_HEADER),
        (OTHER_LAN, LAN_HOST_HEADER),
        (f"http://{LAN_HOST}:{A}", LAN_HOST_HEADER),
        (LAN_ORIGIN, f"192.168.1.9:{A}"),
    ],
)
async def test_mutation_rejects_non_matching_origin(
    authenticated_client: AsyncClient, origin: str, host: str
) -> None:
    """A non-matching Origin gets 403 and creates no row."""
    resp = await authenticated_client.post(
        MCP_BASE, json=_mcp_body(), headers={"Host": host, "Origin": origin}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Cross-origin request rejected"
    assert (await authenticated_client.get(MCP_BASE)).json() == []


def test_events_ws_accepts_lan_pair() -> None:
    """/ws/events accepts the LAN host pair."""
    with TestClient(agent_app) as tc:
        login_test_client(tc)
        with tc.websocket_connect(
            "/ws/events", headers={"Host": LAN_HOST_HEADER, "Origin": LAN_ORIGIN}
        ) as ws:
            assert ws is not None


@pytest.mark.parametrize("origin", ["null", FOREIGN])
def test_events_ws_rejects_null_and_foreign(origin: str) -> None:
    """/ws/events closes with 1008 for Origin null and for a foreign Origin."""
    with TestClient(agent_app) as tc:
        login_test_client(tc)
        with pytest.raises(Exception) as exc_info:
            with tc.websocket_connect(
                "/ws/events", headers={"Host": LAN_HOST_HEADER, "Origin": origin}
            ) as ws:
                ws.receive_json()
        assert getattr(exc_info.value, "code", None) == 1008
