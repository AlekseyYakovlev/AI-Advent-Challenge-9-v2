"""PR review: budget, splitting, per-base retrieval, model calls and the merge call."""

import math
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlmodel.ext.asyncio.session import AsyncSession

from agent.code_chunking import parse_line_range
from agent.limits import generation_gate
from agent.llm_client import ChatCompletionResult, count_tokens
from agent.providers import KIND_LM_STUDIO
from agent.rag import CYRILLIC_SAFETY, RagFailure, build_rag_block, sources_from_chunks
from agent.rag_llm import REJECTED_STATUS_CODES, STAGE_REASONING_EFFORT
from agent.rag_pipeline import PipelineConfig, run_retrieval_pipeline
from agent.review_github import PrFile, PullRequestData
from agent.review_prompts import (
    FRAGMENTS_INSTRUCTION,
    HEADING_ARCHITECTURE,
    HEADING_BUGS,
    HEADING_RECOMMENDATIONS,
    NO_REMARKS,
    REPAIR_REMINDER,
    build_merge_messages,
    build_review_messages,
)
from shared.logger import get_logger
from shared.models import KnowledgeBase

logger = get_logger(__name__)

CODE_LM_STUDIO_UNAVAILABLE: str = "LM_STUDIO_UNAVAILABLE"
CODE_LLM_UNREACHABLE: str = "LLM_UNREACHABLE"
CODE_LLM_ERROR: str = "LLM_ERROR"
CODE_REVIEW_BAD_OUTPUT: str = "REVIEW_BAD_OUTPUT"
CODE_EMBEDDER_UNAVAILABLE: str = "EMBEDDER_UNAVAILABLE"
CODE_PR_TOO_LARGE: str = "PR_TOO_LARGE"
CODE_INDEX_UNAVAILABLE: str = "CODE_INDEX_UNAVAILABLE"

DOCS_TOP_K: int = 4
CODE_TOP_K: int = 6
CANDIDATE_K: int = 20
DOCS_THRESHOLD: float = 0.35
CODE_THRESHOLD: float = 0.0
MAX_QUERY_CHARS: int = 1500
ANSWER_MAX_TOKENS: int = 3000
PROMPT_OVERHEAD_TOKENS: int = 1200
RAG_SHARE: float = 0.25
MAX_PARTS: int = 20
REVIEW_TEMPERATURE: float = 0.2
MAX_SECTION_CHARS: int = 12000

MSG_PR_TOO_LARGE: str = "Pull Request слишком большой для ревью выбранной моделью."
MSG_CONTEXT_TOO_SMALL: str = "Длины контекста модели не хватает даже на служебный текст ревью."
TRUNCATION_LINE: str = "…текст сокращён."
DOCS_QUERY_PHRASE: str = "правила проекта, архитектура, соглашения по коду"
KIND_DOCS: str = "docs"
KIND_CODE: str = "code"
ZERO_WIDTH_SPACE: str = "​"

MSG_MODEL_UNREACHABLE: str = "Модель недоступна."
MSG_MODEL_HTTP_ERROR: str = "Модель вернула ошибку HTTP {status}."
MSG_BAD_OUTPUT: str = "Модель вернула ответ не в ожидаемом формате."
MSG_EMBEDDER_UNAVAILABLE: str = "LM Studio недоступен для эмбеддингов."
MSG_CODE_INDEX_UNAVAILABLE: str = "Индекс кода недоступен."
NOTE_DOCS_NOT_SELECTED: str = "Документация не использовалась: база документации не выбрана."
NOTE_DOCS_UNAVAILABLE: str = "Документация не использовалась: база документации недоступна."
NOTE_UNREVIEWED: str = "Не проанализированы (нет текста изменений): {names}."
FAILURE_EMBEDDER: str = "embedder_unavailable"

MAX_TITLE_QUERY_CHARS: int = 300
MAX_ADDED_LINES_IN_QUERY: int = 40
MAX_ADDED_LINE_CHARS: int = 200
MAX_QUERY_NAMES: int = 60

