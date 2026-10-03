"""Unit tests for knowledge base chunking (fixed and structural strategies)."""

from agent.kb_chunking import (
    MSG_BAD_OVERLAP,
    MSG_SIZE_TOO_LARGE,
    MSG_SIZE_TOO_SMALL,
    chunk_fixed,
    chunk_structural,
    page_for_offset,
    validate_chunk_params,
)
from agent.kb_limits import MAX_EMBED_CHARS


def _words(n_chars: int) -> str:
    out: list[str] = []
    total = 0
    i = 0
    while total < n_chars:
        w = f"слово{i}"
        out.append(w)
        total += len(w) + 1
        i += 1
    return " ".join(out)[:n_chars]


# ---------- validation ----------

def test_validate_ok() -> None:
    assert validate_chunk_params(1000, 150) is None
    assert validate_chunk_params(2000, 1000) is None
    assert validate_chunk_params(1000, 500) is None


def test_validate_errors() -> None:
    assert validate_chunk_params(99, 0) == MSG_SIZE_TOO_SMALL
    assert validate_chunk_params(2001, 0) == MSG_SIZE_TOO_LARGE
    assert validate_chunk_params(1000, -1) == MSG_BAD_OVERLAP
    assert validate_chunk_params(1000, 1000) == MSG_BAD_OVERLAP
    assert validate_chunk_params(1000, 501) == MSG_BAD_OVERLAP


# ---------- page_for_offset ----------

def test_page_for_offset() -> None:
    offs = [0, 100, 250]
    assert page_for_offset(offs, 0) == 1
    assert page_for_offset(offs, 99) == 1
    assert page_for_offset(offs, 100) == 2
    assert page_for_offset(offs, 999) == 3
    assert page_for_offset(None, 5) is None
    assert page_for_offset([], 5) is None


# ---------- fixed ----------

def test_fixed_empty() -> None:
    assert chunk_fixed("", 1000, 150) == []
    assert chunk_fixed("   \n  ", 1000, 150) == []


def test_fixed_sizes_and_overlap() -> None:
    text = _words(5000)
    chunks = chunk_fixed(text, 1000, 150)
    assert len(chunks) >= 5
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end >= len(text.rstrip()) - 1
    for c in chunks:
        assert len(c.text) <= 1000
        assert c.section is None
        assert c.text == text[c.char_start:c.char_end].strip()
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.char_start <= prev.char_end - 1
        assert prev.char_end - nxt.char_start <= 150 + 50


def test_fixed_snaps_to_whitespace() -> None:
    text = _words(3000)
    chunks = chunk_fixed(text, 1000, 150)
    for c in chunks[:-1]:
        assert text[c.char_end] == " " or text[c.char_end - 1] == " "


def test_fixed_hard_cut_without_spaces() -> None:
    text = "x" * 1500
    chunks = chunk_fixed(text, 1000, 150)
    assert len(chunks[0].text) == 1000
    assert chunks[-1].char_end == 1500


def test_fixed_forward_progress_small_overlap_edge() -> None:
    text = "a " * 500
    chunks = chunk_fixed(text, 100, 50)
    assert chunks
    starts = [c.char_start for c in chunks]
    assert starts == sorted(set(starts))


def test_fixed_page_start() -> None:
    text = _words(500)
    chunks = chunk_fixed(text, 200, 20, page_offsets=[0, 100, 200])
    assert chunks[0].page_start == 1
    assert chunks[-1].page_start == 3


# ---------- structural: legal ----------

LEGAL = (
    "Раздел I. Общие положения\n\n"
    "Глава 5. Правонарушения\n\n"
    "Статья 5.1. Нарушение права\n\nТекст статьи один.\n\n"
    "Статья 5.2. Другое\n\nТекст два.\n\n"
    "Глава 7. Собственность\n\n"
    "Статья 7.1. Самовольное занятие\n\nТекст.\n\n"
    "Статья 7.1-1. Суффикс\n\nТекст суффикса."
)


def test_legal_sections_and_breadcrumb() -> None:
    chunks = chunk_structural(LEGAL, doc_title="КоАП", is_markdown=False)
    assert [c.section for c in chunks] == [
        "Раздел I > Глава 5 > Статья 5.1",
        "Раздел I > Глава 5 > Статья 5.2",
        "Раздел I > Глава 7 > Статья 7.1",
        "Раздел I > Глава 7 > Статья 7.1-1",
    ]
    assert chunks[0].text.splitlines()[0] == (
        "КоАП > Раздел I > Глава 5 > Статья 5.1. Нарушение права"
    )
    assert "Текст статьи один." in chunks[0].text
    for c in chunks:
        assert LEGAL[c.char_start:c.char_end].strip() in c.text


