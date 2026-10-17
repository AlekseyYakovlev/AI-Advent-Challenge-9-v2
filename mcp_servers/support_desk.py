"""Read-only support desk MCP server over a JSON file of users and tickets, served over stdio.

Usage:
    python mcp_servers/support_desk.py --data=<path to json> [--max-chars=18000]

This file is self-contained on purpose. It does not use the application logger or
settings modules from the shared package: that logger writes JSON to stdout, and stdout is
the MCP protocol channel here. Diagnostics go to stderr through ``sys.stderr.write``. This
is the documented exception to the project's logging rule, so the file has no print calls.
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, NoReturn

from mcp.server.fastmcp import FastMCP

DEFAULT_MAX_CHARS: int = 18000
MIN_MAX_CHARS: int = 1000
APP_RESULT_CAP: int = 20000
MAX_FILE_BYTES: int = 1024 * 1024
MAX_SEARCH_HITS: int = 20
MAX_SEARCH_TEXT: int = 100
ECHO_CHARS: int = 64
OPEN_STATUSES: frozenset[str] = frozenset({"open", "in_progress", "waiting"})
USER_FIELDS: tuple[str, ...] = (
    "id", "login", "name", "email", "plan", "account_status", "app_version", "os",
)  # fmt: skip
TICKET_FIELDS: tuple[str, ...] = (
    "id", "client_id", "subject", "category", "status", "priority",
    "created_at", "updated_at", "error_text",
)  # fmt: skip
MESSAGE_FIELDS: tuple[str, ...] = ("at", "author", "text")
_TICKET_ID = re.compile(r"^#?T-?(\d{1,6})$", re.IGNORECASE)
_CLIENT_ID = re.compile(r"^U-\d{1,6}$", re.IGNORECASE)
_LOGIN = re.compile(r"^@?([A-Za-z0-9][A-Za-z0-9._-]{0,31})$")


class ToolInputError(ValueError):
    """Raised when a tool argument or the data file is rejected; the message names the fix."""


@dataclass(frozen=True)
class ServerConfig:
    """Validated launch settings shared by every tool."""

    data_path: str
    max_chars: int


_config: ServerConfig | None = None
_cache: tuple[int, int, dict[str, Any]] | None = None

server = FastMCP("support", log_level="WARNING")


def configure(config: ServerConfig) -> None:
    """Store the validated launch settings and drop any cached data."""
    global _config, _cache
    _config = config
    _cache = None


def get_config() -> ServerConfig:
    """Return the launch settings or fail when the server is not configured."""
    if _config is None:
        raise RuntimeError("server is not configured")
    return _config


def fail(message: str) -> NoReturn:
    """Write the reason as the last stderr line and exit with code 1."""
    sys.stderr.write(message + "\n")
    sys.stderr.flush()
    sys.exit(1)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse and validate the launch arguments."""
    parser = argparse.ArgumentParser(
        prog="support_desk.py",
        description="Read-only support desk MCP server over stdio.",
        allow_abbrev=False,
    )
    parser.add_argument("--data", required=True, help="path to the JSON data file")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"result size limit in characters (minimum {MIN_MAX_CHARS})",
    )
    args = parser.parse_args(argv)
    if args.max_chars < MIN_MAX_CHARS:
        parser.error(f"--max-chars must be at least {MIN_MAX_CHARS}")
    return args


# ------------------------------------------------------------------ data file


def _invalid(reason: str) -> ToolInputError:
    return ToolInputError(f"data file is invalid: {reason}; fix the file and retry")


def _check_strings(item: Any, fields: tuple[str, ...], what: str) -> None:
    if not isinstance(item, dict):
        raise _invalid(f"{what} must be an object")
    for field in fields:
        if not isinstance(item.get(field), str):
            raise _invalid(f"{what} field '{field}' must be a string")