_DIFF_HEADER_RE = re.compile(r"^diff --git ", re.MULTILINE)
_HUNK_HEADER_RE = re.compile(r"^@@[^@\n]*@@[ \t]*(.*)$", re.MULTILINE)
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_KEYWORDS = frozenset(
    {
        "def", "class", "function", "async", "await", "public", "private", "protected",
        "static", "void", "int", "return", "const", "let", "var", "self", "this", "func",
        "export", "default", "interface", "struct", "enum", "impl", "final", "override",
    }
)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_HEADING_RE = re.compile(
    r"^[ \t]{0,3}#{2,3}[ \t]*(Потенциальные баги|Архитектурные проблемы|Рекомендации)[ \t]*:?[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)
_FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)|!\[[^\]\n]*\]\[[^\]\n]*\]")
_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^<>\n]*>")
_MENTION_RE = re.compile(r"(?<![\w`])@(?=[A-Za-z0-9])")


class ReviewError(Exception):
    """The review could not be produced; `code` is machine-readable, `detail` is Russian."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code: str = code
        self.detail: str = detail


@dataclass
class ReviewPart:
    """One model call's worth of the diff."""

    files: list[str]
    diff_text: str
    note: str | None


@dataclass
class ReviewSections:
    """The three sections of a review."""

    bugs: str
    architecture: str
    recommendations: str


@dataclass
class ReviewResult:
    """A finished review with the sources it relied on."""

    sections: ReviewSections
    sources_docs: list[dict] = field(default_factory=list)
    sources_code: list[dict] = field(default_factory=list)
    docs_used: bool = False
    parts: int = 0
    unreviewed_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """Token estimate with headroom for providers that count Cyrillic and code higher."""
    return math.ceil(count_tokens(text) * CYRILLIC_SAFETY)


def _diff_segment_path(header: str, segment_lines: list[str]) -> str:
    """Return the new path of one `diff --git` segment."""
    for line in segment_lines[1:]:
        if line.startswith("rename to "):
            return line[len("rename to ") :]
        if line.startswith("@@"):
            break
    rest = header[len("diff --git ") :]
    if rest.startswith("a/"):
        index = rest.find(" b/")
        while index != -1:
            left, right = rest[2:index], rest[index + 3 :]
            if left == right:
                return right
            index = rest.find(" b/", index + 1)
        last = rest.rfind(" b/")
        if last != -1:
            return rest[last + 3 :]
    return rest.split(" ")[-1]


def split_diff_by_file(diff: str) -> dict[str, str]:
    """Map the new path of every changed file to its segment of a unified diff."""
    if not diff:
        return {}
    segments: dict[str, str] = {}
    current: list[str] = []

    def flush() -> None:
        if current:
            path = _diff_segment_path(current[0], current)
            segments[path] = "\n".join(current)

    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            flush()
            current = [line]
        elif current:
            current.append(line)
    flush()
    return segments


def _file_text(file: PrFile, segments: dict[str, str]) -> str | None:
    """Return the diff text of one file or None when nothing is known about its change."""
    if file.patch is not None:
        return f"diff --git a/{file.filename} b/{file.filename}\n{file.patch}"
    return segments.get(file.filename)


def _split_oversized_line(line: str, budget: int) -> list[str]:
    """Cut one huge line by characters into pieces of at most `budget` characters."""
    size = max(1, budget)
    return [line[start : start + size] for start in range(0, len(line), size)]


def _pack_lines(lines: list[str], budget: int) -> list[list[str]]:
    """Group lines so each group's estimate stays within the budget."""
    groups: list[list[str]] = []
    current: list[str] = []
    used = 0
    for raw in lines:
        for line in _split_oversized_line(raw, budget) if estimate_tokens(raw) > budget else [raw]:
            cost = estimate_tokens(line) + 1
            if current and used + cost > budget:
                groups.append(current)
                current, used = [], 0
            current.append(line)
            used += cost
    if current:
        groups.append(current)
    return groups


def _split_file_text(path: str, text: str, available: int) -> list[str]:
    """Split one oversized file diff on hunk boundaries, then on line boundaries."""
    lines = text.split("\n")
    first_hunk = next((i for i, line in enumerate(lines) if line.startswith("@@")), len(lines))
    header = "\n".join(lines[:first_hunk])
    body = lines[first_hunk:]
    budget = max(1, available - estimate_tokens(header) - 4)
    hunks: list[list[str]] = []
    for line in body:
        if line.startswith("@@") or not hunks:
            hunks.append([line])
        else:
            hunks[-1].append(line)
    pieces: list[str] = []
    current: list[str] = []
    used = 0
    for hunk in hunks:
        hunk_text = "\n".join(hunk)
        cost = estimate_tokens(hunk_text) + 1
        if cost > budget:
            if current:
                pieces.append("\n".join(current))
                current, used = [], 0
            pieces.extend("\n".join(group) for group in _pack_lines(hunk, budget))
            continue
        if current and used + cost > budget:
            pieces.append("\n".join(current))
            current, used = [], 0
        current.append(hunk_text)
        used += cost
    if current:
        pieces.append("\n".join(current))
    return [f"{header}\n{piece}" if header else piece for piece in pieces]


