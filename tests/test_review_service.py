"""Tests for review settings resolution and the produce_review orchestration."""

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
import respx

from agent import providers, review_service
from agent.code_index import CODE_BASE_UNAVAILABLE, CodeIndexError, CodeIndexState
from agent.review_github import CODE_PR_NOT_FOUND, GithubReviewError, PullRequestData
from agent.review_pipeline import ReviewError, ReviewResult, ReviewSections
from agent.review_service import (
    MSG_NO_LOADED_MODEL,
    MSG_NO_MODEL,
    MSG_NOT_CONFIGURED,
    ReviewServiceError,
    produce_review,
    resolve_docs_kb,
    resolve_review_settings,
)
from kb_helpers import seed_kb, seed_user
from shared.config import settings
from shared.database import async_session_factory
from shared.models import HelpConfig, KbStatus, LlmProvider, ReviewConfig

REPO = "aleksey/demo"
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
MODELS_URL = f"{settings.LM_STUDIO_BASE_URL.rstrip('/')}/api/v0/models"


def make_pr(head: str = HEAD) -> PullRequestData:
    return PullRequestData(
        repo=REPO, number=7, title="t", body="", state="open", draft=False,
        base_ref="main", base_sha="c" * 40, head_ref="feat", head_sha=head,
        changed_files=1, files=[], diff="diff --git a/x b/x\n",
    )


async def save_config(user_id: int, **fields: Any) -> None:
    async with async_session_factory() as session:
        session.add(ReviewConfig(user_id=user_id, **fields))
        await session.commit()


async def save_help(user_id: int, kb_id: int | None) -> None:
    async with async_session_factory() as session:
        session.add(HelpConfig(user_id=user_id, kb_id=kb_id))
        await session.commit()


def loaded(*items: tuple[str, str, str]) -> httpx.Response:
    return httpx.Response(
        200, json={"data": [{"id": i, "type": t, "state": s} for i, t, s in items]}
    )


