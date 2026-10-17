"""Read a Pull Request from the user's GitHub MCP server in code, without the tool-result cut."""

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession
from mcp.shared.exceptions import McpError
from sqlmodel.ext.asyncio.session import AsyncSession

from agent import mcp_config
from agent.mcp_client import ensure_connected, get_live_session
from agent.schemas import McpConnectionStatus
from shared.logger import get_logger
from shared.models import McpServerConfig

logger = get_logger(__name__)

CODE_GITHUB_MCP_UNAVAILABLE: str = "GITHUB_MCP_UNAVAILABLE"
CODE_PR_NOT_FOUND: str = "PR_NOT_FOUND"
CODE_PR_TOO_LARGE: str = "PR_TOO_LARGE"

GITHUB_TOOL: str = "pull_request_read"
DEFAULT_SERVER_NAME: str = "github"
FILES_PER_PAGE: int = 100
MAX_FILE_PAGES: int = 30
REVIEW_MCP_TIMEOUT: float = 120.0

MSG_SERVER_NOT_CONFIGURED: str = (
    "Сервер GitHub не настроен: добавьте и включите MCP сервер github в настройках «MCP серверы»."
)
MSG_SERVER_NOT_CONNECTED: str = "Сервер GitHub не подключился."
MSG_TOKEN_REJECTED: str = "Сервер GitHub отклонил токен (401)."
MSG_SERVER_TIMEOUT: str = "Сервер GitHub не ответил вовремя."
MSG_BAD_ANSWER: str = "Ответ сервера GitHub не распознан."
MSG_PR_NOT_FOUND: str = "Pull Request не найден."
MSG_PR_TOO_LARGE: str = "В Pull Request слишком много файлов для ревью."

REPO_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TOKEN_REJECTED_MARKERS: tuple[str, ...] = ("401", "Bad credentials")
NOT_FOUND_MARKERS: tuple[str, ...] = ("404", "Not Found")


class GithubReviewError(Exception):
    """A PR could not be read; `code` is machine-readable, `detail` is a Russian sentence."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code: str = code
        self.detail: str = detail


@dataclass(frozen=True)
class PrFile:
    """One changed file of a Pull Request."""

    filename: str
    status: str
    additions: int
    deletions: int
    patch: str | None
    previous_filename: str | None


@dataclass(frozen=True)
class PullRequestData:
    """Everything the review needs to know about a Pull Request."""

    repo: str
    number: int
    title: str
    body: str
    state: str
    draft: bool
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str
    changed_files: int
    files: list[PrFile]
    diff: str


def _unavailable(detail: str) -> GithubReviewError:
    """Build the error for a GitHub server that cannot serve the request."""
    return GithubReviewError(CODE_GITHUB_MCP_UNAVAILABLE, detail)


def _validate_repo(repo: str) -> tuple[str, str]:
    """Split `owner/name`, rejecting anything outside the allowed character set."""
    if not REPO_PATTERN.match(repo):
        raise ValueError("repo must look like owner/name")
    owner, name = repo.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise ValueError("repo must look like owner/name")
    return owner, name


async def resolve_github_server(
    session: AsyncSession,
    user_id: int,
    server_id: int | None,
) -> McpServerConfig:
    """Return the user's enabled GitHub server row or raise GITHUB_MCP_UNAVAILABLE."""
    row: McpServerConfig | None = None
    if server_id is None:
        for candidate in await mcp_config.list_servers(session, user_id):
            if candidate.name == DEFAULT_SERVER_NAME and candidate.enabled:
                row = candidate
                break
    else:
        found = await mcp_config.get_server(session, user_id, server_id)
        if found is not None and found.enabled:
            row = found
    if row is None or row.id is None:
        raise _unavailable(MSG_SERVER_NOT_CONFIGURED)
    return row


async def _live_session(user_id: int, row: McpServerConfig) -> ClientSession:
    """Connect the server on demand and return its live session."""
    assert row.id is not None
    result = await ensure_connected(
        user_id,
        row.id,
        row.command,
        mcp_config.load_args(row),
        mcp_config.load_env(row) or None,
        row.cwd,
    )
    if result.status != McpConnectionStatus.CONNECTED:
        raise _unavailable(MSG_SERVER_NOT_CONNECTED)
    live = get_live_session(user_id, row.id)
    if live is None:
        raise _unavailable(MSG_SERVER_NOT_CONNECTED)
    return live


def _flatten_text(result: Any) -> str:
    """Join the text items of a tool result; any other item type is an unusable answer."""
    parts: list[str] = []
    for item in result.content or []:
        if getattr(item, "type", None) != "text":
            raise _unavailable(MSG_BAD_ANSWER)
        parts.append(getattr(item, "text", ""))
    return "\n".join(parts)


def _raise_for_error_text(text: str) -> None:
    """Map the text of a failed tool result to a distinct error."""
    if any(marker in text for marker in TOKEN_REJECTED_MARKERS):
        raise _unavailable(MSG_TOKEN_REJECTED)
    if any(marker in text for marker in NOT_FOUND_MARKERS):
        raise GithubReviewError(CODE_PR_NOT_FOUND, MSG_PR_NOT_FOUND)
    raise _unavailable(MSG_BAD_ANSWER)