def _available_tokens(context_length: int) -> int:
    """Tokens one call may spend on the diff after the fixed overhead and fragments."""
    rag_budget = int(context_length * RAG_SHARE)
    return context_length - PROMPT_OVERHEAD_TOKENS - ANSWER_MAX_TOKENS - rag_budget


def plan_parts(
    pr: PullRequestData, context_length: int
) -> tuple[list[ReviewPart], list[str]]:
    """Decide between one call and a per-part review; return the parts and unreviewed files."""
    available = _available_tokens(context_length)
    if available <= 0:
        raise ReviewError(CODE_PR_TOO_LARGE, MSG_CONTEXT_TOO_SMALL)
    names = [file.filename for file in pr.files]
    if pr.diff.strip() and estimate_tokens(pr.diff) <= available:
        return [ReviewPart(names, pr.diff, None)], []

    segments = split_diff_by_file(pr.diff)
    items: list[tuple[str, str, str | None]] = []
    unreviewed: list[str] = []
    for file in pr.files:
        text = _file_text(file, segments)
        if text is None or not text.strip():
            unreviewed.append(file.filename)
            continue
        if estimate_tokens(text) <= available:
            items.append((file.filename, text, None))
            continue
        note = f"часть файла {file.filename}"
        for piece in _split_file_text(file.filename, text, available):
            items.append((file.filename, piece, note))

    parts: list[ReviewPart] = []
    current: list[tuple[str, str, str | None]] = []
    used = 0

    def close() -> None:
        notes = list(dict.fromkeys(note for _, _, note in current if note))
        parts.append(
            ReviewPart(
                list(dict.fromkeys(path for path, _, _ in current)),
                "\n".join(text for _, text, _ in current),
                "; ".join(notes) if notes else None,
            )
        )

    for item in items:
        cost = estimate_tokens(item[1]) + 1
        if current and used + cost > available:
            close()
            current, used = [], 0
        current.append(item)
        used += cost
    if current:
        close()
    if len(parts) > MAX_PARTS:
        raise ReviewError(CODE_PR_TOO_LARGE, MSG_PR_TOO_LARGE)
    return parts, unreviewed


def _hunk_names(diff_text: str) -> list[str]:
    """Identifiers from hunk-header context (`@@ ... @@ def name`)."""
    names: list[str] = []
    for match in _HUNK_HEADER_RE.finditer(diff_text):
        for word in _IDENTIFIER_RE.findall(match.group(1)):
            if word not in _KEYWORDS and word not in names:
                names.append(word)
    return names[:MAX_QUERY_NAMES]


def _added_lines(diff_text: str) -> list[str]:
    """First meaningful added lines, without the leading plus."""
    lines: list[str] = []
    for line in diff_text.split("\n"):
        if line.startswith("+") and not line.startswith("+++"):
            stripped = line[1:].strip()
            if stripped:
                lines.append(stripped[:MAX_ADDED_LINE_CHARS])
                if len(lines) >= MAX_ADDED_LINES_IN_QUERY:
                    break
    return lines


def build_query(pr: PullRequestData, part: ReviewPart, kind: str) -> str:
    """Build the retrieval query for one part; `kind` is `docs` or `code`."""
    paths = "\n".join(part.files)
    names = " ".join(_hunk_names(part.diff_text))
    if kind == KIND_DOCS:
        reserved = len(DOCS_QUERY_PHRASE) + 1
        head = "\n".join(
            piece for piece in (pr.title[:MAX_TITLE_QUERY_CHARS], paths, names) if piece
        )
        return f"{head[: MAX_QUERY_CHARS - reserved]}\n{DOCS_QUERY_PHRASE}"
    pieces = [piece for piece in (paths, names, "\n".join(_added_lines(part.diff_text))) if piece]
    return "\n".join(pieces)[:MAX_QUERY_CHARS]