class Calls:
    """Recording fakes for the four collaborators of produce_review."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, final_head: str | None = HEAD,
                 parts: int = 1) -> None:
        self.order: list[str] = []
        self.refresh_args: dict[str, Any] = {}
        self.run_args: dict[str, Any] = {}
        self.final_head = final_head
        self.parts = parts
        monkeypatch.setattr(review_service, "fetch_pull_request", self.fetch_pr)
        monkeypatch.setattr(review_service, "refresh_code_index", self.refresh)
        monkeypatch.setattr(review_service, "run_review", self.run)
        monkeypatch.setattr(review_service, "fetch_head_sha", self.head)

    async def fetch_pr(self, session: Any, user_id: int, server_id: Any, repo: str,
                       number: int) -> PullRequestData:
        self.order.append("fetch_pull_request")
        self.server_id = server_id
        return make_pr()

    async def refresh(self, user_id: int, repo_path: str, base_ref: str, expected_repo: Any,
                      embedding_model: str) -> CodeIndexState:
        self.order.append("refresh_code_index")
        self.refresh_args = {
            "repo_path": repo_path, "base_ref": base_ref,
            "expected_repo": expected_repo, "embedding_model": embedding_model,
        }
        kb_id = await seed_kb(user_id, status=KbStatus.READY)
        return CodeIndexState(
            kb_id=kb_id, repo_key=REPO, base_ref=base_ref, commit="d" * 40, file_count=1,
            chunk_count=1, changed=0, removed=0, updated_at=datetime.now(timezone.utc),
        )

    async def run(self, session: Any, pr: Any, docs_kb: Any, code_kb: Any, client: Any,
                  model: str, context_length: int, provider_kind: Any = None) -> ReviewResult:
        self.order.append("run_review")
        self.run_args = {
            "docs_kb": docs_kb, "model": model, "client": client,
            "context_length": context_length, "provider_kind": provider_kind,
        }
        return ReviewResult(
            sections=ReviewSections(bugs="b", architecture="a", recommendations="r"),
            sources_docs=[{"file": "docs/a.md", "section": None}],
            sources_code=[{"file": "x.py", "section": "f", "lines": "1-5"}],
            docs_used=docs_kb is not None, parts=self.parts, notes=["n1"],
        )

    async def head(self, *args: Any) -> str:
        self.order.append("fetch_head_sha")
        if self.final_head is None:
            raise RuntimeError("down")
        return self.final_head


async def configured_user(model: str | None = "m1", **extra: Any) -> int:
    user_id = await seed_user("rev")
    await save_config(user_id, repo_path="C:/repo", model=model, **extra)
    return user_id


# ---------------------------------------------------------------- build_client


def test_build_client_timeout_argument() -> None:
    row = LlmProvider(user_id=1, name="x", base_url="http://localhost:1234", kind="lm_studio")
    default = providers.build_client(row)
    custom = providers.build_client(row, timeout=900.0)
    assert custom._timeout == 900.0
    assert default._timeout == settings.LLM_TIMEOUT


# ------------------------------------------------------------------ settings


async def test_no_row_is_not_configured() -> None:
    user_id = await seed_user("rev")
    async with async_session_factory() as session:
        with pytest.raises(ReviewServiceError) as err:
            await resolve_review_settings(session, user_id)
    assert err.value.code == "REVIEW_NOT_CONFIGURED"
    assert err.value.detail == MSG_NOT_CONFIGURED


async def test_empty_repo_path_is_not_configured() -> None:
    user_id = await seed_user("rev")
    await save_config(user_id, repo_path="  ")
    async with async_session_factory() as session:
        with pytest.raises(ReviewServiceError) as err:
            await resolve_review_settings(session, user_id)
    assert err.value.code == "REVIEW_NOT_CONFIGURED"


async def test_docs_kb_review_setting_wins() -> None:
    user_id = await seed_user("rev")
    review_kb = await seed_kb(user_id, status=KbStatus.READY)
    help_kb = await seed_kb(user_id, status=KbStatus.READY)
    await save_config(user_id, repo_path="C:/r", docs_kb_id=review_kb)
    await save_help(user_id, help_kb)
    async with async_session_factory() as session:
        config = await session.get(ReviewConfig, user_id)
        kb, source = await resolve_docs_kb(session, user_id, config)
    assert kb is not None and kb.id == review_kb and source == "review"


async def test_docs_kb_falls_back_to_help_mark() -> None:
    user_id = await seed_user("rev")
    help_kb = await seed_kb(user_id, status=KbStatus.READY)
    await save_config(user_id, repo_path="C:/r")
    await save_help(user_id, help_kb)
    async with async_session_factory() as session:
        config = await session.get(ReviewConfig, user_id)
        kb, source = await resolve_docs_kb(session, user_id, config)
    assert kb is not None and kb.id == help_kb and source == "help"


async def test_foreign_or_unready_docs_kb_is_absent() -> None:
    user_id = await seed_user("rev")
    other = await seed_user("other")
    foreign = await seed_kb(other, status=KbStatus.READY)
    unready = await seed_kb(user_id, status=KbStatus.QUEUED)
    await save_config(user_id, repo_path="C:/r", docs_kb_id=foreign)
    await save_help(user_id, unready)
    async with async_session_factory() as session:
        config = await session.get(ReviewConfig, user_id)
        assert await resolve_docs_kb(session, user_id, config) == (None, None)
    await save_help(other, foreign)  # another user's own mark never leaks to this user


async def test_stored_model_is_used_without_contacting_lm_studio() -> None:
    user_id = await configured_user(model="stored")
    async with async_session_factory() as session:
        resolved = await resolve_review_settings(session, user_id)
    assert resolved.model == "stored" and resolved.provider.kind == "lm_studio"
    assert resolved.context_length == 65536


@respx.mock
async def test_loaded_chat_model_skips_embeddings_and_unloaded() -> None:
    user_id = await configured_user(model=None)
    respx.get(MODELS_URL).mock(return_value=loaded(
        ("embed-x", "embeddings", "loaded"),
        ("not-loaded", "llm", "not-loaded"),
        ("chat-1", "llm", "loaded"),
    ))
    async with async_session_factory() as session:
        resolved = await resolve_review_settings(session, user_id)
    assert resolved.model == "chat-1"


@respx.mock
async def test_no_loaded_chat_model() -> None:
    user_id = await configured_user(model=None)
    respx.get(MODELS_URL).mock(return_value=loaded(("embed-x", "embeddings", "loaded")))
    async with async_session_factory() as session:
        with pytest.raises(ReviewServiceError) as err:
            await resolve_review_settings(session, user_id)
    assert err.value.code == "LM_STUDIO_UNAVAILABLE"
    assert err.value.detail == MSG_NO_LOADED_MODEL


@respx.mock
async def test_lm_studio_not_answering() -> None:
    user_id = await configured_user(model=None)
    respx.get(MODELS_URL).mock(side_effect=httpx.ConnectError("refused"))
    async with async_session_factory() as session:
        with pytest.raises(ReviewServiceError) as err:
            await resolve_review_settings(session, user_id)
    assert err.value.code == "LM_STUDIO_UNAVAILABLE"


async def test_non_lm_studio_provider_without_model() -> None:
    user_id = await seed_user("rev")
    async with async_session_factory() as session:
        row = LlmProvider(
            user_id=user_id, name="Cloud", base_url="https://api.example.com", kind="openai"
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        provider_id = row.id
    await save_config(user_id, repo_path="C:/r", provider_id=provider_id)
    async with async_session_factory() as session:
        with pytest.raises(ReviewServiceError) as err:
            await resolve_review_settings(session, user_id)
    assert err.value.code == "REVIEW_NOT_CONFIGURED"
    assert err.value.detail == MSG_NO_MODEL


async def test_disabled_provider_is_not_configured() -> None:
    user_id = await seed_user("rev")
    async with async_session_factory() as session:
        row = LlmProvider(
            user_id=user_id, name="Off", base_url="https://api.example.com",
            kind="openai", enabled=False,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        provider_id = row.id
    await save_config(user_id, repo_path="C:/r", provider_id=provider_id, model="m")
    async with async_session_factory() as session:
        with pytest.raises(ReviewServiceError) as err:
            await resolve_review_settings(session, user_id)
    assert err.value.code == "REVIEW_NOT_CONFIGURED"
    assert err.value.detail == review_service.MSG_PROVIDER_UNAVAILABLE


# ------------------------------------------------------------ produce_review


async def test_call_order_and_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = Calls(monkeypatch)
    user_id = await configured_user()
    body = await produce_review(user_id, REPO, 7, "")
    assert calls.order == [
        "fetch_pull_request", "refresh_code_index", "run_review", "fetch_head_sha"
    ]
    assert calls.refresh_args["expected_repo"] == REPO
    assert calls.refresh_args["base_ref"] == "main"
    assert calls.refresh_args["repo_path"] == "C:/repo"
    assert calls.refresh_args["embedding_model"] == review_service.DEFAULT_EMBEDDING_MODEL
    assert calls.run_args["client"]._timeout == review_service.REVIEW_LLM_TIMEOUT == 900.0
    assert set(body) == {
        "repo", "pr_number", "head_sha", "head_changed", "base_ref", "base_commit",
        "provider", "model", "sections", "sources", "docs_used", "parts",
        "unreviewed_files", "notes",
    }
    assert body["head_sha"] == HEAD and body["head_changed"] is False
    assert body["base_commit"] == "d" * 40 and body["model"] == "m1"
    assert body["provider"] == "LM Studio"
    assert body["sections"] == {"bugs": "b", "architecture": "a", "recommendations": "r"}
    assert body["notes"] == ["n1"]


async def test_embedding_model_follows_documentation_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = Calls(monkeypatch)
    user_id = await seed_user("rev")
    kb_id = await seed_kb(user_id, status=KbStatus.READY, model="custom-embed")
    await save_config(user_id, repo_path="C:/r", model="m", docs_kb_id=kb_id)
    body = await produce_review(user_id, REPO, 7, "")
    assert calls.refresh_args["embedding_model"] == "custom-embed"
    assert body["docs_used"] is True


async def test_head_in_result_is_the_one_read_from_github(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Calls(monkeypatch)
    user_id = await configured_user()
    body = await produce_review(user_id, REPO, 7, OTHER_HEAD)
    assert body["head_sha"] == HEAD


async def test_new_commits_during_review_are_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    Calls(monkeypatch, final_head=OTHER_HEAD)
    user_id = await configured_user()
    body = await produce_review(user_id, REPO, 7, "")
    assert body["head_changed"] is True and body["head_sha"] == HEAD
    assert review_service.NOTE_HEAD_CHANGED in body["notes"]


async def test_failed_final_head_check_does_not_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    Calls(monkeypatch, final_head=None)
    user_id = await configured_user()
    body = await produce_review(user_id, REPO, 7, "")
    assert body["head_changed"] is False


async def test_parts_note(monkeypatch: pytest.MonkeyPatch) -> None:
    Calls(monkeypatch, parts=3)
    user_id = await configured_user()
    body = await produce_review(user_id, REPO, 7, "")
    assert "Ревью выполнено по частям: 3." in body["notes"]


@pytest.mark.parametrize(
    "target,error,code",
    [
        ("fetch_pull_request", GithubReviewError(CODE_PR_NOT_FOUND, "нет PR"), "PR_NOT_FOUND"),
        ("refresh_code_index", CodeIndexError(CODE_BASE_UNAVAILABLE, "нет ветки"), "BASE_UNAVAILABLE"),
        ("run_review", ReviewError("LLM_ERROR", "модель"), "LLM_ERROR"),
    ],
)
async def test_domain_errors_become_service_errors(
    monkeypatch: pytest.MonkeyPatch, target: str, error: Exception, code: str
) -> None:
    Calls(monkeypatch)

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(review_service, target, boom)
    user_id = await configured_user()
    with pytest.raises(ReviewServiceError) as err:
        await produce_review(user_id, REPO, 7, "")
    assert err.value.code == code and err.value.detail == error.detail


async def test_other_exceptions_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    Calls(monkeypatch)

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(review_service, "run_review", boom)
    user_id = await configured_user()
    with pytest.raises(RuntimeError):
        await produce_review(user_id, REPO, 7, "")


async def test_not_configured_stops_before_github(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = Calls(monkeypatch)
    user_id = await seed_user("rev")
    with pytest.raises(ReviewServiceError) as err:
        await produce_review(user_id, REPO, 7, "")
    assert err.value.code == "REVIEW_NOT_CONFIGURED" and calls.order == []
