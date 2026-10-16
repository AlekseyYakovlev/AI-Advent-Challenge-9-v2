"""Two-stage RAG retrieval pipeline shared by the chat turn and the eval script."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.kb_search import cosine_for_ids
from agent.rag import DEFAULT_CANDIDATE_K, RagFailure, resolve_threshold, retrieve_vectors
from agent.rag_fts import fts_search, load_chunks_by_ids
from agent.rag_llm import condense_query, llm_rerank, rewrite_query
from agent.rag_rank import (
    RERANK_TOP_N,
    article_numbers,
    contains_number,
    lexical_rerank,
    lexical_score,
    parse_article,
    rrf_order,
)
from shared.logger import get_logger
from shared.models import ChatRagConfig, KnowledgeBase

logger = get_logger(__name__)

STATUS_IN_ANSWER = "in_answer"
STATUS_BELOW_THRESHOLD = "below_threshold"
STATUS_OUTSIDE_TOP_K = "outside_top_k"
STATUS_OVER_BUDGET = "over_budget"
VERDICT_OK = "ok"
VERDICT_BELOW_THRESHOLD = "below_threshold"
STAGE_THRESHOLD = "threshold"
STAGE_LEXICAL = "lexical"
STAGE_LLM = "llm"
STAGE_HYBRID = "hybrid"
STAGE_REWRITE = "rewrite"
STAGE_HISTORY = "history"

REASON_NO_LLM = "no_llm"
REASON_SEARCH_FAILED = "search_failed"
REASON_FTS_ERROR = "fts_error"
REASON_STAGE_ERROR = "stage_error"
_REASON_UNCHANGED = "unchanged"

FOUND_ORIGINAL = "original"
FOUND_REWRITTEN = "rewritten"
FOUND_BOTH = "both"


@dataclass(frozen=True)
class HistoryContext:
    """Recent dialog pairs and rendered task memory used to condense a follow-up."""

    pairs: tuple[tuple[str, str], ...]
    memory_text: str | None


@dataclass(frozen=True)
class PipelineConfig:
    """Effective retrieval settings for one turn."""

    candidate_k: int = 20
    top_k: int = 5
    threshold: float = 0.0
    threshold_source: str = "none"
    lexical: bool = False
    llm_rerank: bool = False
    hybrid: bool = False
    rewrite: bool = False
    history: HistoryContext | None = None


@dataclass
class _Candidate:
    """One retrieved chunk with the scores each stage attached to it."""

    row_id: int
    chunk: dict[str, Any]
    cos: float
    found_by: str = FOUND_ORIGINAL
    fts_rank: int | None = None
    lex: float | None = None
    llm: float | None = None
    fts_exempt: bool = False
    rank_before: int = 0
    rank_after: int | None = None
    status: str = STATUS_OUTSIDE_TOP_K


@dataclass
class _Run:
    """Mutable bookkeeping shared by the stage helpers of one pipeline run."""

    stages: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    stage_ms: dict[str, int] = field(default_factory=dict)

    def skip(self, stage: str, reason: str) -> None:
        self.skipped.append({"stage": stage, "reason": reason})

    def done(self, stage: str, started: float) -> None:
        self.stages.append(stage)
        self.stage_ms[stage] = int((time.perf_counter() - started) * 1000)


def config_from_row(
    row: ChatRagConfig, kb: KnowledgeBase | None, history: HistoryContext | None = None
) -> PipelineConfig:
    """Build the effective pipeline settings from a chat's RAG config row."""
    threshold, source = resolve_threshold(row.threshold, kb.embedding_model if kb else None)
    return PipelineConfig(
        candidate_k=max(row.candidate_k or DEFAULT_CANDIDATE_K, row.top_k),
        top_k=row.top_k,
        threshold=threshold,
        threshold_source=source,
        lexical=bool(row.lexical),
        llm_rerank=bool(row.llm_rerank),
        hybrid=bool(row.hybrid),
        rewrite=bool(row.rewrite),
        history=history,
    )


def _sorted_by_cosine(candidates: dict[int, _Candidate]) -> list[_Candidate]:
    """Candidates by raw cosine descending; ties keep insertion order."""
    return sorted(candidates.values(), key=lambda item: -item.cos)


