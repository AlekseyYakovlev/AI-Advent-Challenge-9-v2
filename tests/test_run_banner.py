"""Tests for run.py startup output and host binding in loopback and LAN modes."""

import inspect
from unittest.mock import AsyncMock, patch

import pytest

import run
import ui.main
from shared.config import settings

RULE = "=" * 60


def _run_main(capsys: pytest.CaptureFixture[str], credentials: object = None) -> tuple[str, object]:
    with patch("run.cleanup_port"), patch(
        "run.bootstrap_admin_if_needed",
        new_callable=AsyncMock,
        return_value=credentials,
    ), patch("run.uvicorn.run") as mock_run:
        run.main()
    return capsys.readouterr().out, mock_run


def test_loopback_output_is_golden(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Loopback mode prints exactly the three legacy lines."""
    monkeypatch.setattr(settings, "BIND_HOST", "127.0.0.1")
    with patch("run.reachable_urls") as urls_mock:
        out, mock_run = _run_main(capsys)
    expected = (
        f"Starting UI on http://127.0.0.1:{settings.UI_PORT}\n"
        f"Agent will be auto-started on port {settings.AGENT_PORT}\n"
        "Press Ctrl+C to stop.\n"
    )
    assert out == expected
    urls_mock.assert_not_called()
    assert mock_run.call_args.kwargs["host"] == "127.0.0.1"


def test_lan_banner_layout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """LAN mode prints the framed warning, URL list and binds all interfaces."""
    monkeypatch.setattr(settings, "BIND_HOST", "0.0.0.0")
    urls = ["http://192.168.1.5:1", "http://PC:1", "http://127.0.0.1:1"]
    with patch("run.reachable_urls", return_value=urls) as urls_mock:
        out, mock_run = _run_main(capsys)
    assert out.splitlines() == [
        RULE,
        "WARNING: LAN mode is ON (BIND_HOST=0.0.0.0)",
        "The app is reachable by everyone on your network.",
        "Traffic is plain HTTP (no HTTPS): do not use on public networks.",
        "To go back to local-only access, remove BIND_HOST from .env",
        "or set BIND_HOST=127.0.0.1, then restart.",
        RULE,
        "Starting UI on:",
        *[f"    {u}" for u in urls],
        f"Agent will be auto-started on port {settings.AGENT_PORT}",
        "Press Ctrl+C to stop.",
    ]
    out.encode("ascii")
    assert "Starting UI on http://" not in out
    assert mock_run.call_args.kwargs["host"] == "0.0.0.0"
    urls_mock.assert_called_once_with(settings.UI_PORT)


def test_lan_bootstrap_block_first(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bootstrap admin block precedes the LAN warning."""
    monkeypatch.setattr(settings, "BIND_HOST", "0.0.0.0")
    with patch("run.reachable_urls", return_value=["http://127.0.0.1:1"]):
        out, _ = _run_main(capsys, credentials=("admin", "secret"))
    assert out.index("Bootstrap admin account created") < out.index("WARNING: LAN mode")


def test_ui_supervisor_uses_bind_host() -> None:
    """ui.main passes BIND_HOST to the agent supervisor."""
    assert "host=settings.BIND_HOST" in inspect.getsource(ui.main)
