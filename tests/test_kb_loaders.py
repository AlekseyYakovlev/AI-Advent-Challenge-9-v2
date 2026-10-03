"""Golden and unit tests for knowledge-base document loading and PDF cleaning."""
import re
from pathlib import Path

import pymupdf
import pytest

from agent.kb_loaders import (
    MSG_EMPTY,
    MSG_ENCODING,
    MSG_PDF_BROKEN,
    MSG_SCAN,
    KbLoadError,
    LoadedDocument,
    clean_pdf_pages,
    is_scan,
    load_document,
    normalize_plain_text,
    read_text_file,
)

FIXTURES: Path = Path(__file__).parent / "fixtures" / "kb"
ARTICLE_RE: re.Pattern[str] = re.compile(r"^Статья[ \t]+(\d+(?:\.\d+)*(?:-\d+)?)\.", re.M)


def _load_pages(name: str) -> list[str]:
    """Read a raw page-text fixture split on form feed."""
    return (FIXTURES / name).read_text(encoding="utf-8").split("\f")


@pytest.fixture(scope="module")
def koap_pages() -> list[str]:
    """Raw КоАП page texts."""
    return _load_pages("koap_excerpt_pages.txt")


@pytest.fixture(scope="module")
def fz_pages() -> list[str]:
    """Raw ФЗ-196 page texts."""
    return _load_pages("fz196_excerpt_pages.txt")


def _make_text_pdf(path: Path, pages: int) -> None:
    """Write a PDF with several lines of Latin text on every page."""
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page()
        for line in range(12):
            page.insert_text(
                (50, 60 + line * 20),
                f"Page {n} line {line}: the quick brown fox jumps over the lazy dog.",
            )
    doc.save(str(path))
    doc.close()


def test_golden_koap_noise_removed(koap_pages: list[str]) -> None:
    text, _ = clean_pdf_pages(koap_pages)
    # Repeal stubs legitimately end with the edition note, so they are excluded from the check.
    checked = "\n".join(ln for ln in text.split("\n") if "тратил" not in ln)
    for noise in (
        r"Страница \d+",
        r"ИС «Техэксперт",
        r"Внимание! Документ с изменениями",
        r"Кодекс РФ от 30\.12\.2001 N 195-ФЗ",
        r"См\. предыдущую редакцию\)\s*$",
        r"Комментарий к статье",
    ):
        assert not re.search(noise, checked, re.M), noise
    assert "(Часть в редакции" not in text
    assert "Утратил" in text


def test_golden_koap_articles_preserved(koap_pages: list[str]) -> None:
    raw = "\n".join(koap_pages)
    text, _ = clean_pdf_pages(koap_pages)
    raw_numbers = set(ARTICLE_RE.findall(raw))
    clean_numbers = set(ARTICLE_RE.findall(text))
    assert "5.1" in clean_numbers
    assert clean_numbers == raw_numbers


def test_golden_koap_lines_are_joined(koap_pages: list[str]) -> None:
    text, _ = clean_pdf_pages(koap_pages)
    lines = [ln for ln in text.split("\n") if ln.strip()]
    short = [ln for ln in lines if len(ln.split()) <= 2]
    assert len(short) / len(lines) < 0.10


def test_golden_fz196_annotations_removed(fz_pages: list[str]) -> None:
    text, _ = clean_pdf_pages(fz_pages)
    assert "(в ред. Федерального закона" not in text
    assert "(в ред. Федеральных законов" not in text
    assert "КонсультантПлюс: примечание." not in text
    headings = re.findall(r"^Статья[ \t]+\d+\.", text, re.M)
    assert len(headings) >= 2
    assert not re.search(r"^Статья\s*$", text, re.M)


def test_page_offsets(koap_pages: list[str]) -> None:
    text, offsets = clean_pdf_pages(koap_pages)
    assert len(offsets) == len(koap_pages)
    assert offsets[0] == 0
    assert all(b >= a for a, b in zip(offsets, offsets[1:]))
    assert offsets[-1] <= len(text)


def test_dehyphenation() -> None:
    pages = ["Первая строка\nадми-\nнистративный кодекс\nтопливо- и газ"]
    text, _ = clean_pdf_pages(pages)
    assert "административный" in text
    assert "топливо- и" in text


def test_frequency_filter_skipped_for_short_documents() -> None:
    pages = ["Шапка документа\nТекст первой страницы", "Шапка документа\nТекст второй страницы"]
    text, _ = clean_pdf_pages(pages)
    assert text.count("Шапка документа") == 2


def test_frequency_filter_drops_repeated_footer() -> None:
    body = "\n".join(f"Строка {i} основного текста страницы." for i in range(12))
    pages = [
        f"{body}\nСодержимое {n}\nОфициальный колонтитул документа\nСтраница {n}"
        for n in range(5)
    ]
    text, _ = clean_pdf_pages(pages)
    assert "колонтитул" not in text
    assert "Страница" not in text
    assert "Строка 5 основного" in text


def test_is_scan_thresholds(koap_pages: list[str]) -> None:
    assert is_scan(["", "", " "]) is True
    assert is_scan(["x" * 90, "y" * 90]) is True
    assert is_scan(["a" * 800, "", "", ""]) is True
    assert is_scan(koap_pages) is False


def test_scan_pdf_raises_russian_error(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(50, 50, 200, 200), fill=(0.5, 0.5, 0.5))
    doc.save(str(path))
    doc.close()
    with pytest.raises(KbLoadError) as exc:
        load_document(path, "scan.pdf")
    assert exc.value.message == MSG_SCAN.format(name="scan.pdf")
    assert "Возможно, это скан без текстового слоя" in exc.value.message


def test_text_pdf_loads(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    _make_text_pdf(path, 3)
    doc = load_document(path, "doc.pdf")
    assert isinstance(doc, LoadedDocument)
    assert doc.page_count == 3
    assert doc.is_markdown is False
    assert doc.title == "doc"
    assert doc.page_offsets is not None and len(doc.page_offsets) == 3
    assert "quick brown fox" in doc.text


def test_broken_pdf_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.pdf"
    path.write_bytes(bytes(range(256)) * 8)
    with pytest.raises(KbLoadError) as exc:
        load_document(path, "bad.pdf")
    assert exc.value.message == MSG_PDF_BROKEN.format(name="bad.pdf")
    assert str(tmp_path) not in exc.value.message


def test_read_text_file_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes("﻿Привет мир".encode("utf-8"))
    assert read_text_file(path, "a.txt") == "Привет мир"


def test_read_text_file_cp1251(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes("Привет, мир".encode("cp1251"))
    assert read_text_file(path, "a.txt") == "Привет, мир"


def test_read_text_file_bad_encoding(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes(b"\x98\xff\x98\x98")
    with pytest.raises(KbLoadError) as exc:
        read_text_file(path, "a.txt")
    assert exc.value.message == MSG_ENCODING.format(name="a.txt")


def test_read_text_file_empty(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text("  \n\n ", encoding="utf-8")
    with pytest.raises(KbLoadError) as exc:
        read_text_file(path, "a.md")
    assert exc.value.message == MSG_EMPTY.format(name="a.md")


def test_load_markdown_flag(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_text("# Заголовок\r\n\r\nТекст  \r\n", encoding="utf-8")
    doc = load_document(path, "notes.md")
    assert doc.is_markdown is True
    assert doc.page_offsets is None and doc.page_count is None
    assert "\r" not in doc.text
    assert doc.title == "notes"


def test_normalize_plain_text() -> None:
    raw = "a  \r\nb\r\n\r\n\r\n\r\nc"
    assert normalize_plain_text(raw) == "a\nb\n\nc"
