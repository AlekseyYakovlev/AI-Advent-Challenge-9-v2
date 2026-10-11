"""Discover the addresses where the service is reachable on the network."""

import ipaddress
import socket

import psutil

from shared.config import BIND_LOOPBACK
from shared.logger import get_logger

logger = get_logger(__name__)


def lan_ipv4_addresses() -> list[str]:
    """Return non-loopback IPv4 addresses of active interfaces, first-seen order."""
    try:
        stats = psutil.net_if_stats()
        interfaces = psutil.net_if_addrs()
    except (OSError, psutil.Error) as exc:
        logger.warning("network_discovery_failed", error=str(exc))
        return []

    result: list[str] = []
    for name, entries in interfaces.items():
        iface_stats = stats.get(name)
        if iface_stats is None or not iface_stats.isup:
            continue
        for entry in entries:
            if entry.family != socket.AF_INET:
                continue
            try:
                if ipaddress.ip_address(entry.address).is_loopback:
                    continue
            except ValueError:
                continue
            if entry.address not in result:
                result.append(entry.address)
    return result


def reachable_urls(port: int) -> list[str]:
    """Return URLs for each LAN address, the machine name and loopback."""
    urls = [f"http://{ip}:{port}" for ip in lan_ipv4_addresses()]
    try:
        name = socket.gethostname()
    except OSError:
        name = ""
    if name:
        urls.append(f"http://{name}:{port}")
    urls.append(f"http://{BIND_LOOPBACK}:{port}")
    return urls
