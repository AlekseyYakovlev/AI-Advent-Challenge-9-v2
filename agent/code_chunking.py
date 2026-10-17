"""Split source files into fragments for the code knowledge base."""

import ast
import re
from pathlib import PurePosixPath

from agent.kb_chunking import ChunkDraft

MAX_CHUNK_CHARS = 2000
WINDOW_LINES = 50
WINDOW_OVERLAP_LINES = 8
MODULE_NAME = "<module>"

# A very long path or name must not eat the room of the body.
_MAX_HEADER_CHARS = 300
_LINE_RANGE_RE = re.compile(r"L(\d+)-(\d+)\)?$")


def chunk_code(path: str, text: str) -> list[ChunkDraft]:
    """Split one source file: Python by definitions, any other file by line windows."""
    if PurePosixPath(path.replace("\\", "/")).suffix.lower() == ".py":
        return chunk_python(path, text)
    return chunk_lines(path, text)


def chunk_python(path: str, text: str) -> list[ChunkDraft]:
    """Split a Python file by top-level functions, classes and methods using `ast`."""
    lines, offsets = _split_lines(text)
    if not lines:
        return []
    if "\x00" in text:
        return chunk_lines(path, text)
    try:
        tree = ast.parse("\n".join(lines))
    except (SyntaxError, ValueError, RecursionError):
        return chunk_lines(path, text)

    drafts: list[ChunkDraft] = []
    covered_to = 0
    for start, end, name in _definition_spans(tree):
        start = max(start, covered_to + 1)
        if start > end:
            continue
        drafts.extend(_gap_fragments(path, lines, offsets, covered_to + 1, start - 1))
        drafts.extend(_span_fragments(path, name, lines, offsets, start, end))
        covered_to = end
    drafts.extend(_gap_fragments(path, lines, offsets, covered_to + 1, len(lines)))
    return drafts


def chunk_lines(
    path: str, text: str, name: str | None = None, first_line: int = 1
) -> list[ChunkDraft]:
    """Split text into overlapping line windows; `first_line` is the number of its first line."""
    lines, offsets = _split_lines(text)
    if not lines:
        return []
    step = WINDOW_LINES - WINDOW_OVERLAP_LINES
    shift = first_line - 1
    drafts: list[ChunkDraft] = []
    begin = 1
    while begin <= len(lines):
        end = min(begin + WINDOW_LINES - 1, len(lines))
        if _has_content(lines, begin, end):
            drafts.extend(
                _span_fragments(path, name, lines, offsets, begin, end, line_shift=shift)
            )
        if end == len(lines):
            break
        begin += step
    return drafts


def parse_line_range(section: str | None) -> tuple[int, int] | None:
    """Return the 1-based inclusive line range stored in a fragment section, or None."""
    if not section:
        return None
    match = _LINE_RANGE_RE.search(section)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _split_lines(text: str) -> tuple[list[str], list[int]]:
    """Normalise line endings and return the lines with each line's start offset."""
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalised:
        return [], []
    lines = normalised.split("\n")
    if lines[-1] == "":
        lines.pop()
    offsets: list[int] = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line) + 1
    return lines, offsets


def _has_content(lines: list[str], start: int, end: int) -> bool:
    """Whether any line in the 1-based inclusive range holds a non-whitespace character."""
    return any(line.strip() for line in lines[start - 1 : end])


