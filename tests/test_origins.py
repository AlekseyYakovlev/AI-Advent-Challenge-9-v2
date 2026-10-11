"""Tests for the host-match Origin rule and its CORS, REST and WebSocket enforcement."""

import inspect

import pytest

import shared.origins
from shared.config import settings
from shared.origins import LOOPBACK_ORIGINS, is_allowed_origin

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
