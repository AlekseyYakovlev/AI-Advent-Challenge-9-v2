"""PR review: budget, splitting, per-base retrieval, model calls and the merge call."""

import math
import re
from dataclasses import dataclass, field

from agent.llm_client import count_tokens
from agent.rag import CYRILLIC_SAFETY
from agent.review_github import PrFile, PullRequestData
from agent.review_prompts import (
    HEADING_ARCHITECTURE,
    HEADING_BUGS,
    HEADING_RECOMMENDATIONS,
    NO_REMARKS,
)
from shared.logger import get_logger

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
