"""Pure ranking, validation and calibration helpers for two-stage RAG retrieval."""

import math
import re
import statistics
from typing import Any

STOPWORDS: frozenset[str] = frozenset(
    {
        "что", "как", "при", "или", "для", "это", "его", "они", "она", "оно",
        "который", "которая", "которые", "какой", "какая", "какие", "если",
        "также", "тоже", "под", "над", "без", "про", "чем", "где", "когда",
        "есть", "быть", "был", "была", "были", "может", "нужно", "надо",
        "the", "and", "for",
    }
)
STEM_LEN: int = 5
MIN_TOKEN_LEN: int = 3
_WORD_RE: re.Pattern[str] = re.compile(r"[^\W_]+")
ARTICLE_NUMBER_RE: re.Pattern[str] = re.compile(r"\d+(?:\.\d+)+")
ARTICLE_WORD_RE: re.Pattern[str] = re.compile(
    r"(?<!\w)(?:ст\.?|стать(?:я|и|е|ю|ёй|ей))\s*(\d+(?:\.\d+)*)", re.IGNORECASE
)
ARTICLE_IN_SECTION_RE: re.Pattern[str] = re.compile(r"Статья\s+(\d+(?:[._-]\d+)*)")
LEX_OVERLAP_WEIGHT: float = 0.6
LEX_ARTICLE_WEIGHT: float = 0.6
ARTICLE_TEXT_ONLY_SCORE: float = 0.5
FUSION_COSINE_WEIGHT: float = 0.6
FUSION_LEXICAL_WEIGHT: float = 0.4
RRF_K: int = 60
MINMAX_EPSILON: float = 1e-6


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens without short tokens, stopwords and pure numbers."""
    tokens: list[str] = []
    for token in _WORD_RE.findall((text or "").lower()):
        if len(token) < MIN_TOKEN_LEN or token in STOPWORDS or token.isdigit():
            continue
        tokens.append(token)
    return tokens


def stems(text: str) -> set[str]:
    """Prefix stems of the content tokens of a text."""
    return {token[:STEM_LEN] for token in tokenize(text)}


def article_numbers(text: str) -> list[str]:
    """Article numbers mentioned in a text, in order of first appearance."""
    found: list[tuple[int, str]] = []
    for match in ARTICLE_NUMBER_RE.finditer(text or ""):
        found.append((match.start(), match.group(0)))
    for match in ARTICLE_WORD_RE.finditer(text or ""):
        found.append((match.start(1), match.group(1)))
    result: list[str] = []
    for _, number in sorted(found, key=lambda item: item[0]):
        if number not in result:
            result.append(number)
    return result


def parse_article(text: str | None) -> str | None:
    """Return the deepest article number in a breadcrumb, or None."""
    if not text:
        return None
    matches = ARTICLE_IN_SECTION_RE.findall(text)
    return matches[-1] if matches else None


def _contains_number(text: str, number: str) -> bool:
    """True when the number occurs as a whole token, not inside a longer number."""
    pattern = r"(?<!\d)(?<!\d\.)" + re.escape(number) + r"(?!\d|\.\d)"
    return re.search(pattern, text) is not None


def lexical_score(query: str, chunk: dict[str, Any]) -> float:
    """Lexical relevance of a chunk to a query in 0..1."""
    section: str = chunk.get("section") or ""
    title: str = chunk.get("title") or ""
    text: str = chunk.get("text") or ""
    query_stems = stems(query)
    overlap = 0.0
    if query_stems:
        overlap = len(query_stems & stems(section + " " + text)) / len(query_stems)
    article_match = 0.0
    numbers = article_numbers(query)
    if numbers:
        chunk_articles = {parse_article(section), parse_article(title)}
        if any(number in chunk_articles for number in numbers):
            article_match = 1.0
        elif any(_contains_number(text, number) for number in numbers):
            article_match = ARTICLE_TEXT_ONLY_SCORE
    return min(1.0, LEX_OVERLAP_WEIGHT * overlap + LEX_ARTICLE_WEIGHT * article_match)


def lexical_rerank(
    query: str, chunks: list[dict[str, Any]]
) -> tuple[list[int], list[float]]:
    """Order chunk indices by fused cosine and lexical score; return order and lexical scores."""
    lex_scores = [lexical_score(query, chunk) for chunk in chunks]
    rounded = [round(value, 4) for value in lex_scores]
    if len(chunks) < 2:
        return list(range(len(chunks))), rounded
    cosines = [float(chunk.get("score", 0.0)) for chunk in chunks]
    low, high = min(cosines), max(cosines)
    spread = high - low
    if spread < MINMAX_EPSILON:
        normalised = [1.0] * len(cosines)
    else:
        normalised = [(value - low) / spread for value in cosines]
    fused = [
        FUSION_COSINE_WEIGHT * norm + FUSION_LEXICAL_WEIGHT * lex
        for norm, lex in zip(normalised, lex_scores)
    ]
    order = sorted(range(len(chunks)), key=lambda index: -fused[index])
    return order, rounded


def rrf_order(vector_ids: list[int], fts_ids: list[int]) -> list[int]:
    """Reciprocal rank fusion of two ranked id lists."""
    scores: dict[int, float] = {}
    for ranked in (vector_ids, fts_ids):
        for rank, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (RRF_K + rank)
    vector_pos = {item: pos for pos, item in reversed(list(enumerate(vector_ids)))}
    fts_pos = {item: pos for pos, item in reversed(list(enumerate(fts_ids)))}
    big = len(vector_ids) + len(fts_ids) + 1
    return sorted(
        scores,
        key=lambda item: (-scores[item], vector_pos.get(item, big), fts_pos.get(item, big)),
    )


def build_fts_query(question: str) -> str | None:
    """FTS5 MATCH string built only from quoted terms, safe against operator injection."""
    terms: list[str] = []
    for number in ARTICLE_NUMBER_RE.findall(question or ""):
        terms.append(f'"{number}"')
    for token in tokenize(question):
        terms.append(f'"{token[:STEM_LEN]}"*')
    unique = list(dict.fromkeys(terms))
    return " OR ".join(unique) if unique else None


def _round_half_up(value: float) -> float:
    """Round to 2 decimals with halves going up (built-in round uses banker's rounding)."""
    return math.floor(round(value * 100, 6) + 0.5) / 100


def choose_threshold(
    gold_scores: list[float], ooc_top1: list[float]
) -> tuple[float, dict[str, Any]]:
    """Pick a relevance cutoff separating answerable from out-of-corpus top-1 scores."""
    if not gold_scores or not ooc_top1:
        return 0.0, {"separable": False, "method": "none"}
    stats: dict[str, Any] = {
        "gold_min": min(gold_scores),
        "gold_median": statistics.median(gold_scores),
        "gold_max": max(gold_scores),
        "ooc_min": min(ooc_top1),
        "ooc_median": statistics.median(ooc_top1),
        "ooc_max": max(ooc_top1),
        "n_gold": len(gold_scores),
        "n_ooc": len(ooc_top1),
    }
    if max(ooc_top1) < min(gold_scores):
        stats.update(separable=True, method="midpoint", youden_j=1.0)
        return _round_half_up((max(ooc_top1) + min(gold_scores)) / 2), stats
    best_t, best_j = 0.0, -math.inf
    for candidate in sorted(set(gold_scores) | set(ooc_top1)):
        gold_share = sum(1 for v in gold_scores if v >= candidate) / len(gold_scores)
        ooc_share = sum(1 for v in ooc_top1 if v >= candidate) / len(ooc_top1)
        j_value = gold_share - ooc_share
        if j_value > best_j + 1e-12:
            best_t, best_j = candidate, j_value
    stats.update(separable=False, method="youden", youden_j=round(best_j, 4))
    return _round_half_up(best_t), stats
