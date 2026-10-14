"""Tests for the review route, the review settings routes and the code index routes."""

import asyncio
import shutil
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from agent import code_index, review_api, review_service
from agent.main import app
from agent.review_service import ReviewServiceError
from kb_helpers import seed_kb
from shared.database import async_session_factory
from shared.models import KbStatus, LlmProvider, ReviewCodeIndex, ReviewConfig
from test_code_git import Repos

REVIEWS = "/api/v1/reviews"
CONFIG = "/api/v1/reviews/config"
INDEX = "/api/v1/reviews/code-index"
REFRESH = "/api/v1/reviews/code-index/refresh"
BODY = {"repo": "aleksey/demo", "pr_number": 7, "head_sha": ""}
SUCCESS_KEYS = {
    "repo", "pr_number", "head_sha", "head_changed", "base_ref", "base_commit", "provider",
    "model", "sections", "sources", "docs_used", "parts", "unreviewed_files", "notes",
}
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def canned(**override: Any) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "repo": "aleksey/demo", "pr_number": 7, "head_sha": "a" * 40, "head_changed": False,
        "base_ref": "main", "base_commit": "b" * 40, "provider": "LM Studio", "model": "m",
        "sections": {"bugs": "b", "architecture": "a", "recommendations": "r"},
        "sources": {"docs": [], "code": []}, "docs_used": False, "parts": 1,
        "unreviewed_files": [], "notes": [],
    }
    reply.update(override)
    return reply


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch: pytest.MonkeyPatch) -> None:
    code_index.reset_state()
    review_api._review_locks.clear()

    async def no_model(provider: Any, timeout: float) -> str | None:
        return "loaded-model"

    monkeypatch.setattr(review_service, "_loaded_chat_model", no_model)


@pytest.fixture
def fake_review(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    async def fake(user_id: int, repo: str, pr_number: int, head_sha: str) -> dict[str, Any]:
        calls.append((user_id, repo, pr_number, head_sha))
        return canned()

    monkeypatch.setattr(review_service, "produce_review", fake)
    return calls


def api_client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def make_key(client: AsyncClient) -> dict[str, str]:
    resp = await client.post("/api/v1/api-keys", json={"name": "t"})
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['key']}"}


async def stored(user_id: int) -> ReviewConfig | None:
    async with async_session_factory() as session:
        return await session.get(ReviewConfig, user_id)


# ------------------------------------------------------------------ review route


async def test_review_requires_authentication() -> None:
    async with api_client() as anon:
        assert (await anon.post(REVIEWS, json=BODY)).status_code == 401
        bad = await anon.post(REVIEWS, json=BODY, headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401


async def test_review_success_with_api_key(
    authenticated_client: AsyncClient, fake_review: list[tuple[Any, ...]]
) -> None:
    headers = await make_key(authenticated_client)
    async with api_client() as caller:
        resp = await caller.post(REVIEWS, json={**BODY, "head_sha": "A" * 40}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert set(resp.json()) == SUCCESS_KEYS
    assert fake_review == [(authenticated_client.seeded_user_id, "aleksey/demo", 7, "A" * 40)]


async def test_session_cookie_also_works(
    authenticated_client: AsyncClient, fake_review: list[tuple[Any, ...]]
) -> None:
    assert (await authenticated_client.post(REVIEWS, json=BODY)).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {**BODY, "repo": "no-slash"},
        {**BODY, "repo": "a/b/c"},
        {**BODY, "repo": "a/b c"},
        {**BODY, "pr_number": 0},
        {**BODY, "pr_number": -3},
        {**BODY, "head_sha": "abc"},
        {**BODY, "head_sha": "z" * 40},
        {**BODY, "repo_path": "C:/x"},
        {"repo": "a/b", "pr_number": 1, "repo_path": "C:/x"},
        {"pr_number": 1},
    ],
)
async def test_review_body_validation(
    authenticated_client: AsyncClient, fake_review: list[tuple[Any, ...]], body: dict[str, Any]
) -> None:
    resp = await authenticated_client.post(REVIEWS, json=body)
    assert resp.status_code == 422
    assert fake_review == []


async def test_review_rejects_wrong_content_type_and_foreign_origin(
    authenticated_client: AsyncClient, fake_review: list[tuple[Any, ...]]
) -> None:
    plain = await authenticated_client.post(
        REVIEWS, content='{"repo":"a/b","pr_number":1}', headers={"Content-Type": "text/plain"}
    )
    assert plain.status_code == 415
    foreign = await authenticated_client.post(
        REVIEWS, json=BODY, headers={"Origin": "https://evil.example"}
    )
    assert foreign.status_code == 403
    assert fake_review == []


