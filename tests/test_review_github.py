"""Tests for the in-code Pull Request reader backed by the GitHub MCP server."""

import asyncio
import json
import os
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from agent import mcp_config, review_github
from agent.review_github import (
    CODE_GITHUB_MCP_UNAVAILABLE,
    CODE_PR_NOT_FOUND,
    CODE_PR_TOO_LARGE,
    FILES_PER_PAGE,
    MAX_FILE_PAGES,
    GithubReviewError,
    fetch_head_sha,
    fetch_pull_request,
    resolve_github_server,
)
from agent.schemas import McpConnectionStatus, McpConnectResult
from shared.database import async_session_factory
from tests.conftest import _create_user
from tests.test_mcp_real_github import (
    GITHUB_ARGS,
    GITHUB_MCP_EXE,
    OWNER,
    REPO,
    TOKEN_ENV,
    requires_real_github,
)

REPO_FULL = "octo/hello"


def _text_result(text: str, is_error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(isError=is_error, content=[SimpleNamespace(type="text", text=text)])


def _pr_json(changed_files: int = 2, body: str | None = "body") -> str:
    return json.dumps({
        "title": "Add thing",
        "body": body,
        "state": "open",
        "draft": False,
        "head": {"ref": "feature", "sha": "h" * 40},
        "base": {"ref": "main", "sha": "b" * 40},
        "changed_files": changed_files,
    })


def _files_json(count: int, offset: int = 0, patch: bool = True) -> str:
    items = []
    for index in range(count):
        item: dict[str, Any] = {
            "filename": f"f{offset + index}.py",
            "status": "modified",
            "additions": 1,
            "deletions": 0,
        }
        if patch:
            item["patch"] = "@@ -1 +1 @@"
        items.append(item)
    return json.dumps(items)


class FakeSession:
    """A session whose call_tool answers from a function of the arguments."""

    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        assert name == "pull_request_read"
        self.calls.append(arguments)
        return self.handler(arguments)


@pytest.fixture
async def github_user(monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    """A user with an enabled `github` server and a connect that always succeeds."""
    user_id = await _create_user("reviewgh", "pw")
    async with async_session_factory() as session:
        row = await mcp_config.create_server(
            session, user_id, "github", "gh-bin", ["stdio"], {"T": "x"}, None, True,
        )
        server_id = row.id

    async def fake_connect(*_args: Any, **_kwargs: Any) -> McpConnectResult:
        return McpConnectResult(status=McpConnectionStatus.CONNECTED)

    monkeypatch.setattr(review_github, "ensure_connected", fake_connect)
    return user_id, server_id


def _install(monkeypatch: pytest.MonkeyPatch, session: FakeSession | None) -> None:
    monkeypatch.setattr(review_github, "get_live_session", lambda *_a, **_k: session)


async def _fetch(user_id: int, server_id: int | None = None, number: int = 7) -> Any:
    async with async_session_factory() as session:
        return await fetch_pull_request(session, user_id, server_id, REPO_FULL, number)


def _standard_handler(files_pages: list[str], diff: str = "diff --git a b\n", changed: int = 2):
    def handler(arguments: dict[str, Any]) -> SimpleNamespace:
        method = arguments["method"]
        if method == "get":
            return _text_result(_pr_json(changed_files=changed))
        if method == "get_files":
            return _text_result(files_pages[arguments["page"] - 1])
        return _text_result(diff)

    return handler


async def test_fetch_returns_metadata_files_and_diff(
    github_user: tuple[int, int], monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, _ = github_user
    fake = FakeSession(_standard_handler([_files_json(2)]))
    _install(monkeypatch, fake)

    data = await _fetch(user_id)

    assert data.title == "Add thing"
    assert (data.base_ref, data.base_sha) == ("main", "b" * 40)
    assert (data.head_ref, data.head_sha) == ("feature", "h" * 40)
    assert data.changed_files == 2
    assert len(data.files) == 2
    assert data.diff.startswith("diff --git")
    assert [c["method"] for c in fake.calls] == ["get", "get_files", "get_diff"]
    assert fake.calls[0]["pullNumber"] == 7
    assert fake.calls[1]["perPage"] == 100


async def test_files_are_paged_and_missing_patch_is_none(
    github_user: tuple[int, int], monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, _ = github_user
    pages = [_files_json(100, 0), _files_json(100, 100), _files_json(37, 200, patch=False)]
    fake = FakeSession(_standard_handler(pages, changed=237))
    _install(monkeypatch, fake)

    data = await _fetch(user_id)

    file_calls = [c for c in fake.calls if c["method"] == "get_files"]
    assert [c["page"] for c in file_calls] == [1, 2, 3]
    assert len(data.files) == 237
    assert data.files[0].patch == "@@ -1 +1 @@"
    assert data.files[-1].patch is None


async def test_too_many_pages_raises_pr_too_large(
    github_user: tuple[int, int], monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, _ = github_user
    full_page = _files_json(FILES_PER_PAGE)
    fake = FakeSession(_standard_handler([full_page] * MAX_FILE_PAGES, changed=10**6))
    _install(monkeypatch, fake)

    with pytest.raises(GithubReviewError) as info:
        await _fetch(user_id)
    assert info.value.code == CODE_PR_TOO_LARGE


async def test_huge_diff_is_returned_unchanged(
    github_user: tuple[int, int], monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, _ = github_user
    diff = "diff --git a/x b/x\n" + "+" * (300_000 - 19)
    assert len(diff) == 300_000
    _install(monkeypatch, FakeSession(_standard_handler([_files_json(2)], diff=diff)))

    data = await _fetch(user_id)

    assert len(data.diff) == 300_000
    assert data.diff == diff


async def test_null_body_becomes_empty_string(
    github_user: tuple[int, int], monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, _ = github_user

    def handler(arguments: dict[str, Any]) -> SimpleNamespace:
        if arguments["method"] == "get":
            return _text_result(_pr_json(changed_files=0, body=None))
        if arguments["method"] == "get_files":
            return _text_result("[]")
        return _text_result("diff --git")

    _install(monkeypatch, FakeSession(handler))
    data = await _fetch(user_id)
    assert data.body == ""


@pytest.mark.parametrize(
    ("text", "code", "fragment"),
    [
        ("HTTP 401 Unauthorized", CODE_GITHUB_MCP_UNAVAILABLE, "токен"),
        ("Bad credentials", CODE_GITHUB_MCP_UNAVAILABLE, "токен"),
        ("failed: 404 Not Found", CODE_PR_NOT_FOUND, "не найден"),
        ("Not Found", CODE_PR_NOT_FOUND, "не найден"),
    ],
)
async def test_error_results_are_mapped(
    github_user: tuple[int, int],
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    code: str,
    fragment: str,
) -> None:
    user_id, _ = github_user
    _install(monkeypatch, FakeSession(lambda _a: _text_result(text, is_error=True)))

    with pytest.raises(GithubReviewError) as info:
        await _fetch(user_id)
    assert info.value.code == code
    assert fragment in info.value.detail


async def test_timeout_and_mcp_error_are_unavailable(
    github_user: tuple[int, int], monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, _ = github_user

    def raise_timeout(_a: dict[str, Any]) -> Any:
        raise asyncio.TimeoutError

    def raise_mcp(_a: dict[str, Any]) -> Any:
        raise McpError(ErrorData(code=-1, message="boom"))

    for handler in (raise_timeout, raise_mcp):
        _install(monkeypatch, FakeSession(handler))
        with pytest.raises(GithubReviewError) as info:
            await _fetch(user_id)
        assert info.value.code == CODE_GITHUB_MCP_UNAVAILABLE


@pytest.mark.parametrize("answer", ["not json", json.dumps({"title": "x", "head": {}})])
async def test_unparseable_get_is_not_understood(
    github_user: tuple[int, int], monkeypatch: pytest.MonkeyPatch, answer: str,
) -> None:
    user_id, _ = github_user
    _install(monkeypatch, FakeSession(lambda _a: _text_result(answer)))

    with pytest.raises(GithubReviewError) as info:
        await _fetch(user_id)
    assert info.value.code == CODE_GITHUB_MCP_UNAVAILABLE
    assert "не распознан" in info.value.detail


async def test_non_text_item_is_not_understood(
    github_user: tuple[int, int], monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, _ = github_user
    image = SimpleNamespace(isError=False, content=[SimpleNamespace(type="image")])
    _install(monkeypatch, FakeSession(lambda _a: image))

    with pytest.raises(GithubReviewError) as info:
        await _fetch(user_id)
    assert "не распознан" in info.value.detail


async def test_invalid_repo_raises_value_error_before_any_call(
    github_user: tuple[int, int], monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, _ = github_user
    fake = FakeSession(_standard_handler([_files_json(2)]))
    _install(monkeypatch, fake)

    async with async_session_factory() as session:
        for bad in ("noslash", "a/b/c", "a/ b", "../x"):
            with pytest.raises(ValueError):
                await fetch_pull_request(session, user_id, None, bad, 1)
    assert fake.calls == []


async def test_fetch_head_sha_makes_one_get_call(
    github_user: tuple[int, int], monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, _ = github_user
    fake = FakeSession(_standard_handler([_files_json(2)]))
    _install(monkeypatch, fake)

    async with async_session_factory() as session:
        sha = await fetch_head_sha(session, user_id, None, REPO_FULL, 7)

    assert sha == "h" * 40
    assert [c["method"] for c in fake.calls] == ["get"]


async def test_resolve_server_errors(github_user: tuple[int, int]) -> None:
    user_id, server_id = github_user
    other = await _create_user("reviewother", "pw")

    async with async_session_factory() as session:
        row = await resolve_github_server(session, user_id, None)
        assert row.id == server_id
        assert (await resolve_github_server(session, user_id, server_id)).id == server_id

        for who, sid in ((other, None), (other, server_id)):
            with pytest.raises(GithubReviewError) as info:
                await resolve_github_server(session, who, sid)
            assert info.value.code == CODE_GITHUB_MCP_UNAVAILABLE
            assert "не настроен" in info.value.detail

        row = await mcp_config.get_server(session, user_id, server_id)
        await mcp_config.update_server(session, row, enabled=False)
        for sid in (None, server_id):
            with pytest.raises(GithubReviewError) as info:
                await resolve_github_server(session, user_id, sid)
            assert info.value.code == CODE_GITHUB_MCP_UNAVAILABLE


async def test_failed_connect_and_missing_session_are_unavailable(
    github_user: tuple[int, int], monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, _ = github_user

    async def failing_connect(*_args: Any, **_kwargs: Any) -> McpConnectResult:
        return McpConnectResult(status=McpConnectionStatus.ERROR)

    monkeypatch.setattr(review_github, "ensure_connected", failing_connect)
    with pytest.raises(GithubReviewError) as info:
        await _fetch(user_id)
    assert info.value.code == CODE_GITHUB_MCP_UNAVAILABLE
    assert "не подключился" in info.value.detail

    async def ok_connect(*_args: Any, **_kwargs: Any) -> McpConnectResult:
        return McpConnectResult(status=McpConnectionStatus.CONNECTED)

    monkeypatch.setattr(review_github, "ensure_connected", ok_connect)
    _install(monkeypatch, None)
    with pytest.raises(GithubReviewError) as info:
        await _fetch(user_id)
    assert info.value.code == CODE_GITHUB_MCP_UNAVAILABLE


@requires_real_github
@pytest.mark.skipif(
    not os.environ.get("GITHUB_REVIEW_TEST_PR"),
    reason="set GITHUB_REVIEW_TEST_PR to an existing Pull Request number",
)
async def test_real_binary_reads_a_pull_request() -> None:
    from agent import mcp_client

    user_id = await _create_user("realreview", "pw")
    async with async_session_factory() as session:
        row = await mcp_config.create_server(
            session, user_id, "github", GITHUB_MCP_EXE, GITHUB_ARGS,
            {TOKEN_ENV: os.environ[TOKEN_ENV]}, None, True,
        )
        server_id = row.id
    try:
        async with async_session_factory() as session:
            data = await fetch_pull_request(
                session, user_id, None, f"{OWNER}/{REPO}", int(os.environ["GITHUB_REVIEW_TEST_PR"]),
            )
        assert data.diff.startswith("diff --git")
        assert len(data.files) == data.changed_files
    finally:
        await mcp_client.disconnect_server(user_id, server_id)