async def _rewrite_stage(
    session: AsyncSession,
    kb: KnowledgeBase,
    question: str,
    config: PipelineConfig,
    client: Any | None,
    model: str | None,
    candidates: dict[int, _Candidate],
    vectors: list[tuple[np.ndarray, str]],
    run: _Run,
) -> tuple[str | None, float | None]:
    """Rewrite or condense the query and merge the second search into the candidates."""
    started = time.perf_counter()
    history = config.history
    stage = STAGE_HISTORY if history is not None else STAGE_REWRITE
    if client is None or model is None:
        run.skip(stage, REASON_NO_LLM)
        return None, None
    try:
        if history is not None:
            outcome = await condense_query(
                client, model, question, history.pairs, history.memory_text
            )
        else:
            outcome = await rewrite_query(client, model, question)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("rag_stage_failed", stage=stage, error=type(exc).__name__)
        run.skip(stage, REASON_STAGE_ERROR)
        return None, None
    if outcome.reason == _REASON_UNCHANGED:
        run.done(stage, started)
        return None, None
    if outcome.reason is not None or not outcome.value:
        run.skip(stage, outcome.reason or REASON_STAGE_ERROR)
        return None, None
    rewritten = str(outcome.value)
    try:
        results, vector = await retrieve_vectors(session, kb, rewritten, config.candidate_k)
    except RagFailure:
        run.skip(stage, REASON_SEARCH_FAILED)
        return None, None
    for item in results:
        existing = candidates.get(item["row_id"])
        if existing is None:
            candidates[item["row_id"]] = _Candidate(
                item["row_id"], item, item["score"], found_by=FOUND_REWRITTEN
            )
            continue
        existing.found_by = FOUND_BOTH
        if item["score"] > existing.cos:
            existing.cos = item["score"]
            existing.chunk["score"] = item["score"]
    vectors.append((vector, FOUND_REWRITTEN))
    run.done(stage, started)
    return rewritten, round(float(np.dot(vectors[0][0], vector)), 4)


async def _hybrid_stage(
    session: AsyncSession,
    kb: KnowledgeBase,
    fts_text: str,
    config: PipelineConfig,
    candidates: dict[int, _Candidate],
    vectors: list[tuple[np.ndarray, str]],
    run: _Run,
) -> bool:
    """Add FTS5 hits, record their bm25 rank and reorder by RRF; True when it ran."""
    started = time.perf_counter()
    try:
        fts_ids = await fts_search(session, kb.id, fts_text, config.candidate_k)
        vector_order = [item.row_id for item in _sorted_by_cosine(candidates)]
        missing = [row_id for row_id in fts_ids if row_id not in candidates]
        loaded = await load_chunks_by_ids(session, kb.id, missing)
        added: dict[int, _Candidate] = {}
        if loaded:
            best: dict[int, tuple[float, str]] = {}
            for vector, origin in vectors:
                cosines = await cosine_for_ids(kb, vector, list(loaded))
                for row_id, cosine in cosines.items():
                    if row_id not in best or cosine > best[row_id][0]:
                        best[row_id] = (cosine, origin)
            for row_id, chunk in loaded.items():
                if row_id not in best:
                    continue
                cosine, origin = best[row_id]
                chunk["score"] = cosine
                added[row_id] = _Candidate(row_id, chunk, cosine, found_by=origin)
        known = {**candidates, **added}
        ranked_fts = [row_id for row_id in fts_ids if row_id in known]
        order = rrf_order(vector_order, ranked_fts)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("rag_stage_failed", stage=STAGE_HYBRID, error=type(exc).__name__)
        run.skip(STAGE_HYBRID, REASON_FTS_ERROR)
        return False
    candidates.update(added)
    for position, row_id in enumerate(ranked_fts, start=1):
        candidates[row_id].fts_rank = position
    ordered = {row_id: candidates[row_id] for row_id in order if row_id in candidates}
    candidates.clear()
    candidates.update(ordered)
    run.done(STAGE_HYBRID, started)
    return True