def test_legal_inline_mention_not_split() -> None:
    text = (
        "Статья 1. Первая\n\nв соответствии со\nСтатья 5 закона мы делаем.\n\n"
        "Статья 2. Вторая\n\nТекст."
    )
    chunks = chunk_structural(text, doc_title="Д", is_markdown=False)
    assert [c.section for c in chunks] == ["Статья 1", "Статья 2"]


def test_legal_stub_is_tiny_chunk() -> None:
    text = (
        "Статья 5.2. Другое\n\nТекст.\n\n"
        "Статья 5.3. (Утратила силу в 2020 году)\n\n"
        "Статья 5.4. Следующая\n\nТекст."
    )
    chunks = chunk_structural(text, doc_title="Д", is_markdown=False)
    assert len(chunks) == 3
    assert chunks[1].section == "Статья 5.3"
    assert "Утратила силу" in chunks[1].text
    assert len(chunks[1].text) < 120


def test_legal_preamble() -> None:
    text = "Вводный текст документа.\n\nСтатья 1. Первая\n\nТекст."
    chunks = chunk_structural(text, doc_title="Д", is_markdown=False)
    assert chunks[0].section == "Преамбула"
    assert "Вводный текст" in chunks[0].text
    assert chunks[1].section == "Статья 1"


def test_legal_long_article_subsplit() -> None:
    paras = [f"Абзац {i}. " + _words(400) for i in range(22)]
    body = "\n\n".join(paras)
    assert len(body) > 9000
    text = "Статья 9. Большая\n\n" + body
    chunks = chunk_structural(text, doc_title="КоАП", is_markdown=False)
    assert len(chunks) > 4
    breadcrumb = "КоАП > Статья 9. Большая"
    for c in chunks:
        assert len(c.text) <= MAX_EMBED_CHARS
        assert c.text.startswith(breadcrumb + "\n")
        assert c.section == "Статья 9"
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.char_start < prev.char_end


def test_long_sentence_hard_cut() -> None:
    text = "Статья 1. Т\n\n" + "y" * 5000
    chunks = chunk_structural(text, doc_title="Д", is_markdown=False)
    assert len(chunks) >= 3
    assert all(len(c.text) <= MAX_EMBED_CHARS for c in chunks)


# ---------- structural: markdown / plain ----------

def test_markdown_headings() -> None:
    text = "# A\n\ntext a\n\n## B\n\ntext b"
    chunks = chunk_structural(text, doc_title="doc", is_markdown=True)
    assert [c.section for c in chunks] == ["A", "A > B"]
    assert chunks[1].text.splitlines()[0] == "doc > A > B"
    assert "text b" in chunks[1].text


def test_plain_paragraphs_packed() -> None:
    paras = [f"Параграф {i} " + "z" * 300 for i in range(10)]
    text = "\n\n".join(paras)
    chunks = chunk_structural(text, doc_title="doc", is_markdown=False)
    assert 1 < len(chunks) < 10
    assert all(len(c.text) <= MAX_EMBED_CHARS for c in chunks)
    assert all(c.section is None for c in chunks)
    joined = "\n".join(c.text for c in chunks)
    for p in paras:
        assert p in joined


def test_no_structure_single_section_split() -> None:
    text = _words(5000)
    chunks = chunk_structural(text, doc_title="doc", is_markdown=False)
    assert len(chunks) >= 3
    assert all(len(c.text) <= MAX_EMBED_CHARS for c in chunks)
    for c in chunks:
        assert 0 <= c.char_start < c.char_end <= len(text)


def test_structural_empty() -> None:
    assert chunk_structural("", doc_title="d", is_markdown=False) == []


def test_structural_page_start() -> None:
    text = "Статья 1. А\n\nТекст.\n\nСтатья 2. Б\n\nТекст."
    idx = text.index("Статья 2")
    chunks = chunk_structural(
        text, doc_title="d", is_markdown=False, page_offsets=[0, idx]
    )
    assert chunks[0].page_start == 1
    assert chunks[1].page_start == 2