@pytest.mark.parametrize("code,expected", sorted(review_api.STATUS_BY_CODE.items()))
async def test_error_codes_map_to_statuses(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    expected: int,
) -> None:
    async def fail(*args: Any) -> dict[str, Any]:
        raise ReviewServiceError(code, "Читаемая причина.")

    monkeypatch.setattr(review_service, "produce_review", fail)
    resp = await authenticated_client.post(REVIEWS, json=BODY)
    assert resp.status_code == expected
    assert resp.json() == {"detail": "Читаемая причина.", "code": code}


async def test_unknown_code_is_502(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*args: Any) -> dict[str, Any]:
        raise ReviewServiceError("SOMETHING_NEW", "x")

    monkeypatch.setattr(review_service, "produce_review", fail)
    assert (await authenticated_client.post(REVIEWS, json=BODY)).status_code == 502


async def test_unexpected_error_is_a_fixed_500(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*args: Any) -> dict[str, Any]:
        raise RuntimeError("secret detail")

    monkeypatch.setattr(review_service, "produce_review", fail)
    resp = await authenticated_client.post(REVIEWS, json=BODY)
    assert resp.status_code == 500
    assert resp.json() == {"detail": review_api.MSG_INTERNAL, "code": "INTERNAL_ERROR"}
    assert "secret detail" not in resp.text