# D-08 amended by the user at the 15-09 checkpoint (2026-10-03): with hybrid on, an FTS hit
# below the threshold survives only on a keyword match, because on the calibration set every
# out-of-corpus question otherwise received an exempt chunk and below_threshold was unreachable.
FTS_EXEMPT_MIN_LEXICAL = 0.5


def _earns_fts_exemption(question: str, chunk: dict[str, Any]) -> bool:
    """True when an FTS hit matches an article number of the question or overlaps lexically."""
    chunk_articles = {parse_article(chunk.get("section")), parse_article(chunk.get("title"))}
    text: str = chunk.get("text") or ""
    for number in article_numbers(question):
        if number in chunk_articles or contains_number(text, number):
            return True
    return lexical_score(question, chunk) >= FTS_EXEMPT_MIN_LEXICAL


def _apply_threshold(
    ordered: list[_Candidate], config: PipelineConfig, hybrid_ran: bool, question: str
) -> list[_Candidate]:
    """Cut by raw cosine; an FTS hit with a keyword match survives when hybrid ran (D-07, D-08)."""
    survivors: list[_Candidate] = []
    for item in ordered:
        if item.cos >= config.threshold:
            survivors.append(item)
        elif (
            hybrid_ran
            and item.fts_rank is not None
            and _earns_fts_exemption(question, item.chunk)
        ):
            item.fts_exempt = True
            survivors.append(item)
        else:
            item.status = STATUS_BELOW_THRESHOLD
    return survivors


async def _lexical_stage(
    question: str, survivors: list[_Candidate], run: _Run
) -> list[_Candidate]:
    """Reorder survivors by fused cosine and lexical score."""
    started = time.perf_counter()
    try:
        chunks = [item.chunk for item in survivors]
        order, lex_scores = await asyncio.to_thread(lexical_rerank, question, chunks)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("rag_stage_failed", stage=STAGE_LEXICAL, error=type(exc).__name__)
        run.skip(STAGE_LEXICAL, REASON_STAGE_ERROR)
        return survivors
    for item, score in zip(survivors, lex_scores):
        item.lex = score
    run.done(STAGE_LEXICAL, started)
    return [survivors[index] for index in order]


async def _llm_stage(
    question: str,
    survivors: list[_Candidate],
    client: Any | None,
    model: str | None,
    run: _Run,
) -> list[_Candidate]:
    """Score the top survivors in one prompt and reorder that batch."""
    started = time.perf_counter()
    if client is None or model is None:
        run.skip(STAGE_LLM, REASON_NO_LLM)
        return survivors
    batch = survivors[:RERANK_TOP_N]
    try:
        outcome = await llm_rerank(client, model, question, [item.chunk for item in batch])
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("rag_stage_failed", stage=STAGE_LLM, error=type(exc).__name__)
        run.skip(STAGE_LLM, REASON_STAGE_ERROR)
        return survivors
    if outcome.reason is not None or outcome.value is None:
        run.skip(STAGE_LLM, outcome.reason or REASON_STAGE_ERROR)
        return survivors
    for item, score in zip(batch, outcome.value):
        item.llm = float(score)
    reordered = sorted(
        enumerate(batch), key=lambda pair: (-(pair[1].llm or 0.0), pair[0])
    )
    run.done(STAGE_LLM, started)
    return [item for _, item in reordered] + survivors[RERANK_TOP_N:]


def _serialise(item: _Candidate) -> dict[str, Any]:
    """Metadata-only trace entry; never carries chunk text."""
    chunk = item.chunk
    return {
        "chunk_id": chunk["chunk_id"],
        "file": chunk["source"],
        "section": chunk.get("section") or chunk.get("title"),
        "page": chunk.get("page"),
        "rank_before": item.rank_before,
        "rank_after": item.rank_after,
        "cos": round(item.cos, 4),
        "lex": item.lex,
        "fts_rank": item.fts_rank,
        "llm": item.llm,
        "found_by": item.found_by,
        "fts_exempt": item.fts_exempt,
        "status": item.status,
    }


