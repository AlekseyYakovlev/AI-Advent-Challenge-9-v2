"""Chat-model calls of the release pipeline: changelog draft and version decision."""

from typing import Any

from agent.rag_llm import complete_stage
from agent.release_pipeline import (
    Decision,
    build_changelog_messages,
    build_decision_messages,
    build_retry_messages,
    changelog_coverage,
    parse_changelog,
    parse_decision,
)
from shared.logger import get_logger

logger = get_logger(__name__)

RELEASE_LLM_TIMEOUT: float = 240.0
# Reasoning models (qwen3.5-9b) spend ~6000 tokens thinking before the answer; 1800 left the reply empty.
CHANGELOG_MAX_TOKENS: int = 8192
DECISION_MAX_TOKENS: int = 4096
RAW_DRAFT_RETRY_CHARS: int = 4000


class ReleaseLlm:
    """Non-streaming calls on the chat's own client and model."""

    def __init__(self, client: Any, model: str, timeout: float = RELEASE_LLM_TIMEOUT) -> None:
        self.client = client
        self.model = model
        self.timeout = timeout

    async def complete(
        self, stage: str, messages: list[dict[str, str]], max_tokens: int
    ) -> str | None:
        """Return the reply text, or None when the call failed or the reply is blank."""
        result, reason = await complete_stage(
            f"release_{stage}", self.client, messages, self.model, max_tokens, self.timeout
        )
        if result is None:
            logger.warning("release_llm_failed", stage=stage, reason=reason)
            return None
        if not (result.content or "").strip():
            logger.warning(
                "release_llm_unusable",
                stage=stage,
                finish_reason=result.finish_reason,
                has_reasoning=result.has_reasoning,
                content_empty=True,
            )
            return None
        return result.content


def _numeric_phases(groups: dict[str, list[str]]) -> list[str]:
    """Return the numeric phase keys that the changelog must mention."""
    return [key for key in groups if key.isdigit()]


async def draft_changelog(
    llm: ReleaseLlm,
    groups: dict[str, list[str]],
    day_merges: list[str],
    prs: list[str],
) -> tuple[str | None, list[str], int]:
    """Draft the changelog body; returns (body, missing items, shrunk subject count)."""
    messages, shrunk = build_changelog_messages(groups, day_merges, prs)
    phases = _numeric_phases(groups)
    raw = await llm.complete("changelog", messages, CHANGELOG_MAX_TOKENS)
    body = parse_changelog(raw)
    missing = changelog_coverage(body, phases, day_merges) if body else []
    if body is not None and not missing:
        return body, [], shrunk
    if raw is None:
        return None, [], shrunk
    first_missing = missing if body else [*phases, *day_merges]
    draft = body if body else raw[:RAW_DRAFT_RETRY_CHARS]
    retry_raw = await llm.complete(
        "changelog", build_retry_messages(messages, draft, first_missing), CHANGELOG_MAX_TOKENS
    )
    retry_body = parse_changelog(retry_raw)
    if retry_body is None:
        return body, missing, shrunk
    retry_missing = changelog_coverage(retry_body, phases, day_merges)
    if body is None or len(retry_missing) <= len(missing):
        return retry_body, retry_missing, shrunk
    return body, missing, shrunk


async def choose_version(
    llm: ReleaseLlm, candidates: list[str], signals: dict[str, Any], body: str
) -> Decision:
    """Ask the model to pick one of the candidate versions with a title and rationale."""
    messages = build_decision_messages(candidates, signals, body)
    text = await llm.complete("decision", messages, DECISION_MAX_TOKENS)
    return parse_decision(text, candidates)
