"""Knowledge-base document loading: PDF text extraction, cleaning and plain-text decoding."""
import math
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from shared.logger import get_logger

logger = get_logger(__name__)

MSG_SCAN: str = "Не удалось извлечь текст из файла {name}. Возможно, это скан без текстового слоя."
MSG_PDF_BROKEN: str = "Не удалось прочитать файл {name}: файл повреждён или не является PDF."
MSG_ENCODING: str = (
    "Не удалось прочитать файл {name}: неизвестная кодировка (ожидается UTF-8 или Windows-1251)."
)
MSG_EMPTY: str = "Файл {name} не содержит текста."

HEADER_LINES: int = 3
FOOTER_LINES: int = 6
FREQUENCY_SHARE: float = 0.30
FREQUENCY_MIN_PAGES: int = 3
SCAN_MIN_AVG_CHARS: int = 100
SCAN_MIN_PAGE_CHARS: int = 50

# A line starting with one of these opens a new paragraph instead of continuing the previous one.
_MARKER_RE: re.Pattern[str] = re.compile(
    r"^(?:Статья\b|Глава\b|Раздел\b|Комментарий к статье|КонсультантПлюс: примечание"
    r"|\d+(?:\.\d+)*\.(?=\s|$)|\d+\)|\(Утратил)"
)
_HEADING_WORD_RE: re.Pattern[str] = re.compile(r"^(?:Статья|Глава|Раздел)$")
_PAGE_NUMBER_RE: re.Pattern[str] = re.compile(r"^(?:Страница\s+\d+|\d+)$")
_COMMENT_RE: re.Pattern[str] = re.compile(r"^Комментарий к статье")
_DIGITS_RE: re.Pattern[str] = re.compile(r"\d+")
_SPACES_RE: re.Pattern[str] = re.compile(r"[ \t]{2,}")

# Bounded `[^()]{0,400}?` keeps the match linear on 100 KB+ paragraphs.
_ANNOTATION_RE: re.Pattern[str] = re.compile(
    r"\((?:Часть|Пункт|Абзац|Статья|Наименование|Примечание|В редакции|в ред\.|п\. [\d.]+ (?:введен|в ред\.)"
    r"|абзац введен)[^()]{0,1200}?(?:См\. предыдущую редакцию|ред\.|N \d+-\s*ФЗ)\s*\)",
    re.DOTALL | re.IGNORECASE,
)
_CONSULTANT_NOTE_RE: re.Pattern[str] = re.compile(
    r"КонсультантПлюс: примечание\.[^\n]{0,600}?\([^()]{0,300}\)\.?"
)
_CONSULTANT_LABEL_RE: re.Pattern[str] = re.compile(r"КонсультантПлюс: примечание\.")
_SPACE_BEFORE_PUNCT_RE: re.Pattern[str] = re.compile(r"\s+([.,;])")
_HYPHEN_TAIL_RE: re.Pattern[str] = re.compile(r"\w-$")
_CONTINUATION_RE: re.Pattern[str] = re.compile(r"^([a-zа-яё]{3,})", re.IGNORECASE)


class KbLoadError(Exception):
    """User-facing document loading failure carrying a Russian message."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message: str = message


@dataclass(frozen=True)
class LoadedDocument:
    """A document reduced to clean text plus optional page boundaries."""

    filename: str
    title: str
    text: str
    page_offsets: list[int] | None
    page_count: int | None
    is_markdown: bool


def extract_pdf_pages(path: Path, filename: str | None = None) -> list[str]:
    """Return the raw text of every PDF page."""
    name: str = filename or path.name
    try:
        with pymupdf.open(str(path), filetype="pdf") as doc:
            return [page.get_text() for page in doc]
    except (pymupdf.FileDataError, pymupdf.EmptyFileError, RuntimeError, ValueError) as exc:
        logger.warning("kb_load_failed", filename=name, error=type(exc).__name__)
        raise KbLoadError(MSG_PDF_BROKEN.format(name=name)) from exc


def is_scan(pages: list[str]) -> bool:
    """Detect a PDF without a usable text layer."""
    if not pages:
        return True
    lengths: list[int] = [len(page.strip()) for page in pages]
    if sum(lengths) / len(pages) < SCAN_MIN_AVG_CHARS:
        return True
    sparse: int = sum(1 for length in lengths if length < SCAN_MIN_PAGE_CHARS)
    return sparse / len(pages) > 0.5


def _page_lines(page: str) -> list[str]:
    """Stripped non-empty lines of one page."""
    return [line.strip() for line in page.splitlines() if line.strip()]


def _normalize_key(line: str) -> str:
    """Digit-insensitive key used to spot repeated headers and footers."""
    return _DIGITS_RE.sub("#", line)


def _edge_indexes(count: int) -> set[int]:
    """Indexes of the first header lines and last footer lines of a page."""
    return set(range(min(HEADER_LINES, count))) | set(range(max(0, count - FOOTER_LINES), count))


def _repeated_edge_keys(pages_lines: list[list[str]]) -> set[str]:
    """Normalised edge lines present on a large enough share of pages."""
    if len(pages_lines) < FREQUENCY_MIN_PAGES:
        return set()
    counts: dict[str, int] = {}
    for lines in pages_lines:
        keys: set[str] = {_normalize_key(lines[i]) for i in _edge_indexes(len(lines))}
        for key in keys:
            counts[key] = counts.get(key, 0) + 1
    threshold: int = max(FREQUENCY_MIN_PAGES, math.ceil(FREQUENCY_SHARE * len(pages_lines)))
    return {key for key, n in counts.items() if n >= threshold}


def _is_noise_line(line: str) -> bool:
    """Page numbers, bare numbers and commentary headers carry no content."""
    return bool(_PAGE_NUMBER_RE.match(line) or _COMMENT_RE.match(line))


def _join_lines(lines: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Merge physical lines into paragraphs, remembering each paragraph's first page."""
    paragraphs: list[tuple[str, int]] = []
    for line, page_idx in lines:
        if not paragraphs:
            paragraphs.append((line, page_idx))
            continue
        current, first_page = paragraphs[-1]
        if _MARKER_RE.match(line) and not _HEADING_WORD_RE.match(current):
            paragraphs.append((line, page_idx))
            continue
        paragraphs[-1] = (_append_line(current, line), first_page)
    return paragraphs


