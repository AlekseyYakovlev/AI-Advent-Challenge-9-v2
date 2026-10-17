"""Tests for the support desk MCP server: tools, re-read, startup failures and stdio exposure."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent import mcp_client, mcp_config
from agent.mcp_tools import build_mcp_toolset, call_mcp_tool
from agent.schemas import McpConnectionStatus
from agent.tools import TOOL_REGISTRY
from mcp_servers import support_desk
from shared.database import async_session_factory
from tests.conftest import _create_user

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SERVER: str = str(REPO_ROOT / "mcp_servers" / "support_desk.py")
SEED: Path = REPO_ROOT / "mcp_servers" / "support_data.json"
FOUR_TOOLS: list[str] = ["get_ticket", "get_user", "list_user_tickets", "search_tickets"]


def _configure(path: Path, max_chars: int = support_desk.DEFAULT_MAX_CHARS) -> None:
    support_desk.configure(support_desk.ServerConfig(str(path), max_chars))
    support_desk._cache = None


@pytest.fixture
def data_file(tmp_path: Path):
    path = tmp_path / "support_data.json"
    shutil.copyfile(SEED, path)
    _configure(path)
    yield path
    support_desk._cache = None


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: dict[str, Any], bump: int) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2 * bump), encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + bump * 2_000_000_000))


# ------------------------------------------------------------ direct tools


async def test_get_ticket_found(data_file: Path) -> None:
    text = await support_desk.get_ticket("T-1001")
    lines = text.splitlines()
    assert lines[0] == "Ticket T-1001: open, high priority"
    assert any(line.startswith("Subject: ") for line in lines)
    assert any(line.startswith("Error: 429") for line in lines)
    assert any(line.startswith("Client: U-1 @ivan") for line in lines)
    assert "Messages:" in lines


@pytest.mark.parametrize("value", ["t-1001", "#T-1001", "T1001"])
async def test_get_ticket_id_normalisation(data_file: Path, value: str) -> None:
    text = await support_desk.get_ticket(value)
    assert text.startswith("Ticket T-1001:")


async def test_get_ticket_not_found_and_malformed(data_file: Path) -> None:
    with pytest.raises(support_desk.ToolInputError, match="not found; call search_tickets"):
        await support_desk.get_ticket("T-9999")
    with pytest.raises(support_desk.ToolInputError, match="invalid ticket id 'T-12x'"):
        await support_desk.get_ticket("T-12x")


@pytest.mark.parametrize("value", ["ivan", "@ivan", "U-1"])
async def test_get_user_by_login_and_id(data_file: Path, value: str) -> None:
    text = await support_desk.get_user(value)
    assert text.splitlines()[0].startswith("User U-1 @ivan:")
    assert "Tickets (1, 1 open):" in text


async def test_get_user_unknown(data_file: Path) -> None:
    with pytest.raises(support_desk.ToolInputError, match="not found"):
        await support_desk.get_user("nobody")
    with pytest.raises(support_desk.ToolInputError, match="invalid user"):
        await support_desk.get_user("bad login!")


async def test_list_user_tickets_with_and_without_status(data_file: Path) -> None:
    text = await support_desk.list_user_tickets("olga")
    assert text.splitlines()[0] == "2 tickets for @olga (2 open)"
    assert "T-1002" in text and "T-1004" in text
    closed = await support_desk.list_user_tickets("petr", status="closed")
    assert closed.splitlines()[0] == "1 ticket for @petr with status closed"
    none = await support_desk.list_user_tickets("ivan", status="closed")
    assert none.strip() == "0 tickets for @ivan with status closed"


async def test_list_user_tickets_unknown_status(data_file: Path) -> None:
    with pytest.raises(support_desk.ToolInputError, match="unknown status 'weird': use one of"):
        await support_desk.list_user_tickets("ivan", status="weird")


async def test_search_tickets_hit_and_filters(data_file: Path) -> None:
    text = await support_desk.search_tickets("Origin")
    assert text.splitlines()[0] == "Found 1 tickets matching 'Origin'"
    assert "T-1002" in text and "@olga" in text
    narrowed = await support_desk.search_tickets("ключ", category="api", status="waiting")
    assert "T-1004" in narrowed
    with pytest.raises(support_desk.ToolInputError, match="unknown category"):
        await support_desk.search_tickets("x", category="weird")


async def test_search_tickets_zero_hits_not_empty(data_file: Path) -> None:
    text = await support_desk.search_tickets("zzzzqqq")
    assert text == "Found 0 tickets matching 'zzzzqqq'"


async def test_search_text_length_rejected(data_file: Path) -> None:
    for bad in ("", "   ", "x" * 101):
        with pytest.raises(support_desk.ToolInputError, match="search text must be 1-100"):
            await support_desk.search_tickets(bad)


async def test_echoed_input_is_truncated(data_file: Path) -> None:
    with pytest.raises(support_desk.ToolInputError) as info:
        await support_desk.get_ticket("T-" + "9" * 200)
    assert len(str(info.value)) < 200


# --------------------------------------------------------------- size cap


async def test_size_cap_truncates_with_marker(tmp_path: Path) -> None:
    data = _load(SEED)
    ticket = data["tickets"][0]
    ticket["messages"] = [
        {"at": "2026-10-12T08:15:00Z", "author": "client", "text": f"message {i} " + "w" * 80}
        for i in range(50)
    ]
    path = tmp_path / "big.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    _configure(path, max_chars=1000)
    text = await support_desk.get_ticket("T-1001")
    assert len(text) <= 1000
    assert text.rstrip().endswith("]")
    assert "[output truncated" in text


# ----------------------------------------------------------------- re-read


async def test_edit_is_picked_up_and_invalid_json_recovers(data_file: Path) -> None:
    first = await support_desk.get_ticket("T-1001")
    assert first.startswith("Ticket T-1001: open")

    data = _load(data_file)
    data["tickets"][0]["status"] = "closed"
    _write(data_file, data, bump=1)
    second = await support_desk.get_ticket("T-1001")
    assert second.startswith("Ticket T-1001: closed")

    data_file.write_text("{broken", encoding="utf-8")
    with pytest.raises(support_desk.ToolInputError, match="data file is not valid JSON"):
        await support_desk.get_ticket("T-1001")

    data["tickets"][0]["status"] = "in_progress"
    _write(data_file, data, bump=2)
    third = await support_desk.get_ticket("T-1001")
    assert third.startswith("Ticket T-1001: in_progress")


async def test_missing_file_after_start(data_file: Path) -> None:
    data_file.unlink()
    with pytest.raises(support_desk.ToolInputError, match="data file is missing"):
        await support_desk.get_ticket("T-1001")


async def test_structurally_invalid_data(data_file: Path) -> None:
    data = _load(data_file)
    data["tickets"][0]["client_id"] = "U-99"
    _write(data_file, data, bump=1)
    with pytest.raises(support_desk.ToolInputError, match="^data file is invalid"):
        await support_desk.get_ticket("T-1001")


# ------------------------------------------------------- startup failures


def _run_server(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, SERVER, *args],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=20,
    )


def test_startup_missing_file(tmp_path: Path) -> None:
    proc = _run_server(f"--data={tmp_path / 'nope.json'}")
    assert proc.returncode == 1
    assert b"data file not found" in proc.stderr


def test_startup_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{broken", encoding="utf-8")
    proc = _run_server(f"--data={path}")
    assert proc.returncode == 1
    assert b"not valid JSON" in proc.stderr


def test_startup_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "huge.json"
    path.write_text(" " * (support_desk.MAX_FILE_BYTES + 10), encoding="utf-8")
    proc = _run_server(f"--data={path}")
    assert proc.returncode == 1
    assert b"larger than 1 MiB" in proc.stderr


def test_startup_missing_data_argument() -> None:
    proc = _run_server()
    assert proc.returncode != 0
    assert b"--data" in proc.stderr


# -------------------------------------------------------- stdio round-trip


async def test_stdio_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "support_data.json"
    shutil.copyfile(SEED, path)
    server_args = [SERVER, f"--data={path}"]
    user_id = await _create_user("supportuser", "pw")
    async with async_session_factory() as session:
        row = await mcp_config.create_server(
            session, user_id, "support", sys.executable, server_args, {}, None, True
        )
        server_id = row.id
    connection = await mcp_client.connect_server(
        user_id, server_id, sys.executable, server_args, None, None
    )
    assert connection.status == McpConnectionStatus.CONNECTED, connection.error_message
    try:
        async with async_session_factory() as session:
            toolset = await build_mcp_toolset(session, user_id, set(TOOL_REGISTRY))
        for name in FOUR_TOOLS:
            assert f"mcp__support__{name}" in toolset.bindings
        binding = toolset.bindings["mcp__support__get_ticket"]
        found = await call_mcp_tool(binding, {"id": "T-1002"})
        assert found["ok"] is True
        assert "Cross-origin request rejected" in found["text"]
        missing = await call_mcp_tool(binding, {"id": "T-9999"})
        assert missing["ok"] is False
        assert "not found" in missing["text"]
    finally:
        await mcp_client.disconnect_server(user_id, server_id)


# ------------------------------------------------------------- read-only


async def test_exactly_four_read_tools() -> None:
    tools = await support_desk.server.list_tools()
    assert sorted(t.name for t in tools) == FOUR_TOOLS
