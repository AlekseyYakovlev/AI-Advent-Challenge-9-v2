"""Portable stdio MCP fixture server with selectable healthy and failing behaviors.

Run as ``python mcp_stdio_server.py <mode>`` where mode is one of:
ok, tools_extra, die_after, garbage, stderr_exit, hang, bad_protocol, slow_ping.

``ok`` serves exactly echo and add. ``tools_extra`` serves those two plus fail, slow, big
and noargs, which exercise error, timeout, oversized-result and no-argument tool calls.
``slow_ping`` completes the handshake but answers each ping only after a blocking delay
(``argv[2]`` seconds, default 3), like a single-threaded server busy with long work.
"""

import asyncio
import json
import os
import sys
import threading
import time

USAGE = (
    "usage: mcp_stdio_server.py "
    "<ok|tools_extra|die_after|garbage|stderr_exit|hang|bad_protocol|slow_ping [delay]>\n"
)


def _run_fastmcp_server(extra: bool = False) -> None:
    """Serve echo and add over stdio using the MCP SDK's FastMCP, plus more when extra is set."""
    from mcp.server.fastmcp import FastMCP

    sys.stderr.write("fixture-server starting\n")
    sys.stderr.flush()

    server = FastMCP("fixture-server")

    @server.tool()
    def echo(text: str) -> str:
        """Echo the given text back."""
        return text

    @server.tool()
    def add(a: int, b: int = 0) -> int:
        """Add two integers."""
        return a + b

    if extra:

        @server.tool()
        def fail() -> str:
            """Always raise so the server reports isError."""
            raise ValueError("fixture failure")

        @server.tool()
        async def slow(seconds: float) -> str:
            """Sleep for the given seconds without blocking the server loop."""
            await asyncio.sleep(seconds)
            return "slow-done"

        @server.tool()
        def big(size: int) -> str:
            """Return a string of the requested length."""
            return "x" * size

        @server.tool()
        def noargs() -> str:
            """Take no arguments."""
            return "noargs-ok"

    server.run(transport="stdio")


def _mode_die_after() -> None:
    """Serve normally but kill the process about one second after start."""
    timer = threading.Timer(1.0, os._exit, args=(3,))
    timer.daemon = True
    timer.start()
    _run_fastmcp_server()


def _mode_garbage() -> None:
    """Write non-JSON-RPC text to stdout and exit."""
    sys.stdout.write("this is not json-rpc\n")
    sys.stdout.flush()
    sys.stderr.write("fatal: fixture exiting\n")
    sys.stderr.flush()
    sys.exit(0)


def _mode_stderr_exit() -> None:
    """Write 25 lines to stderr and exit with a failure code."""
    for index in range(1, 26):
        sys.stderr.write(f"stderr line {index}\n")
    sys.stderr.flush()
    sys.exit(1)


def _mode_hang() -> None:
    """Never write anything, so the client handshake must time out."""
    time.sleep(120)


def _mode_bad_protocol() -> None:
    """Answer initialize with an unsupported protocol version, then idle until EOF."""
    line = sys.stdin.readline()
    request = json.loads(line)
    reply = {
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {
            "protocolVersion": "1999-01-01",
            "capabilities": {},
            "serverInfo": {"name": "bad", "version": "0"},
        },
    }
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()
    while sys.stdin.readline():
        pass


def _mode_slow_ping() -> None:
    """Serve a raw JSON-RPC loop whose ping reply is delayed by a blocking sleep."""
    delay = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        request_id = request.get("id")
        if request_id is None:
            continue
        method = request.get("method")
        reply: dict[str, object] = {"jsonrpc": "2.0", "id": request_id}
        if method == "initialize":
            reply["result"] = {
                "protocolVersion": request.get("params", {}).get("protocolVersion"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "slow-ping", "version": "0"},
            }
        elif method == "tools/list":
            reply["result"] = {"tools": []}
        elif method == "ping":
            time.sleep(delay)
            reply["result"] = {}
        else:
            reply["error"] = {"code": -32601, "message": "Method not found"}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


def main() -> None:
    """Dispatch to the behavior selected by the first command-line argument."""
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    handlers = {
        "ok": _run_fastmcp_server,
        "tools_extra": lambda: _run_fastmcp_server(extra=True),
        "die_after": _mode_die_after,
        "garbage": _mode_garbage,
        "stderr_exit": _mode_stderr_exit,
        "hang": _mode_hang,
        "bad_protocol": _mode_bad_protocol,
        "slow_ping": _mode_slow_ping,
    }
    handler = handlers.get(mode)
    if handler is None:
        sys.stderr.write(USAGE)
        sys.exit(2)
    handler()


if __name__ == "__main__":
    main()