def _definition_spans(tree: ast.Module) -> list[tuple[int, int, str]]:
    """Return (start, end, name) for each top-level definition, methods split out."""
    spans: list[tuple[int, int, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spans.append((_node_start(node), node.end_lineno or node.lineno, node.name))
        elif isinstance(node, ast.ClassDef):
            spans.extend(_class_spans(node))
    return spans


def _class_spans(node: ast.ClassDef) -> list[tuple[int, int, str]]:
    """Split a class into its header and one fragment per direct method."""
    start = _node_start(node)
    end = node.end_lineno or node.lineno
    methods = [
        child
        for child in node.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not methods:
        return [(start, end, node.name)]
    starts = [_node_start(method) for method in methods]
    spans = [(start, starts[0] - 1, node.name)]
    for index, method in enumerate(methods):
        # Class-level statements after a method stay with that method.
        method_end = starts[index + 1] - 1 if index + 1 < len(methods) else end
        spans.append((starts[index], method_end, f"{node.name}.{method.name}"))
    return spans


def _node_start(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> int:
    """First line of a definition, including its decorators."""
    return min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])


def _gap_fragments(
    path: str, lines: list[str], offsets: list[int], start: int, end: int
) -> list[ChunkDraft]:
    """Fragments for module-level lines between definitions; whitespace-only gaps are dropped."""
    if start > end or not _has_content(lines, start, end):
        return []
    return _span_fragments(path, MODULE_NAME, lines, offsets, start, end)


def _header(path: str, name: str | None) -> str:
    """The first line of every fragment text."""
    header = f"# {path} :: {name}" if name else f"# {path}"
    return header[:_MAX_HEADER_CHARS]


def _section(name: str | None, start: int, end: int) -> str:
    """The section label of a fragment."""
    return f"{name} (L{start}-{end})" if name else f"L{start}-{end}"


def _span_fragments(
    path: str,
    name: str | None,
    lines: list[str],
    offsets: list[int],
    start: int,
    end: int,
    line_shift: int = 0,
) -> list[ChunkDraft]:
    """Build capped fragments for a 1-based inclusive line span.

    A span that does not fit `MAX_CHUNK_CHARS` is cut on line boundaries; a single line
    that alone does not fit is cut by characters. `line_shift` converts local line
    numbers to the numbers shown in sections.
    """
    header = _header(path, name)
    room = max(MAX_CHUNK_CHARS - len(header) - 1, 1)
    drafts: list[ChunkDraft] = []
    part_start = start
    size = 0
    for number in range(start, end + 1):
        line = lines[number - 1]
        if len(line) > room:
            if number > part_start:
                drafts.append(
                    _make(header, name, lines, offsets, part_start, number - 1, line_shift)
                )
            drafts.extend(_cut_long_line(header, name, line, offsets[number - 1], number, room, line_shift))
            part_start, size = number + 1, 0
            continue
        added = len(line) + (1 if number > part_start else 0)
        if size + added > room:
            drafts.append(_make(header, name, lines, offsets, part_start, number - 1, line_shift))
            part_start, size = number, len(line)
        else:
            size += added
    if part_start <= end:
        drafts.append(_make(header, name, lines, offsets, part_start, end, line_shift))
    return [draft for draft in drafts if draft.text.split("\n", 1)[-1].strip()]


def _make(
    header: str,
    name: str | None,
    lines: list[str],
    offsets: list[int],
    start: int,
    end: int,
    line_shift: int,
) -> ChunkDraft:
    """One fragment made of whole lines."""
    body = "\n".join(lines[start - 1 : end])
    char_start = offsets[start - 1]
    char_end = max(offsets[end - 1] + len(lines[end - 1]), char_start + 1)
    return ChunkDraft(
        text=f"{header}\n{body}",
        section=_section(name, start + line_shift, end + line_shift),
        char_start=char_start,
        char_end=char_end,
    )


def _cut_long_line(
    header: str,
    name: str | None,
    line: str,
    line_offset: int,
    number: int,
    room: int,
    line_shift: int,
) -> list[ChunkDraft]:
    """Cut one over-long line into character pieces that share its line number."""
    pieces: list[ChunkDraft] = []
    for begin in range(0, len(line), room):
        piece = line[begin : begin + room]
        pieces.append(
            ChunkDraft(
                text=f"{header}\n{piece}",
                section=_section(name, number + line_shift, number + line_shift),
                char_start=line_offset + begin,
                char_end=line_offset + begin + len(piece),
            )
        )
    return pieces
