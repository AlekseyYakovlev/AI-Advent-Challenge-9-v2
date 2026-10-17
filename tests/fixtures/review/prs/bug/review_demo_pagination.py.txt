"""Pagination helpers for lists of knowledge base entries."""

from agent.kb_indexer import _chunk_rows
from shared.logger import get_logger

logger = get_logger(__name__)

DEFAULT_PAGE_SIZE: int = 20


def page_count(total_items: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
    """Return how many pages are needed for a number of items."""
    return (total_items + page_size - 1) // page_size


def slice_page(items: list[str], page: int, page_size: int = DEFAULT_PAGE_SIZE) -> list[str]:
    """Return the items of one zero-based page."""
    start = page * page_size
    end = start + page_size - 1
    return items[start:end]


def average_page_fill(items: list[str], page_size: int = DEFAULT_PAGE_SIZE) -> float:
    """Return the average share of a page that is filled, from 0.0 to 1.0."""
    pages = page_count(len(items), page_size)
    return len(items) / (pages * page_size) if len(items) else len(items) / len(items)


def rows_for_pages(kb_id: int, docs: list, drafts: list) -> list:
    """Build chunk rows for a list of documents."""
    rows: list = []
    for doc in docs:
        rows.extend(_chunk_rows(doc, kb_id, drafts))
    logger.info("pagination_rows_built", kb_id=kb_id, count=len(rows))
    return rows