def _final_chunk(item: _Candidate, rank: int) -> dict[str, Any]:
    """Chunk dict for the answer prompt with the search_kb keys and the raw cosine score."""
    chunk = {key: value for key, value in item.chunk.items() if key != "row_id"}
    chunk["rank"] = rank
    chunk["score"] = round(item.cos, 4)
    return chunk


async def run_retrieval_pipeline(
    session: AsyncSession,
    kb: KnowledgeBase | None,
    question: str,
    config: PipelineConfig,
    client: Any | None = None,
    model: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch candidates, cut by raw cosine, optionally rerank, and return the top-K with a trace."""
    started = time.perf_counter()
    run = _Run()
    results, vector = await retrieve_vectors(session, kb, question, config.candidate_k)
    assert kb is not None
    vectors: list[tuple[np.ndarray, str]] = [(vector, FOUND_ORIGINAL)]
    candidates: dict[int, _Candidate] = {
        item["row_id"]: _Candidate(item["row_id"], item, item["score"]) for item in results
    }

    rewritten: str | None = None
    rewrite_cosine: float | None = None
    if config.rewrite or config.history is not None:
        rewritten, rewrite_cosine = await _rewrite_stage(
            session, kb, question, config, client, model, candidates, vectors, run
        )
    ordered_map = {item.row_id: item for item in _sorted_by_cosine(candidates)}
    candidates.clear()
    candidates.update(ordered_map)

    hybrid_ran = False
    if config.hybrid:
        fts_text = f"{question} {rewritten}" if rewritten else question
        hybrid_ran = await _hybrid_stage(session, kb, fts_text, config, candidates, vectors, run)

    ordered = list(candidates.values())
    for position, item in enumerate(ordered, start=1):
        item.rank_before = position
    survivors = _apply_threshold(ordered, config, hybrid_ran, question)

    if config.lexical and survivors:
        survivors = await _lexical_stage(question, survivors, run)
    if config.llm_rerank and survivors:
        survivors = await _llm_stage(question, survivors, client, model, run)

    final = survivors[: config.top_k]
    for position, item in enumerate(final, start=1):
        item.status = STATUS_IN_ANSWER
        item.rank_after = position
    for item in survivors[config.top_k :]:
        item.status = STATUS_OUTSIDE_TOP_K

    verdict = VERDICT_BELOW_THRESHOLD if ordered and not survivors else VERDICT_OK
    latency_ms = int((time.perf_counter() - started) * 1000)
    trace: dict[str, Any] = {
        "verdict": verdict,
        "query": question,
        "rewritten": rewritten,
        "rewrite_cosine": rewrite_cosine,
        "condensed": config.history is not None and rewritten is not None,
        "history_pairs": len(config.history.pairs) if config.history is not None else 0,
        "config": {
            "candidate_k": config.candidate_k,
            "top_k": config.top_k,
            "threshold": config.threshold,
            "threshold_source": config.threshold_source,
            "lexical": config.lexical,
            "llm": config.llm_rerank,
            "hybrid": config.hybrid,
            "rewrite": config.rewrite,
            "history": config.history is not None,
        },
        "stages": [STAGE_THRESHOLD, *run.stages],
        "stage_ms": dict(run.stage_ms),
        "skipped": run.skipped,
        "latency_ms": latency_ms,
        "best_cosine": max((item.cos for item in ordered), default=None),
        "candidates": [_serialise(item) for item in ordered],
    }
    logger.info(
        "rag_pipeline_done",
        kb_id=kb.id,
        candidates=len(ordered),
        survivors=len(survivors),
        final=len(final),
        stages=trace["stages"],
        skipped=len(run.skipped),
        verdict=verdict,
        latency_ms=latency_ms,
    )
    return [_final_chunk(item, rank) for rank, item in enumerate(final, start=1)], trace


def mark_over_budget(trace: dict[str, Any], kept: int) -> None:
    """Flag answer candidates that did not fit the context budget."""
    for candidate in trace["candidates"]:
        rank_after = candidate.get("rank_after")
        if candidate["status"] == STATUS_IN_ANSWER and rank_after is not None and rank_after > kept:
            candidate["status"] = STATUS_OVER_BUDGET
