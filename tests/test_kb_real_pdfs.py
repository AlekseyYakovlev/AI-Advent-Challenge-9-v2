"""Golden tests against the real ФЗ-196 and КоАП РФ PDFs; skipped when the files are absent."""
import re
import time
from pathlib import Path

import pytest

from agent.kb_chunking import ChunkDraft, chunk_fixed, chunk_structural
from agent.kb_limits import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE, MAX_EMBED_CHARS
from agent.kb_loaders import LoadedDocument, load_document

RAG_DIR: Path = Path(r"C:\Projects\RAG")
FZ_PATH: Path = RAG_DIR / "19951210_20260626_FZ_N_196_FZ.pdf"
KOAP_PATH: Path = RAG_DIR / (
    "Kodex_ot_30_12_2001_N_195-FZ_Kodex_Rossiyskoy_Federatsii_ob_administrativnyh_"
    "pravonarusheniyah_s..._Text.pdf"
)

KOAP_UNIQUE_ARTICLES: int = 907
KOAP_CHAPTERS: int = 33
FZ196_UNIQUE_ARTICLES: int = 34

ARTICLE_RE: re.Pattern[str] = re.compile(r"^Статья[ \t]+(\d+(?:\.\d+)*(?:-\d+)?)\.", re.M)
CHAPTER_RE: re.Pattern[str] = re.compile(r"^Глава[ \t]+\d+", re.M)
# Pattern -> tolerated residue. Page furniture must vanish completely; a few annotations with
# nested parentheses or very long edition lists survive the bounded annotation regex.
NOISE_LIMITS: dict[str, int] = {
    r"Страница \d+": 0,
    r"ИС «Техэксперт": 0,
    r"КонсультантПлюс: примечание\.": 0,
    r"См\. предыдущую редакцию\)\s*$": 60,
    r"в ред\. Федерального закона": 5,
}

needs_fz = pytest.mark.skipif(not FZ_PATH.exists(), reason="real PDF not available")
needs_koap = pytest.mark.skipif(not KOAP_PATH.exists(), reason="real PDF not available")


@pytest.fixture(scope="module")
def fz_doc() -> LoadedDocument:
    """Cleaned ФЗ-196."""
    return load_document(FZ_PATH, FZ_PATH.name)


@pytest.fixture(scope="module")
def koap_load() -> tuple[LoadedDocument, float]:
    """Cleaned КоАП plus load duration in seconds."""
    started: float = time.monotonic()
    doc: LoadedDocument = load_document(KOAP_PATH, KOAP_PATH.name)
    return doc, time.monotonic() - started


def _chunk_both(doc: LoadedDocument) -> dict[str, list[ChunkDraft]]:
    """Chunk a document with the fixed and structural strategies."""
    return {
        "fixed": chunk_fixed(
            doc.text, DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP, doc.page_offsets
        ),
        "structural": chunk_structural(
            doc.text, doc_title=doc.title, is_markdown=doc.is_markdown, page_offsets=doc.page_offsets
        ),
    }


def _assert_no_noise(text: str) -> None:
    """Assert footer and annotation patterns are gone (repeal stubs are exempt)."""
    checked: str = "\n".join(ln for ln in text.split("\n") if "тратил" not in ln)
    for noise, limit in NOISE_LIMITS.items():
        assert len(re.findall(noise, checked, re.M)) <= limit, noise


@needs_fz
def test_fz196_clean(fz_doc: LoadedDocument) -> None:
    _assert_no_noise(fz_doc.text)


@needs_koap
def test_koap_clean(koap_load: tuple[LoadedDocument, float]) -> None:
    _assert_no_noise(koap_load[0].text)


@needs_koap
def test_koap_golden_counts(koap_load: tuple[LoadedDocument, float]) -> None:
    text: str = koap_load[0].text
    assert len(CHAPTER_RE.findall(text)) == KOAP_CHAPTERS
    assert len(set(ARTICLE_RE.findall(text))) == KOAP_UNIQUE_ARTICLES


@needs_fz
def test_fz196_golden_counts(fz_doc: LoadedDocument) -> None:
    assert len(set(ARTICLE_RE.findall(fz_doc.text))) == FZ196_UNIQUE_ARTICLES


@needs_fz
@pytest.mark.parametrize("strategy", ["fixed", "structural"])
def test_fz196_chunks_within_cap(fz_doc: LoadedDocument, strategy: str) -> None:
    chunks: list[ChunkDraft] = _chunk_both(fz_doc)[strategy]
    assert chunks
    assert all(len(c.text) <= MAX_EMBED_CHARS for c in chunks)


@needs_koap
@pytest.mark.parametrize("strategy", ["fixed", "structural"])
def test_koap_chunks_within_cap(koap_load: tuple[LoadedDocument, float], strategy: str) -> None:
    chunks: list[ChunkDraft] = _chunk_both(koap_load[0])[strategy]
    assert chunks
    assert all(len(c.text) <= MAX_EMBED_CHARS for c in chunks)


@needs_koap
def test_koap_structural_sections_and_repeal(koap_load: tuple[LoadedDocument, float]) -> None:
    chunks: list[ChunkDraft] = _chunk_both(koap_load[0])["structural"]
    sections: list[str] = [c.section for c in chunks if c.section]
    assert any("Глава 5" in s and "Статья 5.1" in s for s in sections)
    assert any("Утратил" in c.text for c in chunks)


@needs_koap
def test_koap_pipeline_is_fast() -> None:
    started: float = time.monotonic()
    doc: LoadedDocument = load_document(KOAP_PATH, KOAP_PATH.name)
    _chunk_both(doc)
    assert time.monotonic() - started < 30