async def test_busy_guard_per_pr(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(user_id: int, repo: str, pr_number: int, head_sha: str) -> dict[str, Any]:
        started.set()
        await release.wait()
        return canned(pr_number=pr_number)

    monkeypatch.setattr(review_service, "produce_review", slow)
    first = asyncio.create_task(authenticated_client.post(REVIEWS, json=BODY))
    await asyncio.wait_for(started.wait(), 5)
    busy = await authenticated_client.post(REVIEWS, json={**BODY, "repo": "Aleksey/Demo"})
    assert busy.status_code == 409 and busy.json()["code"] == "REVIEW_BUSY"
    release.set()
    other = await authenticated_client.post(REVIEWS, json={**BODY, "pr_number": 8})
    assert other.status_code == 200
    assert (await first).status_code == 200
    assert (await authenticated_client.post(REVIEWS, json=BODY)).status_code == 200


async def test_lock_released_after_error(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*args: Any) -> dict[str, Any]:
        raise ReviewServiceError("LLM_ERROR", "x")

    monkeypatch.setattr(review_service, "produce_review", fail)
    assert (await authenticated_client.post(REVIEWS, json=BODY)).status_code == 502
    assert (await authenticated_client.post(REVIEWS, json=BODY)).status_code == 502


async def test_review_survives_cancelled_request(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def slow(*args: Any) -> dict[str, Any]:
        started.set()
        await release.wait()
        finished.set()
        return canned()

    monkeypatch.setattr(review_service, "produce_review", slow)
    request = asyncio.create_task(authenticated_client.post(REVIEWS, json=BODY))
    await asyncio.wait_for(started.wait(), 5)
    request.cancel()
    await asyncio.gather(request, return_exceptions=True)
    assert review_api._review_tasks
    release.set()
    await asyncio.wait_for(finished.wait(), 5)


# ------------------------------------------------------------------ settings


@needs_git
async def test_get_defaults_for_session_and_key(authenticated_client: AsyncClient) -> None:
    resp = await authenticated_client.get(CONFIG)
    assert resp.status_code == 200
    body = resp.json()
    assert body["repo_configured"] is False and body["repo_path"] is None
    assert body["context_length"] == 65536 and body["code_index"] is None
    assert body["refresh"]["running"] is False
    assert body["effective"]["provider_kind"] == "lm_studio"
    assert body["effective"]["model"] == "loaded-model"
    assert body["effective"]["docs_kb_source"] is None
    headers = await make_key(authenticated_client)
    async with api_client() as caller:
        keyed = await caller.get(CONFIG, headers=headers)
    assert keyed.status_code == 200 and "repo_path" not in keyed.json()
    assert keyed.json()["repo_configured"] is False


@needs_git
async def test_path_is_hidden_from_api_key_callers(
    authenticated_client: AsyncClient, tmp_path: Path
) -> None:
    repos = Repos(tmp_path)
    repos.commit({"a.py": "x = 1\n"})
    put = await authenticated_client.put(CONFIG, json={"repo_path": str(repos.work)})
    assert put.status_code == 200, put.text
    saved = put.json()["repo_path"]
    assert Path(saved).resolve() == repos.work.resolve()
    session_body = (await authenticated_client.get(CONFIG)).json()
    assert session_body["repo_path"] == saved and session_body["repo_configured"] is True
    headers = await make_key(authenticated_client)
    async with api_client() as caller:
        keyed = await caller.get(CONFIG, headers=headers)
        index = await caller.get(INDEX, headers=headers)
    assert keyed.json()["repo_configured"] is True
    assert "repo_path" not in keyed.json()
    assert saved not in keyed.text
    assert saved not in index.text


@needs_git
async def test_put_requires_session_and_leaves_row_unchanged(
    authenticated_client: AsyncClient,
) -> None:
    headers = await make_key(authenticated_client)
    async with api_client() as caller:
        resp = await caller.put(CONFIG, json={"model": "x"}, headers=headers)
    assert resp.status_code == 403
    assert await stored(authenticated_client.seeded_user_id) is None


async def test_put_roundtrip_omitted_keeps_and_null_clears(
    authenticated_client: AsyncClient,
) -> None:
    user_id = authenticated_client.seeded_user_id
    kb_id = await seed_kb(user_id, status=KbStatus.READY)
    first = await authenticated_client.put(
        CONFIG, json={"docs_kb_id": kb_id, "model": "m1", "context_length": 16384}
    )
    assert first.status_code == 200, first.text
    assert first.json()["docs_kb_id"] == kb_id
    assert first.json()["effective"]["docs_kb_source"] == "review"
    kept = await authenticated_client.put(CONFIG, json={"model": "m2"})
    assert kept.json()["docs_kb_id"] == kb_id and kept.json()["model"] == "m2"
    assert kept.json()["context_length"] == 16384
    cleared = await authenticated_client.put(
        CONFIG, json={"docs_kb_id": None, "model": None, "context_length": None}
    )
    body = cleared.json()
    assert body["docs_kb_id"] is None and body["model"] is None
    assert body["context_length"] == 65536
    assert (await authenticated_client.get(CONFIG)).json()["updated_at"] is not None


@needs_git
async def test_put_rejects_bad_repo_path(authenticated_client: AsyncClient, tmp_path: Path) -> None:
    resp = await authenticated_client.put(CONFIG, json={"repo_path": str(tmp_path / "missing")})
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("Не удалось прочитать репозиторий: ")
    assert resp.json()["detail"].endswith(" Проверьте путь и повторите.")
    plain = tmp_path / "plain"
    plain.mkdir()
    again = await authenticated_client.put(CONFIG, json={"repo_path": str(plain)})
    assert again.status_code == 422
    assert await stored(authenticated_client.seeded_user_id) is None


@pytest.mark.parametrize(
    "body",
    [
        {"context_length": 8191},
        {"context_length": 262145},
        {"model": "m" * 201},
        {"repo_path": "C:/" + "a" * 1000},
        {"unknown": 1},
    ],
)
async def test_put_field_bounds(authenticated_client: AsyncClient, body: dict[str, Any]) -> None:
    assert (await authenticated_client.put(CONFIG, json=body)).status_code == 422


async def test_put_not_ready_kb_is_422(authenticated_client: AsyncClient) -> None:
    kb_id = await seed_kb(authenticated_client.seeded_user_id, status=KbStatus.QUEUED)
    resp = await authenticated_client.put(CONFIG, json={"docs_kb_id": kb_id})
    assert resp.status_code == 422


async def test_put_foreign_ids_are_404(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    other_id = second_authenticated_client.seeded_user_id
    foreign_kb = await seed_kb(other_id, status=KbStatus.READY)
    async with async_session_factory() as session:
        provider = LlmProvider(
            user_id=other_id, name="Theirs", base_url="https://x.example", kind="openai"
        )
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
    for body in (
        {"docs_kb_id": foreign_kb},
        {"provider_id": provider.id},
        {"github_server_id": 9999},
    ):
        assert (await authenticated_client.put(CONFIG, json=body)).status_code == 404
    assert await stored(authenticated_client.seeded_user_id) is None


async def test_two_users_never_see_each_other(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    await authenticated_client.put(CONFIG, json={"model": "mine"})
    theirs = (await second_authenticated_client.get(CONFIG)).json()
    assert theirs["model"] is None and theirs["updated_at"] is None
    async with async_session_factory() as session:
        kb_id = await seed_kb(authenticated_client.seeded_user_id, status=KbStatus.READY)
        session.add(
            ReviewCodeIndex(
                user_id=authenticated_client.seeded_user_id, repo_key="a/b", base_ref="main",
                kb_id=kb_id, indexed_commit="c" * 40,
            )
        )
        await session.commit()
    assert (await authenticated_client.get(INDEX)).json()["code_index"]["base_ref"] == "main"
    assert (await second_authenticated_client.get(INDEX)).json()["code_index"] is None


async def test_code_index_state(authenticated_client: AsyncClient) -> None:
    user_id = authenticated_client.seeded_user_id
    kb_id = await seed_kb(user_id, status=KbStatus.READY)
    async with async_session_factory() as session:
        session.add(
            ReviewCodeIndex(
                user_id=user_id, repo_key="a/b", base_ref="develop", kb_id=kb_id,
                indexed_commit="c" * 40,
            )
        )
        await session.commit()
    body = (await authenticated_client.get(INDEX)).json()
    assert body["code_index"]["base_ref"] == "develop"
    assert body["code_index"]["indexed_commit"] == "c" * 40
    assert set(body["code_index"]) == {
        "base_ref", "indexed_commit", "file_count", "chunk_count", "updated_at"
    }
    assert body["refresh"] == {
        "running": False, "phase": None, "done": 0, "total": 0, "error": None
    }


# -------------------------------------------------------------------- refresh


class Spawner:
    """Recording stand-in for the refresh job starter."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.ok = True

    def __call__(self, user_id: int, repo_path: str, base_ref: str, model: str) -> bool:
        self.calls.append((user_id, repo_path, base_ref, model))
        return self.ok


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> Spawner:
    spawner = Spawner()
    monkeypatch.setattr(code_index, "spawn_refresh_job", spawner)
    return spawner


async def test_refresh_without_saved_path(authenticated_client: AsyncClient) -> None:
    resp = await authenticated_client.post(REFRESH, json={})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Сначала укажите путь и сохраните настройки."


@needs_git
async def test_refresh_is_session_only(authenticated_client: AsyncClient) -> None:
    headers = await make_key(authenticated_client)
    async with api_client() as caller:
        resp = await caller.post(REFRESH, json={}, headers=headers)
    assert resp.status_code == 403


@needs_git
async def test_refresh_base_ref_rules(
    authenticated_client: AsyncClient, tmp_path: Path, spawned: Spawner
) -> None:
    repos = Repos(tmp_path)
    repos.commit({"a.py": "x = 1\n"})
    await authenticated_client.put(CONFIG, json={"repo_path": str(repos.work)})
    explicit = await authenticated_client.post(REFRESH, json={"base_ref": "develop"})
    assert explicit.status_code == 202 and explicit.json() == {
        "running": True, "base_ref": "develop"
    }
    local = await authenticated_client.post(REFRESH)
    assert local.status_code == 202 and local.json()["base_ref"] == "main"
    user_id = authenticated_client.seeded_user_id
    kb_id = await seed_kb(user_id, status=KbStatus.READY)
    async with async_session_factory() as session:
        session.add(
            ReviewCodeIndex(
                user_id=user_id, repo_key="a/b", base_ref="release", kb_id=kb_id,
                indexed_commit="c" * 40,
            )
        )
        await session.commit()
    last = await authenticated_client.post(REFRESH, json={})
    assert last.json()["base_ref"] == "release"
    assert spawned.calls[0][3] == code_index.DEFAULT_EMBEDDING_MODEL


@needs_git
async def test_refresh_invalid_base_ref_and_busy(
    authenticated_client: AsyncClient, tmp_path: Path, spawned: Spawner
) -> None:
    repos = Repos(tmp_path)
    repos.commit({"a.py": "x = 1\n"})
    await authenticated_client.put(CONFIG, json={"repo_path": str(repos.work)})
    bad = await authenticated_client.post(REFRESH, json={"base_ref": "--upload-pack=x"})
    assert bad.status_code == 422
    spawned.ok = False
    busy = await authenticated_client.post(REFRESH, json={})
    assert busy.status_code == 409 and busy.json()["code"] == "REVIEW_INDEX_BUSY"
