"""Orchestration of one PR review: settings, GitHub data, code index refresh, review."""

import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.code_index import DEFAULT_EMBEDDING_MODEL, CodeIndexError, refresh_code_index
from agent.llm_client import get_lm_studio_client
from agent.providers import (
    KIND_LM_STUDIO,
    ProviderUnavailableError,
    build_client,
    get_provider_row,
)
from agent.review_github import GithubReviewError, fetch_head_sha, fetch_pull_request
from agent.review_pipeline import ReviewError, run_review
from agent.schemas import normalize_base_url
from shared.database import async_session_factory
from shared.logger import get_logger
from shared.models import HelpConfig, KbStatus, KnowledgeBase, LlmProvider, ReviewConfig

logger = get_logger(__name__)

CODE_REVIEW_NOT_CONFIGURED = "REVIEW_NOT_CONFIGURED"
CODE_LM_STUDIO_UNAVAILABLE = "LM_STUDIO_UNAVAILABLE"
CODE_INDEX_UNAVAILABLE = "CODE_INDEX_UNAVAILABLE"
REVIEW_LLM_TIMEOUT = 900.0
MODEL_LIST_TIMEOUT = 10.0
MODEL_LIST_QUICK_TIMEOUT = 3.0
DEFAULT_CONTEXT_LENGTH = 65536
CHAT_MODEL_TYPES = frozenset({"llm", "vlm"})

MSG_NOT_CONFIGURED = (
    "Ревью не настроено: укажите путь к репозиторию в настройках «Ревью Pull Request»."
)
MSG_PROVIDER_UNAVAILABLE = "Провайдер, выбранный для ревью, отключён или удалён."
MSG_NO_MODEL = "Не выбрана модель для ревью."
MSG_NO_LOADED_MODEL = "В LM Studio не загружена модель."
MSG_LM_STUDIO_DOWN = "LM Studio недоступен."
MSG_CODE_INDEX_MISSING = "Индекс кода недоступен."
NOTE_HEAD_CHANGED = "Во время ревью в Pull Request появились новые коммиты."
NOTE_PARTS = "Ревью выполнено по частям: {count}."


class ReviewServiceError(Exception):
    """The review could not be produced; `code` is machine-readable, `detail` is Russian."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code: str = code
        self.detail: str = detail


@dataclass
class ResolvedSettings:
    """Everything a review needs from the user's settings, validated."""

    config: ReviewConfig
    repo_path: str
    docs_kb: KnowledgeBase | None
    docs_kb_source: str | None
    provider: LlmProvider
    model: str
    context_length: int
    github_server_id: int | None


async def load_review_config(session: AsyncSession, user_id: int) -> ReviewConfig | None:
    """Return the user's stored review settings, or None when never saved."""
    return await session.get(ReviewConfig, user_id)


async def _ready_owned_kb(
    session: AsyncSession, user_id: int, kb_id: int | None
) -> KnowledgeBase | None:
    """Return the base when it exists, belongs to the user and is ready."""
    if kb_id is None:
        return None
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None or kb.user_id != user_id or kb.status != KbStatus.READY:
        return None
    return kb


async def resolve_docs_kb(
    session: AsyncSession, user_id: int, config: ReviewConfig | None
) -> tuple[KnowledgeBase | None, str | None]:
    """Pick the documentation base: the review setting first, then the /help mark."""
    if config is not None:
        kb = await _ready_owned_kb(session, user_id, config.docs_kb_id)
        if kb is not None:
            return kb, "review"
    help_config = await session.get(HelpConfig, user_id)
    if help_config is not None:
        kb = await _ready_owned_kb(session, user_id, help_config.kb_id)
        if kb is not None:
            return kb, "help"
    return None, None


async def _loaded_chat_model(provider: LlmProvider, timeout: float) -> str | None:
    """Return the first loaded chat model of an LM Studio host; raise httpx errors on failure."""
    base = normalize_base_url(provider.base_url)
    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            response = await http.get(f"{base}/api/v0/models")
            response.raise_for_status()
            data = response.json().get("data", [])
        for item in data:
            if (
                isinstance(item, dict)
                and item.get("id")
                and item.get("state") == "loaded"
                and item.get("type") in CHAT_MODEL_TYPES
            ):
                return str(item["id"])
        return None
    except httpx.HTTPStatusError:
        # Older LM Studio builds lack the control API; /v1/models lists loaded models only.
        models = await get_lm_studio_client(base).list_models()
        for item in models:
            model_id = str(item.get("id", ""))
            if model_id and "embed" not in model_id.lower():
                return model_id
        return None


async def _resolve_model(
    provider: LlmProvider, config: ReviewConfig | None, *, strict: bool
) -> str | None:
    """Return the model id for the review; None (non-strict) or an error when unknown."""
    if config is not None and config.model:
        return config.model
    if provider.kind != KIND_LM_STUDIO:
        if strict:
            raise ReviewServiceError(CODE_REVIEW_NOT_CONFIGURED, MSG_NO_MODEL)
        return None
    timeout = MODEL_LIST_TIMEOUT if strict else MODEL_LIST_QUICK_TIMEOUT
    try:
        model = await _loaded_chat_model(provider, timeout)
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        if strict:
            raise ReviewServiceError(CODE_LM_STUDIO_UNAVAILABLE, MSG_LM_STUDIO_DOWN) from exc
        return None
    if model is None and strict:
        raise ReviewServiceError(CODE_LM_STUDIO_UNAVAILABLE, MSG_NO_LOADED_MODEL)
    return model


