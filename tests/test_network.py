"""Tests for shared.network address discovery."""

import socket
from types import SimpleNamespace

import psutil
import pytest

from shared import network


def _addr(address: str, family: int = socket.AF_INET) -> SimpleNamespace:
    return SimpleNamespace(family=family, address=address)


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    addrs: dict[str, list[SimpleNamespace]],
    up: dict[str, bool],
    hostname: str = "DESKTOP-TEST",
) -> None:
    stats = {k: SimpleNamespace(isup=v) for k, v in up.items()}
    fake = SimpleNamespace(
        net_if_addrs=lambda: addrs,
        net_if_stats=lambda: stats,
        Error=psutil.Error,
    )
    monkeypatch.setattr(network, "psutil", fake)
    monkeypatch.setattr(network.socket, "gethostname", lambda: hostname)


def test_up_interface_included(monkeypatch: pytest.MonkeyPatch) -> None:
    """An up interface IPv4 address is listed."""
    _patch(monkeypatch, {"eth": [_addr("192.168.88.224")]}, {"eth": True})
    assert network.lan_ipv4_addresses() == ["192.168.88.224"]


def test_vpn_included(monkeypatch: pytest.MonkeyPatch) -> None:
    """An up VPN interface address is listed."""
    _patch(monkeypatch, {"vpn": [_addr("10.8.1.1")]}, {"vpn": True})
    assert network.lan_ipv4_addresses() == ["10.8.1.1"]


def test_link_local_included(monkeypatch: pytest.MonkeyPatch) -> None:
    """Link-local addresses on up interfaces are not filtered."""
    _patch(monkeypatch, {"eth": [_addr("169.254.10.20")]}, {"eth": True})
    assert network.lan_ipv4_addresses() == ["169.254.10.20"]


def test_down_interface_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Down interfaces are skipped."""
    _patch(
        monkeypatch,
        {"a": [_addr("169.254.5.5")], "b": [_addr("192.168.7.7")]},
        {"a": False, "b": False},
    )
    assert network.lan_ipv4_addresses() == []


def test_loopback_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loopback address is skipped."""
    _patch(monkeypatch, {"lo": [_addr("127.0.0.1")]}, {"lo": True})
    assert network.lan_ipv4_addresses() == []


def test_non_ipv4_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """IPv6 and other families are ignored."""
    _patch(
        monkeypatch,
        {"eth": [_addr("fe80::1", socket.AF_INET6), _addr("aa:bb", -1)]},
        {"eth": True},
    )
    assert network.lan_ipv4_addresses() == []


def test_missing_stats_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    """An interface without stats is skipped."""
    _patch(monkeypatch, {"eth": [_addr("192.168.1.2")]}, {})
    assert network.lan_ipv4_addresses() == []


def test_duplicates_listed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same address on two interfaces appears once, in iteration order."""
    _patch(
        monkeypatch,
        {"a": [_addr("10.0.0.2")], "b": [_addr("10.0.0.2"), _addr("10.0.0.3")]},
        {"a": True, "b": True},
    )
    assert network.lan_ipv4_addresses() == ["10.0.0.2", "10.0.0.3"]


@pytest.mark.parametrize("exc", [OSError("boom"), psutil.Error("boom")])
def test_discovery_failure_returns_empty(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """Discovery errors degrade to an empty list."""
    def raiser() -> None:
        raise exc

    fake = SimpleNamespace(net_if_addrs=raiser, net_if_stats=lambda: {}, Error=psutil.Error)
    monkeypatch.setattr(network, "psutil", fake)
    assert network.lan_ipv4_addresses() == []


def test_reachable_urls_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """URLs are LAN addresses, machine name, then loopback."""
    _patch(
        monkeypatch,
        {"a": [_addr("192.168.88.224")], "b": [_addr("10.8.1.1")]},
        {"a": True, "b": True},
    )
    assert network.reachable_urls(18000) == [
        "http://192.168.88.224:18000",
        "http://10.8.1.1:18000",
        "http://DESKTOP-TEST:18000",
        "http://127.0.0.1:18000",
    ]


def test_hostname_empty_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty machine name is omitted."""
    _patch(monkeypatch, {"a": [_addr("10.0.0.2")]}, {"a": True}, hostname="")
    assert network.reachable_urls(18000) == [
        "http://10.0.0.2:18000",
        "http://127.0.0.1:18000",
    ]


def test_hostname_oserror_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hostname lookup failure omits only the name line."""
    _patch(monkeypatch, {"a": [_addr("10.0.0.2")]}, {"a": True})

    def raiser() -> str:
        raise OSError("no name")

    monkeypatch.setattr(network.socket, "gethostname", raiser)
    assert network.reachable_urls(18000) == [
        "http://10.0.0.2:18000",
        "http://127.0.0.1:18000",
    ]
