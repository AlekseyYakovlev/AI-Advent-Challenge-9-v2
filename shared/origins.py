"""Origin acceptance rule: an Origin is allowed when its host matches the request Host."""

from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from shared.config import settings

LOOPBACK_ORIGINS: tuple[str, ...] = (
    f"http://localhost:{settings.UI_PORT}",
    f"http://127.0.0.1:{settings.UI_PORT}",
)


def _host_of(host_header: str | None) -> str | None:
    """Return the lowercased hostname of a Host header, or None when it is unusable."""
    if not host_header:
        return None
    try:
        parts = urlsplit("//" + host_header)
        _ = parts.port
        return parts.hostname
    except ValueError:
        return None


def is_allowed_origin(origin: str, host_header: str | None) -> bool:
    """Return True when the origin is a loopback UI origin or matches the Host on the UI port.

    A missing Origin header is not handled here: callers decide that case.
    """
    if origin in LOOPBACK_ORIGINS:
        return True
    try:
        parts = urlsplit(origin)
        origin_port = parts.port
        origin_host = parts.hostname
    except ValueError:
        return False
    if parts.scheme != "http" or origin_host is None:
        return False
    if parts.path or parts.query or parts.fragment or "@" in parts.netloc:
        return False
    request_host = _host_of(host_header)
    if request_host is None:
        return False
    return origin_host == request_host and origin_port == settings.UI_PORT


class HostMatchCORSMiddleware:
    """CORS middleware that reflects an Origin only when it passes is_allowed_origin."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin is None:
            await self.app(scope, receive, send)
            return
        allowed = is_allowed_origin(origin, headers.get("host"))
        cors = CORSMiddleware(
            self.app,
            allow_origins=[origin] if allowed else [],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["Retry-After"],
        )
        await cors(scope, receive, send)
