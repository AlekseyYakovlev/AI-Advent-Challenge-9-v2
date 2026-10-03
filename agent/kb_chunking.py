"""Pure text chunking for the knowledge base: fixed-size and structural strategies."""

import re
from bisect import bisect_right
from dataclasses import dataclass

from agent.kb_limits import MAX_EMBED_CHARS, MIN_CHUNK_SIZE, STRUCT_SUBSPLIT_OVERLAP

MSG_SIZE_TOO_SMALL = "Размер чанка должен быть не меньше 100"
MSG_SIZE_TOO_LARGE = "Размер чанка не должен превышать 2000"
MSG_BAD_OVERLAP = "Перекрытие должно быть меньше размера чанка и не больше его половины"

MAX_BREADCRUMB_CHARS = 300
PREAMBLE_LABEL = "Преамбула"

# Line-anchored with bounded classes: `[ \t]+` never crosses a newline, no nested quantifiers.
ARTICLE_RE = re.compile(r"^Статья[ \t]+(\d+(?:\.\d+)*(?:-\d+)?)\.[ \t]*(.*)$", re.M)
CHAPTER_RE = re.compile(r"^(Глава|Раздел)[ \t]+([\dIVXLC]+)\.?[ \t]*(.*)$", re.M)
MD_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.M)
PARA_SEP_RE = re.compile(r"\n[ \t]*\n\s*")
SENTENCE_END_RE = re.compile(r"(?<=[.!?;])\s+")


@dataclass(frozen=True)
class ChunkDraft:
    """A chunk of source text with its location metadata."""

    text: str
    section: str | None
    char_start: int
    char_end: int
    page_start: int | None = None


@dataclass
class _Section:
    """A structural section: breadcrumb tail, metadata path and source body span."""

    parts: list[str]
    crumb_tail: list[str]
    start: int
    end: int
    keep_empty: bool = False


def validate_chunk_params(size: int, overlap: int) -> str | None:
    """Return a Russian error message for invalid chunk size/overlap, else None."""
    if size < MIN_CHUNK_SIZE:
        return MSG_SIZE_TOO_SMALL
    if size > MAX_EMBED_CHARS:
        return MSG_SIZE_TOO_LARGE
    if overlap < 0 or overlap >= size or overlap > size // 2:
        return MSG_BAD_OVERLAP
    return None


def page_for_offset(page_offsets: list[int] | None, offset: int) -> int | None:
    """Return the 1-based page containing the character offset, or None without offsets."""
    if not page_offsets:
        return None
    return max(1, bisect_right(page_offsets, offset))