def _append_line(current: str, line: str) -> str:
    """Append a line to a paragraph, merging a hyphenated word break when it is safe."""
    if _HYPHEN_TAIL_RE.search(current):
        match = _CONTINUATION_RE.match(line)
        if match is not None:
            return current[:-1] + line
    return f"{current} {line}"


def _strip_annotations(paragraph: str) -> str:
    """Remove edition annotations and consultant notes, keeping repeal stubs."""

    def _drop(match: re.Match[str]) -> str:
        return match.group(0) if "утратил" in match.group(0).lower() else ""

    cleaned: str = _CONSULTANT_NOTE_RE.sub(_drop, paragraph)
    cleaned = _CONSULTANT_LABEL_RE.sub("", cleaned)
    cleaned = _ANNOTATION_RE.sub(_drop, cleaned)
    cleaned = _SPACE_BEFORE_PUNCT_RE.sub(lambda m: m.group(1), cleaned)
    return _SPACES_RE.sub(" ", cleaned).strip()


def clean_pdf_pages(pages: list[str]) -> tuple[str, list[int]]:
    """Clean raw PDF page texts into paragraphs plus the offset where each page starts."""
    pages_lines: list[list[str]] = [_page_lines(page) for page in pages]
    repeated: set[str] = _repeated_edge_keys(pages_lines)

    flat: list[tuple[str, int]] = []
    for page_idx, lines in enumerate(pages_lines):
        edges: set[int] = _edge_indexes(len(lines)) if repeated else set()
        for idx, line in enumerate(lines):
            if idx in edges and _normalize_key(line) in repeated:
                continue
            if _is_noise_line(line):
                continue
            flat.append((line, page_idx))

    kept: list[tuple[str, int]] = []
    for paragraph, first_page in _join_lines(flat):
        cleaned: str = _strip_annotations(paragraph)
        if cleaned:
            kept.append((cleaned, first_page))

    text_parts: list[str] = []
    starts: list[int] = []
    cursor: int = 0
    for paragraph, _ in kept:
        starts.append(cursor)
        text_parts.append(paragraph)
        cursor += len(paragraph) + 2
    text: str = "\n\n".join(text_parts)

    offsets: list[int] = []
    for page_idx in range(len(pages)):
        offset: int = len(text)
        for (_, first_page), start in zip(kept, starts):
            if first_page >= page_idx:
                offset = start
                break
        offsets.append(offset)
    if offsets:
        offsets[0] = 0
    return text, offsets


def normalize_plain_text(text: str) -> str:
    """Normalise line endings and whitespace of TXT/MD content."""
    unified: str = text.replace("\r\n", "\n").replace("\r", "\n")
    stripped: str = "\n".join(line.rstrip() for line in unified.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", stripped).strip()


def read_text_file(path: Path, filename: str) -> str:
    """Decode a TXT/MD file as UTF-8 (BOM allowed) with a Windows-1251 fallback."""
    data: bytes = path.read_bytes()
    text: str
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1251")
        except UnicodeDecodeError as exc:
            logger.warning("kb_load_failed", filename=filename, error=type(exc).__name__)
            raise KbLoadError(MSG_ENCODING.format(name=filename)) from exc
    if not text.strip():
        raise KbLoadError(MSG_EMPTY.format(name=filename))
    return text


def load_document(path: Path, filename: str) -> LoadedDocument:
    """Load a PDF, TXT or MD file into clean text; raises KbLoadError with a Russian message."""
    suffix: str = Path(filename).suffix.lower()
    title: str = Path(filename).stem
    if suffix == ".pdf":
        pages: list[str] = extract_pdf_pages(path, filename)
        if is_scan(pages):
            logger.warning("kb_load_failed", filename=filename, error="scan")
            raise KbLoadError(MSG_SCAN.format(name=filename))
        text, offsets = clean_pdf_pages(pages)
        if not text.strip():
            logger.warning("kb_load_failed", filename=filename, error="empty_after_clean")
            raise KbLoadError(MSG_SCAN.format(name=filename))
        return LoadedDocument(filename, title, text, offsets, len(pages), False)
    if suffix in (".txt", ".md"):
        text = normalize_plain_text(read_text_file(path, filename))
        return LoadedDocument(filename, title, text, None, None, suffix == ".md")
    raise ValueError(f"unsupported file type: {suffix}")
