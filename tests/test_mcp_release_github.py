"""Tests for github_create_draft_release against a respx-mocked GitHub REST API."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from mcp_servers import project_git
from mcp_servers.project_git import ToolInputError

TOKEN: str = "ghp_secretTOKENvalue123"
SHA: str = "a" * 40
URL: str = "https://api.github.com/repos/o/r/releases"
HTML_URL: str = "https://github.com/o/r/releases/tag/untagged-1"


@pytest.fixture(autouse=True)
def _configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_git.configure(str(tmp_path), "git", 18000, 10.0, allow_release=True)
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", TOKEN)


async def _create(**overrides: str) -> str:
    args = {
        "owner": "o", "repo": "r", "tag": "v1.1", "name": "v1.1: Title", "body": "Body",
        "commit": SHA,
    }  # fmt: skip
    args.update(overrides)
    return await project_git.github_create_draft_release(**args)


async def test_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    with pytest.raises(
        ToolInputError, match="^GITHUB_PERSONAL_ACCESS_TOKEN is not set in this server's environment"
    ):
        await _create()


@respx.mock
async def test_creates_draft_with_expected_payload_and_headers() -> None:
    respx.get(URL).respond(200, json=[])
    post = respx.post(URL).respond(
        201, json={"html_url": HTML_URL, "id": 7, "draft": True}
    )
    text = await _create()
    assert text.splitlines()[0] == f"Draft release created: {HTML_URL}"
    assert json.loads(text.splitlines()[1]) == {"html_url": HTML_URL, "id": 7, "draft": True}
    request = post.calls.last.request
    payload = json.loads(request.content)
    assert payload == {
        "tag_name": "v1.1",
        "target_commitish": SHA,
        "name": "v1.1: Title",
        "body": "Body",
        "draft": True,
        "prerelease": False,
    }
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"


@respx.mock
async def test_existing_release_for_tag_is_not_duplicated() -> None:
    respx.get(URL).respond(
        200, json=[{"tag_name": "v1.1", "html_url": HTML_URL, "id": 5, "draft": True}]
    )
    post = respx.post(URL).respond(201, json={})
    text = await _create()
    assert text.splitlines()[0] == f"Draft release already exists: {HTML_URL}"
    assert json.loads(text.splitlines()[1])["id"] == 5
    assert not post.called


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "GitHub rejected the token (401)"),
        (403, "no write access to o/r (403): the token needs Contents: write"),
        (404, "no write access to o/r (404): the token needs Contents: write"),
    ],
)
@respx.mock
async def test_post_error_statuses(status: int, reason: str) -> None:
    respx.get(URL).respond(200, json=[])
    respx.post(URL).respond(status, json={"message": "nope"})
    with pytest.raises(ToolInputError) as caught:
        await _create()
    assert str(caught.value) == reason
    assert TOKEN not in str(caught.value)


@respx.mock
async def test_get_unauthorized_maps_like_post() -> None:
    respx.get(URL).respond(401, json={"message": "Bad credentials"})
    with pytest.raises(ToolInputError, match=r"rejected the token \(401\)"):
        await _create()


@respx.mock
async def test_validation_failed() -> None:
    respx.get(URL).respond(200, json=[])
    respx.post(URL).respond(422, json={"message": "Validation Failed"})
    with pytest.raises(ToolInputError) as caught:
        await _create()
    assert str(caught.value) == "GitHub validation failed (422): Validation Failed"


@respx.mock
async def test_other_status_and_transport_error() -> None:
    respx.get(URL).respond(200, json=[])
    respx.post(URL).respond(500, text="boom")
    with pytest.raises(ToolInputError, match="GitHub answered 500"):
        await _create()
    respx.post(URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(ToolInputError) as caught:
        await _create()
    assert str(caught.value) == "GitHub request failed: ConnectError"


@respx.mock
async def test_token_never_leaks() -> None:
    respx.get(URL).respond(200, json=[])
    respx.post(URL).respond(403, json={"message": f"token {TOKEN} bad"})
    with pytest.raises(ToolInputError) as caught:
        await _create()
    assert TOKEN not in str(caught.value)
    respx.post(URL).respond(422, json={"message": f"echo {TOKEN}"})
    with pytest.raises(ToolInputError) as caught_422:
        await _create()
    assert str(caught_422.value).startswith("GitHub validation failed (422)")


@respx.mock
@pytest.mark.parametrize(
    "overrides",
    [
        {"owner": "o/x"},
        {"repo": "../r"},
        {"repo": ".."},
        {"tag": "1.1"},
        {"name": ""},
        {"name": "two\nlines"},
        {"body": "x" * 125001},
        {"commit": "abc123"},
        {"commit": "A" * 40},
    ],
)
async def test_invalid_arguments_make_no_http_call(overrides: dict[str, str]) -> None:
    route = respx.route().respond(200, json=[])
    with pytest.raises(ToolInputError):
        await _create(**overrides)
    assert not route.called
