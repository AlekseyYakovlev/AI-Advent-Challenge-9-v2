"""Portable stdio MCP fixture server with selectable healthy and failing behaviors.

Run as ``python mcp_stdio_server.py <mode>`` where mode is one of:
ok, die_after, garbage, stderr_exit, hang, bad_protocol.
"""

import json
import os
import sys
import threading
import time

USAGE = "usage: mcp_stdio_server.py <ok|die_after|garbage|stderr_exit|hang|bad_protocol>\n"


def _run_fastmcp_server() -> None:
    """Serve two tools over stdio using the MCP SDK's FastMCP."""
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


def main() -> None:
    """Dispatch to the behavior selected by the first command-line argument."""
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    handlers = {
        "ok": _run_fastmcp_server,
        "die_after": _mode_die_after,
        "garbage": _mode_garbage,
        "stderr_exit": _mode_stderr_exit,
        "hang": _mode_hang,
        "bad_protocol": _mode_bad_protocol,
    }
    handler = handlers.get(mode)
    if handler is None:
        sys.stderr.write(USAGE)
        sys.exit(2)
    handler()


if __name__ == "__main__":
    main()