def validate_data(data: Any) -> None:
    """Check the shape of the parsed data file; raise ToolInputError on any violation."""
    if not isinstance(data, dict):
        raise _invalid("top level must be an object")
    users = data.get("users")
    tickets = data.get("tickets")
    if not isinstance(users, list) or not isinstance(tickets, list):
        raise _invalid("'users' and 'tickets' must be lists")
    user_ids: set[str] = set()
    for user in users:
        _check_strings(user, USER_FIELDS, "user")
        if user["id"] in user_ids:
            raise _invalid(f"duplicate user id {user['id'][:ECHO_CHARS]}")
        user_ids.add(user["id"])
    ticket_ids: set[str] = set()
    for ticket in tickets:
        _check_strings(ticket, TICKET_FIELDS, "ticket")
        if ticket["id"] in ticket_ids:
            raise _invalid(f"duplicate ticket id {ticket['id'][:ECHO_CHARS]}")
        ticket_ids.add(ticket["id"])
        if ticket["client_id"] not in user_ids:
            raise _invalid(f"ticket {ticket['id'][:ECHO_CHARS]} has an unknown client_id")
        messages = ticket.get("messages")
        if not isinstance(messages, list):
            raise _invalid(f"ticket {ticket['id'][:ECHO_CHARS]} 'messages' must be a list")
        for message in messages:
            _check_strings(message, MESSAGE_FIELDS, "message")