def parse_review(text: str | None) -> ReviewSections | None:
    """Split a model reply into the three sections; None when the format is wrong."""
    if not text:
        return None
    cleaned = _THINK_RE.sub("", text).strip()
    matches = list(_HEADING_RE.finditer(cleaned))
    if len(matches) != 3:
        return None
    found: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        key = match.group(1).lower()
        if key in found:
            return None
        found[key] = cleaned[match.end() : end].strip() or NO_REMARKS
    try:
        return ReviewSections(
            bugs=found[HEADING_BUGS[4:].lower()],
            architecture=found[HEADING_ARCHITECTURE[4:].lower()],
            recommendations=found[HEADING_RECOMMENDATIONS[4:].lower()],
        )
    except KeyError:
        return None


def _clean_plain_text(text: str) -> str:
    """Remove images, raw HTML and live mentions from text that is not code."""
    text = _IMAGE_RE.sub("", text)
    text = _SCRIPT_RE.sub("", text)
    text = _HTML_COMMENT_RE.sub("", text)
    text = _HTML_TAG_RE.sub("", text)
    return _MENTION_RE.sub(f"@{ZERO_WIDTH_SPACE}", text)


def _clean_outside_inline_code(text: str) -> str:
    """Clean a stretch that is outside fenced blocks, leaving inline code untouched."""
    out: list[str] = []
    position = 0
    for match in _INLINE_CODE_RE.finditer(text):
        out.append(_clean_plain_text(text[position : match.start()]))
        out.append(match.group(0))
        position = match.end()
    out.append(_clean_plain_text(text[position:]))
    return "".join(out)


def sanitize_markdown(text: str) -> str:
    """Make model output safe to publish: no pings, images or raw HTML, bounded length."""
    out: list[str] = []
    position = 0
    for match in _FENCE_RE.finditer(text):
        out.append(_clean_outside_inline_code(text[position : match.start()]))
        out.append(match.group(0))
        position = match.end()
    out.append(_clean_outside_inline_code(text[position:]))
    cleaned = "".join(out)
    if len(cleaned) <= MAX_SECTION_CHARS:
        return cleaned
    limit = MAX_SECTION_CHARS - len(TRUNCATION_LINE) - 6
    cut = cleaned[:limit].rstrip()
    if cut.count("```") % 2 == 1:
        cut += "\n```"
    return f"{cut}\n{TRUNCATION_LINE}"


@dataclass
class _CallStats:
    """Counts model calls of one review run."""

    model_calls: int = 0


def _render_sections(sections: ReviewSections) -> str:
    """Render the three sections as Markdown with the canonical headings."""
    return (
        f"{HEADING_BUGS}\n{sections.bugs}\n\n"
        f"{HEADING_ARCHITECTURE}\n{sections.architecture}\n\n"
        f"{HEADING_RECOMMENDATIONS}\n{sections.recommendations}"
    )


def _sanitize_sections(sections: ReviewSections) -> ReviewSections:
    """Sanitise every section; a section that became empty reads as no remarks."""
    return ReviewSections(
        bugs=sanitize_markdown(sections.bugs).strip() or NO_REMARKS,
        architecture=sanitize_markdown(sections.architecture).strip() or NO_REMARKS,
        recommendations=sanitize_markdown(sections.recommendations).strip() or NO_REMARKS,
    )