async def resolve_provider_and_model(
    session: AsyncSession,
    user_id: int,
    config: ReviewConfig | None,
    *,
    strict: bool,
) -> tuple[LlmProvider | None, str | None]:
    """Pick the provider and model that write the review (D-05)."""
    try:
        provider = await get_provider_row(
            session, user_id, config.provider_id if config is not None else None
        )
    except ProviderUnavailableError as exc:
        if strict:
            raise ReviewServiceError(CODE_REVIEW_NOT_CONFIGURED, MSG_PROVIDER_UNAVAILABLE) from exc
        return None, None
    model = await _resolve_model(provider, config, strict=strict)
    return provider, model


async def resolve_review_settings(session: AsyncSession, user_id: int) -> ResolvedSettings:
    """Validate the stored settings and return what a review needs, or raise."""
    config = await load_review_config(session, user_id)
    if config is None or not (config.repo_path or "").strip():
        raise ReviewServiceError(CODE_REVIEW_NOT_CONFIGURED, MSG_NOT_CONFIGURED)
    docs_kb, source = await resolve_docs_kb(session, user_id, config)
    provider, model = await resolve_provider_and_model(session, user_id, config, strict=True)
    assert provider is not None and model is not None
    return ResolvedSettings(
        config=config,
        repo_path=str(config.repo_path),
        docs_kb=docs_kb,
        docs_kb_source=source,
        provider=provider,
        model=model,
        context_length=config.context_length,
        github_server_id=config.github_server_id,
    )


def embedding_model_for(docs_kb: KnowledgeBase | None) -> str:
    """Return the embedding model of the documentation base, else the default one."""
    return docs_kb.embedding_model if docs_kb is not None else DEFAULT_EMBEDDING_MODEL


async def _final_head(
    session: AsyncSession, user_id: int, server_id: int | None, repo: str, number: int
) -> str | None:
    """Read the PR head again; a failure of this check never fails the review."""
    try:
        return await fetch_head_sha(session, user_id, server_id, repo, number)
    except Exception as exc:  # noqa: BLE001 - the final check is advisory only
        logger.warning("review_head_check_failed", user_id=user_id, error=type(exc).__name__)
        return None


def _result_body(
    repo: str,
    number: int,
    head_sha: str,
    head_changed: bool,
    base_commit: str,
    base_ref: str,
    resolved: ResolvedSettings,
    review: Any,
    notes: list[str],
) -> dict[str, Any]:
    """Assemble the success reply of the review route."""
    sections = review.sections
    return {
        "repo": repo,
        "pr_number": number,
        "head_sha": head_sha,
        "head_changed": head_changed,
        "base_ref": base_ref,
        "base_commit": base_commit,
        "provider": resolved.provider.name,
        "model": resolved.model,
        "sections": {
            "bugs": sections.bugs,
            "architecture": sections.architecture,
            "recommendations": sections.recommendations,
        },
        "sources": {"docs": review.sources_docs, "code": review.sources_code},
        "docs_used": review.docs_used,
        "parts": review.parts,
        "unreviewed_files": review.unreviewed_files,
        "notes": notes,
    }


async def _produce(
    session: AsyncSession, user_id: int, repo: str, pr_number: int, started: float
) -> dict[str, Any]:
    """Run the chain settings, PR data, index refresh, review, final head check."""
    resolved = await resolve_review_settings(session, user_id)
    pr = await fetch_pull_request(session, user_id, resolved.github_server_id, repo, pr_number)
    state = await refresh_code_index(
        user_id,
        resolved.repo_path,
        pr.base_ref,
        expected_repo=repo,
        embedding_model=embedding_model_for(resolved.docs_kb),
    )
    code_kb = await session.get(KnowledgeBase, state.kb_id)
    if code_kb is None:
        raise ReviewServiceError(CODE_INDEX_UNAVAILABLE, MSG_CODE_INDEX_MISSING)
    client = build_client(resolved.provider, timeout=REVIEW_LLM_TIMEOUT)
    review = await run_review(
        session,
        pr,
        resolved.docs_kb,
        code_kb,
        client,
        resolved.model,
        resolved.context_length,
        provider_kind=resolved.provider.kind,
    )
    notes = list(review.notes)
    if review.parts > 1:
        notes.append(NOTE_PARTS.format(count=review.parts))
    final_head = await _final_head(session, user_id, resolved.github_server_id, repo, pr_number)
    head_changed = final_head is not None and final_head != pr.head_sha
    if head_changed:
        notes.append(NOTE_HEAD_CHANGED)
    logger.info(
        "review_finished",
        user_id=user_id,
        repo=repo,
        pr_number=pr_number,
        parts=review.parts,
        seconds=round(time.perf_counter() - started, 1),
        provider_kind=resolved.provider.kind,
    )
    return _result_body(
        repo, pr_number, pr.head_sha, head_changed, state.commit, pr.base_ref,
        resolved, review, notes,
    )


async def produce_review(
    user_id: int, repo: str, pr_number: int, head_sha: str
) -> dict[str, Any]:
    """Produce the complete review reply for a user and a PR with a private DB session."""
    started = time.perf_counter()
    logger.info(
        "review_started", user_id=user_id, repo=repo, pr_number=pr_number,
        head_requested=bool(head_sha),
    )
    try:
        async with async_session_factory() as session:
            return await _produce(session, user_id, repo, pr_number, started)
    except (GithubReviewError, CodeIndexError, ReviewError) as exc:
        raise ReviewServiceError(exc.code, exc.detail) from exc