def _strip_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Narrow [start, end) to exclude surrounding whitespace."""
    segment = text[start:end]
    lead = len(segment) - len(segment.lstrip())
    return start + lead, start + lead + len(segment.strip())


def chunk_fixed(
    text: str,
    size: int,
    overlap: int,
    page_offsets: list[int] | None = None,
) -> list[ChunkDraft]:
    """Split text into windows of at most `size` chars sharing `overlap` chars."""
    chunks: list[ChunkDraft] = []
    total = len(text)
    pos = 0
    while pos < total:
        end = min(pos + size, total)
        if end < total:
            window_from = pos + int(size * 0.8)
            ws = max(
                text.rfind(" ", window_from, end),
                text.rfind("\n", window_from, end),
                text.rfind("\t", window_from, end),
            )
            if ws != -1:
                end = ws + 1
        start_c, end_c = _strip_span(text, pos, end)
        if end_c > start_c:
            chunks.append(
                ChunkDraft(
                    text=text[start_c:end_c],
                    section=None,
                    char_start=start_c,
                    char_end=end_c,
                    page_start=page_for_offset(page_offsets, start_c),
                )
            )
        if end >= total:
            break
        pos = max(pos + 1, end - overlap)
    return chunks


def _find_cut(text: str, pos: int, hard_end: int, min_cut: int) -> int:
    """Pick the cut point in (min_cut, hard_end]: paragraph, sentence, whitespace, hard."""
    para = text.rfind("\n\n", pos, hard_end)
    if para >= min_cut:
        return para + 2
    sentence_cut = -1
    for m in SENTENCE_END_RE.finditer(text, pos, hard_end):
        if m.end() >= min_cut:
            sentence_cut = m.end()
    if sentence_cut != -1:
        return sentence_cut
    ws = max(text.rfind(" ", pos, hard_end), text.rfind("\n", pos, hard_end))
    if ws >= min_cut:
        return ws + 1
    return hard_end


def _subsplit(text: str, start: int, end: int, available: int) -> list[tuple[int, int]]:
    """Split [start, end) into spans of at most `available` chars with a small overlap."""
    spans: list[tuple[int, int]] = []
    pos = start
    while pos < end:
        hard_end = min(pos + available, end)
        if hard_end >= end:
            cut = end
        else:
            cut = _find_cut(text, pos, hard_end, pos + available // 3)
        spans.append((pos, cut))
        if cut >= end:
            break
        nxt = cut - STRUCT_SUBSPLIT_OVERLAP
        pos = nxt if nxt > pos else cut
    return spans


def _make_breadcrumb(doc_title: str, tail: list[str]) -> str:
    """Join document title and section path, capped at MAX_BREADCRUMB_CHARS."""
    crumb = " > ".join([doc_title, *tail])
    if len(crumb) > MAX_BREADCRUMB_CHARS:
        crumb = crumb[: MAX_BREADCRUMB_CHARS - 1] + "…"
    return crumb


def _legal_sections(text: str) -> list[_Section]:
    """Sections for Раздел/Глава/Статья structured text."""
    events: list[tuple[int, int, str, re.Match[str]]] = []
    for m in CHAPTER_RE.finditer(text):
        events.append((m.start(), m.end(), "chapter", m))
    for m in ARTICLE_RE.finditer(text):
        events.append((m.start(), m.end(), "article", m))
    events.sort(key=lambda ev: ev[0])

    sections: list[_Section] = []
    if events and text[: events[0][0]].strip():
        sections.append(_Section([PREAMBLE_LABEL], [PREAMBLE_LABEL], 0, events[0][0]))

    razdel: str | None = None
    glava: str | None = None
    for i, (_, line_end, kind, m) in enumerate(events):
        body_end = events[i + 1][0] if i + 1 < len(events) else len(text)
        if kind == "chapter":
            label = f"{m.group(1)} {m.group(2)}"
            if m.group(1) == "Раздел":
                razdel, glava = label, None
            else:
                glava = label
            path = [p for p in (razdel, glava) if p]
            full = f"{label}. {m.group(3).strip()}".rstrip(". ") if m.group(3).strip() else label
            sections.append(_Section(path, path[:-1] + [full], line_end, body_end))
        else:
            label = f"Статья {m.group(1)}"
            title = m.group(2).strip()
            tail_last = f"{label}. {title}" if title else f"{label}."
            path = [p for p in (razdel, glava) if p]
            sections.append(
                _Section(
                    path + [label],
                    path + [tail_last],
                    line_end,
                    body_end,
                    keep_empty=True,
                )
            )
    return sections


def _markdown_sections(text: str) -> list[_Section]:
    """Sections for Markdown `#` headings with a heading stack."""
    matches = list(MD_HEADING_RE.finditer(text))
    sections: list[_Section] = []
    if text[: matches[0].start()].strip():
        sections.append(_Section([PREAMBLE_LABEL], [PREAMBLE_LABEL], 0, matches[0].start()))
    stack: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip().rstrip("#").strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        path = [t for _, t in stack]
        sections.append(_Section(path, list(path), m.end(), body_end))
    return sections


def _paragraph_sections(text: str, available: int) -> list[_Section]:
    """Pack blank-line separated paragraphs greedily into sections of up to `available` chars."""
    spans: list[tuple[int, int]] = []
    prev = 0
    for m in PARA_SEP_RE.finditer(text):
        spans.append((prev, m.start()))
        prev = m.end()
    spans.append((prev, len(text)))
    spans = [s for s in spans if text[s[0]:s[1]].strip()]

    sections: list[_Section] = []
    cur_start: int | None = None
    cur_end = 0
    for s, e in spans:
        if cur_start is not None and e - cur_start <= available:
            cur_end = e
            continue
        if cur_start is not None:
            sections.append(_Section([], [], cur_start, cur_end))
        cur_start, cur_end = s, e
    if cur_start is not None:
        sections.append(_Section([], [], cur_start, cur_end))
    return sections


def chunk_structural(
    text: str,
    *,
    doc_title: str,
    is_markdown: bool,
    page_offsets: list[int] | None = None,
    limit: int = MAX_EMBED_CHARS,
) -> list[ChunkDraft]:
    """Split text at legal/Markdown/paragraph boundaries, prefixing each chunk with a breadcrumb."""
    if not text.strip():
        return []

    base_available = limit - len(_make_breadcrumb(doc_title, [])) - 1
    if ARTICLE_RE.search(text):
        sections = _legal_sections(text)
    elif is_markdown and MD_HEADING_RE.search(text):
        sections = _markdown_sections(text)
    elif PARA_SEP_RE.search(text):
        sections = _paragraph_sections(text, base_available)
    else:
        sections = [_Section([], [], 0, len(text))]

    chunks: list[ChunkDraft] = []
    for sec in sections:
        crumb = _make_breadcrumb(doc_title, sec.crumb_tail)
        section_name = " > ".join(sec.parts) or None
        b_start, b_end = _strip_span(text, sec.start, sec.end)
        if b_end <= b_start:
            if not sec.keep_empty:
                continue
            chunks.append(
                ChunkDraft(
                    text=crumb,
                    section=section_name,
                    char_start=sec.start,
                    char_end=sec.start,
                    page_start=page_for_offset(page_offsets, sec.start),
                )
            )
            continue
        available = limit - len(crumb) - 1
        if b_end - b_start <= available:
            spans = [(b_start, b_end)]
        else:
            spans = [_strip_span(text, a, b) for a, b in _subsplit(text, b_start, b_end, available)]
        for a, b in spans:
            if b <= a:
                continue
            chunks.append(
                ChunkDraft(
                    text=f"{crumb}\n{text[a:b]}",
                    section=section_name,
                    char_start=a,
                    char_end=b,
                    page_start=page_for_offset(page_offsets, a),
                )
            )
    return chunks