async def _complete_once(
    client: Any, messages: list[dict[str, str]], model: str
) -> ChatCompletionResult:
    """Call the model without tools and with reasoning off; repeat once on HTTP 400/422."""
    try:
        return await client.complete_chat_detailed(
            messages=messages,
            model=model,
            temperature=REVIEW_TEMPERATURE,
            max_tokens=ANSWER_MAX_TOKENS,
            extra_body={"reasoning_effort": STAGE_REASONING_EFFORT},
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code not in REJECTED_STATUS_CODES:
            raise
        logger.info("review_reasoning_control_rejected", model=model, status_code=status_code)
    return await client.complete_chat_detailed(
        messages=messages,
        model=model,
        temperature=REVIEW_TEMPERATURE,
        max_tokens=ANSWER_MAX_TOKENS,
        extra_body=None,
    )


async def _call_model(
    client: Any, messages: list[dict[str, str]], model: str, provider_kind: str | None
) -> ChatCompletionResult:
    """Run one tool-less model call inside the generation gate and map transport errors."""
    try:
        async with generation_gate.slot():
            return await _complete_once(client, messages, model)
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        logger.error("review_model_http_error", model=model, status_code=status_code)
        raise ReviewError(
            CODE_LLM_ERROR, MSG_MODEL_HTTP_ERROR.format(status=status_code)
        ) from None
    except httpx.TransportError as exc:
        logger.error("review_model_unreachable", model=model, error=type(exc).__name__)
        code = (
            CODE_LM_STUDIO_UNAVAILABLE if provider_kind == KIND_LM_STUDIO else CODE_LLM_UNREACHABLE
        )
        raise ReviewError(code, MSG_MODEL_UNREACHABLE) from None


def _with_reminder(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Copy the messages with the format reminder appended to the last user message."""
    copy = [dict(message) for message in messages]
    copy[-1]["content"] = copy[-1]["content"] + REPAIR_REMINDER
    return copy


async def _generate(
    client: Any,
    messages: list[dict[str, str]],
    model: str,
    provider_kind: str | None,
    stats: _CallStats,
) -> ReviewSections:
    """Call the model and parse the reply; one repair attempt on a malformed reply."""
    for attempt in range(2):
        prompt = messages if attempt == 0 else _with_reminder(messages)
        result = await _call_model(client, prompt, model, provider_kind)
        stats.model_calls += 1
        sections = parse_review(result.content)
        if sections is not None:
            return sections
        logger.warning(
            "review_bad_output",
            model=model,
            attempt=attempt,
            finish_reason=result.finish_reason,
            has_content=result.content is not None,
        )
    raise ReviewError(CODE_REVIEW_BAD_OUTPUT, MSG_BAD_OUTPUT)


def _add_sources(target: list[dict], kept: list[dict[str, Any]], with_lines: bool) -> None:
    """Append metadata of used fragments, skipping ones already listed."""
    seen = {(item["file"], item["section"]) for item in target}
    for item in sources_from_chunks(kept):
        key = (item["file"], item["section"])
        if key in seen:
            continue
        seen.add(key)
        entry: dict[str, Any] = {"file": item["file"], "section": item["section"]}
        if with_lines:
            line_range = parse_line_range(item["section"])
            entry["lines"] = f"{line_range[0]}-{line_range[1]}" if line_range else None
        target.append(entry)


async def _retrieve_docs(
    session: AsyncSession, kb: KnowledgeBase, query: str
) -> list[dict[str, Any]] | None:
    """Search the documentation base; None when it cannot be used, error on a dead embedder."""
    config = PipelineConfig(candidate_k=CANDIDATE_K, top_k=DOCS_TOP_K, threshold=DOCS_THRESHOLD)
    try:
        chunks, _trace = await run_retrieval_pipeline(session, kb, query, config)
    except RagFailure as failure:
        if failure.code == FAILURE_EMBEDDER:
            raise ReviewError(CODE_EMBEDDER_UNAVAILABLE, MSG_EMBEDDER_UNAVAILABLE) from None
        logger.warning("review_docs_unavailable", code=failure.code)
        return None
    return chunks


async def _retrieve_code(
    session: AsyncSession, kb: KnowledgeBase, query: str
) -> list[dict[str, Any]]:
    """Search the code base; any failure stops the review."""
    config = PipelineConfig(candidate_k=CANDIDATE_K, top_k=CODE_TOP_K, threshold=CODE_THRESHOLD)
    try:
        chunks, _trace = await run_retrieval_pipeline(session, kb, query, config)
    except RagFailure as failure:
        if failure.code == FAILURE_EMBEDDER:
            raise ReviewError(CODE_EMBEDDER_UNAVAILABLE, MSG_EMBEDDER_UNAVAILABLE) from None
        logger.warning("review_code_unavailable", code=failure.code)
        raise ReviewError(CODE_INDEX_UNAVAILABLE, MSG_CODE_INDEX_UNAVAILABLE) from None
    return chunks


def _top_score(chunks: list[dict[str, Any]]) -> float | None:
    """Best raw cosine among fragments, or None when there are none."""
    return max((float(chunk["score"]) for chunk in chunks), default=None)


def _part_note(part: ReviewPart, index: int, total: int) -> str | None:
    """Tell the model which part of a large PR it is looking at."""
    if total == 1:
        return part.note
    intro = f"Это часть {index} из {total} большого Pull Request; анализируй только эту часть."
    return f"{intro} {part.note}" if part.note else intro


async def _merge_sections(
    client: Any,
    model: str,
    provider_kind: str | None,
    title: str,
    partials: list[ReviewSections],
    available: int,
    stats: _CallStats,
) -> ReviewSections:
    """Merge partial reviews into one, level by level when they do not fit one call."""
    current = list(partials)
    while len(current) > 1:
        total = sum(estimate_tokens(_render_sections(item)) for item in current)
        if total <= available:
            groups = [current]
        else:
            groups = [current[start : start + 2] for start in range(0, len(current), 2)]
        merged: list[ReviewSections] = []
        for group in groups:
            if len(group) == 1:
                merged.append(group[0])
                continue
            messages = build_merge_messages(title, [_render_sections(item) for item in group])
            merged.append(await _generate(client, messages, model, provider_kind, stats))
        current = merged
    return current[0]


async def run_review(
    session: AsyncSession,
    pr: PullRequestData,
    docs_kb: KnowledgeBase | None,
    code_kb: KnowledgeBase,
    client: Any,
    model: str,
    context_length: int,
    provider_kind: str | None = None,
) -> ReviewResult:
    """Review a PR with documentation and code retrieval; merge parts for a large diff."""
    parts, unreviewed = plan_parts(pr, context_length)
    stats = _CallStats()
    notes: list[str] = []
    sources_docs: list[dict] = []
    sources_code: list[dict] = []
    docs_active = docs_kb is not None
    docs_used = False
    if docs_kb is None:
        notes.append(NOTE_DOCS_NOT_SELECTED)
    fragments_budget = int(context_length * RAG_SHARE) // 2
    partials: list[ReviewSections] = []

    for index, part in enumerate(parts, start=1):
        started = time.perf_counter()
        docs_chunks: list[dict[str, Any]] = []
        if docs_active and docs_kb is not None:
            found = await _retrieve_docs(session, docs_kb, build_query(pr, part, KIND_DOCS))
            if found is None:
                docs_active = False
                notes.append(NOTE_DOCS_UNAVAILABLE)
            else:
                docs_used = True
                docs_chunks = found
        code_chunks = await _retrieve_code(session, code_kb, build_query(pr, part, KIND_CODE))
        docs_block, docs_kept, _ = build_rag_block(
            docs_chunks, fragments_budget, instruction=FRAGMENTS_INSTRUCTION
        )
        code_block, code_kept, _ = build_rag_block(
            code_chunks, fragments_budget, instruction=FRAGMENTS_INSTRUCTION
        )
        _add_sources(sources_docs, docs_kept, with_lines=False)
        _add_sources(sources_code, code_kept, with_lines=True)
        messages = build_review_messages(
            pr.title,
            pr.body,
            part.diff_text,
            docs_block,
            code_block,
            _part_note(part, index, len(parts)),
        )
        partials.append(await _generate(client, messages, model, provider_kind, stats))
        logger.info(
            "review_part_done",
            pr_number=pr.number,
            part=index,
            files=len(part.files),
            docs_fragments=len(docs_kept),
            code_fragments=len(code_kept),
            top_docs_score=_top_score(docs_chunks),
            top_code_score=_top_score(code_chunks),
            prompt_tokens_estimate=sum(estimate_tokens(m["content"]) for m in messages),
            seconds=round(time.perf_counter() - started, 2),
        )

    if not partials:
        sections = ReviewSections(NO_REMARKS, NO_REMARKS, NO_REMARKS)
    else:
        sections = await _merge_sections(
            client,
            model,
            provider_kind,
            pr.title,
            partials,
            _available_tokens(context_length),
            stats,
        )
    if unreviewed:
        notes.append(NOTE_UNREVIEWED.format(names=", ".join(unreviewed)))
    logger.info("review_done", pr_number=pr.number, parts=len(parts), model_calls=stats.model_calls)
    return ReviewResult(
        sections=_sanitize_sections(sections),
        sources_docs=sources_docs,
        sources_code=sources_code,
        docs_used=docs_used,
        parts=len(parts),
        unreviewed_files=unreviewed,
        notes=notes,
    )