async def _read(
    session: ClientSession,
    user_id: int,
    server_id: int,
    method: str,
    owner: str,
    name: str,
    number: int,
    page: int | None = None,
) -> str:
    """Call pull_request_read once and return the whole answer text, uncut."""
    arguments: dict[str, Any] = {
        "method": method,
        "owner": owner,
        "repo": name,
        "pullNumber": number,
    }
    if page is not None:
        arguments["page"] = page
        arguments["perPage"] = FILES_PER_PAGE
    try:
        result = await asyncio.wait_for(
            session.call_tool(GITHUB_TOOL, arguments=arguments),
            REVIEW_MCP_TIMEOUT,
        )
    except TimeoutError:
        logger.warning("review_github_failed", error_type="TimeoutError")
        raise _unavailable(MSG_SERVER_TIMEOUT) from None
    except McpError:
        logger.warning("review_github_failed", error_type="McpError")
        raise _unavailable(MSG_SERVER_NOT_CONNECTED) from None

    text = _flatten_text(result)
    logger.info(
        "review_github_call",
        user_id=user_id,
        server_id=server_id,
        method=method,
        chars=len(text),
    )
    if result.isError:
        _raise_for_error_text(text)
    return text


def _parse_json(text: str) -> Any:
    """Parse a JSON answer or report it as not understood."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("review_github_failed", error_type="JSONDecodeError")
        raise _unavailable(MSG_BAD_ANSWER) from None


def _as_str(value: Any) -> str:
    """Return a string field, mapping null to an empty string."""
    return "" if value is None else str(value)


def _as_int(value: Any) -> int:
    """Return an int field, mapping anything unusable to 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _parse_pr(repo: str, number: int, text: str) -> dict[str, Any]:
    """Parse the `get` answer into the fields of PullRequestData (without files and diff)."""
    data = _parse_json(text)
    try:
        head = data["head"]
        base = data.get("base") or {}
        return {
            "repo": repo,
            "number": number,
            "title": _as_str(data.get("title")),
            "body": _as_str(data.get("body")),
            "state": _as_str(data.get("state")),
            "draft": bool(data.get("draft", False)),
            "base_ref": _as_str(base.get("ref")),
            "base_sha": _as_str(base.get("sha")),
            "head_ref": _as_str(head.get("ref")),
            "head_sha": _as_str(head["sha"]),
            "changed_files": _as_int(data.get("changed_files")),
        }
    except (KeyError, TypeError, AttributeError):
        logger.warning("review_github_failed", error_type="BadShape")
        raise _unavailable(MSG_BAD_ANSWER) from None


def _parse_file(item: Any) -> PrFile:
    """Parse one `get_files` item."""
    try:
        previous = item.get("previous_filename")
        patch = item.get("patch")
        return PrFile(
            filename=_as_str(item["filename"]),
            status=_as_str(item.get("status")),
            additions=_as_int(item.get("additions")),
            deletions=_as_int(item.get("deletions")),
            patch=None if patch is None else str(patch),
            previous_filename=None if previous is None else str(previous),
        )
    except (KeyError, TypeError, AttributeError):
        logger.warning("review_github_failed", error_type="BadShape")
        raise _unavailable(MSG_BAD_ANSWER) from None


async def _read_files(
    session: ClientSession,
    user_id: int,
    server_id: int,
    owner: str,
    name: str,
    number: int,
    changed_files: int,
) -> list[PrFile]:
    """Page through `get_files` until the list is complete."""
    files: list[PrFile] = []
    for page in range(1, MAX_FILE_PAGES + 1):
        text = await _read(session, user_id, server_id, "get_files", owner, name, number, page)
        items = _parse_json(text)
        if not isinstance(items, list):
            raise _unavailable(MSG_BAD_ANSWER)
        files.extend(_parse_file(item) for item in items)
        if len(items) < FILES_PER_PAGE:
            return files
        if changed_files and len(files) >= changed_files:
            return files
    raise GithubReviewError(CODE_PR_TOO_LARGE, MSG_PR_TOO_LARGE)


async def fetch_pull_request(
    session: AsyncSession,
    user_id: int,
    server_id: int | None,
    repo: str,
    number: int,
) -> PullRequestData:
    """Return the metadata, the full file list and the whole diff of a Pull Request."""
    owner, name = _validate_repo(repo)
    row = await resolve_github_server(session, user_id, server_id)
    assert row.id is not None
    live = await _live_session(user_id, row)

    meta = _parse_pr(repo, number, await _read(live, user_id, row.id, "get", owner, name, number))
    files = await _read_files(live, user_id, row.id, owner, name, number, meta["changed_files"])
    diff = await _read(live, user_id, row.id, "get_diff", owner, name, number)
    return PullRequestData(files=files, diff=diff, **meta)


async def fetch_head_sha(
    session: AsyncSession,
    user_id: int,
    server_id: int | None,
    repo: str,
    number: int,
) -> str:
    """Return the current head commit SHA of a Pull Request with a single `get` call."""
    owner, name = _validate_repo(repo)
    row = await resolve_github_server(session, user_id, server_id)
    assert row.id is not None
    live = await _live_session(user_id, row)
    meta = _parse_pr(repo, number, await _read(live, user_id, row.id, "get", owner, name, number))
    return str(meta["head_sha"])