def read_data(path: str) -> dict[str, Any]:
    """Read, parse and validate the data file."""
    try:
        size = os.stat(path).st_size
        if size > MAX_FILE_BYTES:
            raise _invalid("larger than 1 MiB")
        with open(path, "rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        raise ToolInputError("data file is missing: restore it and retry") from None
    except OSError as exc:
        raise ToolInputError(f"data file cannot be read ({exc.strerror}): fix and retry") from None
    if len(raw) > MAX_FILE_BYTES:
        raise _invalid("larger than 1 MiB")
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ToolInputError(
            f"data file is not valid JSON (line {exc.lineno}): fix the file and retry"
        ) from None
    except UnicodeDecodeError:
        raise _invalid("not valid UTF-8") from None
    validate_data(data)
    return data


def load_db() -> dict[str, Any]:
    """Return the parsed data, re-reading the file when its mtime or size changed."""
    global _cache
    path = get_config().data_path
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        raise ToolInputError("data file is missing: restore it and retry") from None
    except OSError as exc:
        raise ToolInputError(f"data file cannot be read ({exc.strerror}): fix and retry") from None
    key = (stat.st_mtime_ns, stat.st_size)
    if _cache is not None and _cache[:2] == key:
        return _cache[2]
    data = read_data(path)
    _cache = (key[0], key[1], data)
    return data


# ----------------------------------------------------------------- formatting


def truncation_marker(shown: int, total: int) -> str:
    """Build the marker that tells the caller how much was shown."""
    return (
        f"[output truncated: showing {shown:,} of {total:,} characters. "
        "Call again with a narrower request to see the rest.]"
    )


def _cut_at_line(text: str, budget: int) -> str:
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    cut = text[:budget]
    newline = cut.rfind("\n")
    return cut[:newline] if newline > 0 else cut


def fit_text(summary: str, body: str, max_chars: int) -> str:
    """Join summary and body within max_chars, adding a truncation marker when cut."""
    summary = summary[: max_chars // 2]
    if not body:
        return summary
    full = summary + "\n" + body
    if len(full) <= max_chars:
        return full
    total = len(body)
    reserve = len(truncation_marker(total, total))
    budget = max_chars - len(summary) - 2 - reserve
    kept = _cut_at_line(body, budget)
    marker = truncation_marker(len(kept), total)
    return summary + "\n" + (kept + "\n" if kept else "") + marker


def _echo(value: str) -> str:
    return value[:ECHO_CHARS]


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _ticket_line(ticket: dict[str, Any], login: str | None = None) -> str:
    who = f" @{login}" if login is not None else ""
    return f"- {ticket['id']} [{ticket['status']}, {ticket['priority']}]{who} {ticket['subject']}"


def _newest_first(tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(tickets, key=lambda t: t["updated_at"], reverse=True)


def _user_by_id(db: dict[str, Any], client_id: str) -> dict[str, Any]:
    return next(u for u in db["users"] if u["id"] == client_id)


# ------------------------------------------------------------------- lookups


def normalise_ticket_id(value: str) -> str:
    """Return the canonical T-<digits> form or raise ToolInputError."""
    match = _TICKET_ID.match(value.strip())
    if match is None:
        raise ToolInputError(f"invalid ticket id '{_echo(value)}': use the form T-1001")
    return f"T-{match.group(1)}"


def find_user(db: dict[str, Any], value: str) -> dict[str, Any]:
    """Resolve a login (with or without @) or a client id to a user record."""
    text = value.strip()
    if _CLIENT_ID.match(text):
        wanted = text.upper()
        found = next((u for u in db["users"] if u["id"].upper() == wanted), None)
    else:
        match = _LOGIN.match(text)
        if match is None:
            raise ToolInputError(
                f"invalid user '{_echo(value)}': use a login like ivan or a client id like U-1"
            )
        login = match.group(1).lower()
        found = next((u for u in db["users"] if u["login"].lower() == login), None)
    if found is None:
        raise ToolInputError(
            f"user '{_echo(value)}' not found; call search_tickets to find the client's login"
        )
    return found


def _check_choice(db: dict[str, Any], value: str | None, field: str) -> str | None:
    if value is None:
        return None
    present = sorted({t[field] for t in db["tickets"]})
    wanted = value.strip().lower()
    if wanted not in present:
        raise ToolInputError(
            f"unknown {field} '{_echo(value)}': use one of {', '.join(present)}"
        )
    return wanted


# --------------------------------------------------------------------- tools


@server.tool(structured_output=False)
async def get_ticket(id: str) -> str:
    """Read-only: return one support ticket with its client, environment and message history.

    The id has the form T-1001 (case-insensitive, a leading # is accepted).
    """
    db = load_db()
    ticket_id = normalise_ticket_id(id)
    ticket = next((t for t in db["tickets"] if t["id"] == ticket_id), None)
    if ticket is None:
        raise ToolInputError(
            f"ticket '{_echo(id)}' not found; call search_tickets or list_user_tickets "
            "to find a valid id"
        )
    user = _user_by_id(db, ticket["client_id"])
    summary = f"Ticket {ticket['id']}: {ticket['status']}, {ticket['priority']} priority"
    lines = [
        f"Subject: {ticket['subject']}",
        f"Category: {ticket['category']}",
        f"Status: {ticket['status']}",
        f"Priority: {ticket['priority']}",
        f"Created: {ticket['created_at']}",
        f"Updated: {ticket['updated_at']}",
        f"Error: {ticket['error_text'] or 'none'}",
        f"Client: {user['id']} @{user['login']}, {user['name']}, {user['email']}",
        f"Plan: {user['plan']}; account status: {user['account_status']}; "
        f"app version: {user['app_version']}; OS: {user['os']}",
        "Messages:",
    ]
    lines += [f"- {m['at']} {m['author']}: {m['text']}" for m in ticket["messages"]]
    return fit_text(summary, "\n".join(lines), get_config().max_chars)


@server.tool(structured_output=False)
async def get_user(user: str) -> str:
    """Read-only: return one support client with plan, environment and the list of tickets.

    The user is a login such as ivan (or @ivan) or a client id such as U-1.
    """
    db = load_db()
    record = find_user(db, user)
    tickets = _newest_first([t for t in db["tickets"] if t["client_id"] == record["id"]])
    open_count = sum(1 for t in tickets if t["status"] in OPEN_STATUSES)
    summary = (
        f"User {record['id']} @{record['login']}: {record['name']}, "
        f"account status {record['account_status']}"
    )
    lines = [
        f"Email: {record['email']}",
        f"Plan: {record['plan']}",
        f"App version: {record['app_version']}",
        f"OS: {record['os']}",
        f"Tickets ({len(tickets)}, {open_count} open):",
    ]
    lines += [_ticket_line(t) for t in tickets]
    return fit_text(summary, "\n".join(lines), get_config().max_chars)


@server.tool(structured_output=False)
async def list_user_tickets(user: str, status: str | None = None) -> str:
    """Read-only: list the tickets of one client, newest update first.

    The user is a login such as ivan (or @ivan) or a client id such as U-1. The optional
    status filters by ticket status, for example open, in_progress, waiting or closed.
    """
    db = load_db()
    record = find_user(db, user)
    wanted = _check_choice(db, status, "status")
    tickets = _newest_first([t for t in db["tickets"] if t["client_id"] == record["id"]])
    if wanted is not None:
        tickets = [t for t in tickets if t["status"] == wanted]
        summary = f"{_plural(len(tickets), 'ticket')} for @{record['login']} with status {wanted}"
    else:
        open_count = sum(1 for t in tickets if t["status"] in OPEN_STATUSES)
        summary = f"{_plural(len(tickets), 'ticket')} for @{record['login']} ({open_count} open)"
    body = "\n".join(_ticket_line(t) for t in tickets)
    return fit_text(summary, body, get_config().max_chars)


@server.tool(structured_output=False)
async def search_tickets(
    text: str, status: str | None = None, category: str | None = None
) -> str:
    """Read-only: search tickets by text in subject, error text and messages (1-100 characters).

    The match is a case-insensitive substring. Optional status and category filters narrow
    the result. At most 20 tickets are listed, newest update first.
    """
    query = text.strip()
    if not 1 <= len(query) <= MAX_SEARCH_TEXT:
        raise ToolInputError("search text must be 1-100 characters")
    db = load_db()
    wanted_status = _check_choice(db, status, "status")
    wanted_category = _check_choice(db, category, "category")
    needle = query.lower()
    hits: list[dict[str, Any]] = []
    for ticket in _newest_first(db["tickets"]):
        if wanted_status is not None and ticket["status"] != wanted_status:
            continue
        if wanted_category is not None and ticket["category"] != wanted_category:
            continue
        haystack = [ticket["subject"], ticket["error_text"]]
        haystack += [m["text"] for m in ticket["messages"]]
        if any(needle in part.lower() for part in haystack):
            hits.append(ticket)
    summary = f"Found {len(hits)} tickets matching '{_echo(query)}'"
    if len(hits) > MAX_SEARCH_HITS:
        summary += f" (showing {MAX_SEARCH_HITS} of {len(hits)})"
    shown = hits[:MAX_SEARCH_HITS]
    body = "\n".join(_ticket_line(t, _user_by_id(db, t["client_id"])["login"]) for t in shown)
    return fit_text(summary, body, get_config().max_chars)


def main() -> None:
    """Validate launch arguments and the data file, then serve over stdio."""
    args = parse_args()
    path = os.path.realpath(args.data)
    if not os.path.exists(path):
        fail(f"support_desk: data file not found: {args.data}")
    if not os.path.isfile(path):
        fail(f"support_desk: data file is not a regular file: {args.data}")
    try:
        read_data(path)
    except ToolInputError as exc:
        fail(f"support_desk: {exc}")
    if args.max_chars > APP_RESULT_CAP:
        sys.stderr.write(
            f"warning: --max-chars={args.max_chars} exceeds the app limit of "
            f"{APP_RESULT_CAP} characters; the app will cut tool results at {APP_RESULT_CAP}\n"
        )
        sys.stderr.flush()
    configure(ServerConfig(data_path=path, max_chars=args.max_chars))
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
