"""Tests for agent/invariants.py CRUD semantics against the GlobalInvariant table."""

import pytest

from agent import invariants
from shared.database import async_session_factory
from shared.models import GlobalInvariant


@pytest.mark.asyncio
async def test_create_global_invariant_persists_title_and_rule() -> None:
    """create_global returns a row with an id, the exact fields, and both timestamps set."""
    async with async_session_factory() as session:
        row = await invariants.create_global(
            session, "Без Docker", "Никогда не предлагай Docker",
        )
        assert row.id is not None
        assert row.title == "Без Docker"
        assert row.rule_text == "Никогда не предлагай Docker"
        assert row.created_at is not None
        assert row.updated_at is not None


@pytest.mark.asyncio
async def test_list_global_invariants_returns_all_rows_oldest_first() -> None:
    """list_global returns every row, ordered oldest-first by creation order."""
    async with async_session_factory() as session:
        first = await invariants.create_global(session, "A", "rule a")
        second = await invariants.create_global(session, "B", "rule b")
        third = await invariants.create_global(session, "C", "rule c")

    async with async_session_factory() as session:
        rows = await invariants.list_global(session)
        assert [row.id for row in rows] == [first.id, second.id, third.id]


@pytest.mark.asyncio
async def test_update_global_invariant_changes_fields_and_advances_updated_at() -> None:
    """update_global changes the supplied fields and bumps updated_at."""
    async with async_session_factory() as session:
        row = await invariants.create_global(session, "Original", "original rule")
        captured_updated_at = row.updated_at

    async with async_session_factory() as session:
        updated = await invariants.update_global(
            session, row.id, title="X", rule_text="Y",
        )
        assert updated is not None
        assert updated.title == "X"
        assert updated.rule_text == "Y"
        assert updated.updated_at >= captured_updated_at


@pytest.mark.asyncio
async def test_update_global_invariant_returns_none_for_unknown_id() -> None:
    """update_global on a nonexistent id returns None instead of raising."""
    async with async_session_factory() as session:
        result = await invariants.update_global(session, 999_999, title="X")
        assert result is None


@pytest.mark.asyncio
async def test_delete_global_invariant_removes_row() -> None:
    """delete_global removes the row; a second delete on the same id returns False."""
    async with async_session_factory() as session:
        row = await invariants.create_global(session, "Doomed", "will be deleted")

    async with async_session_factory() as session:
        deleted = await invariants.delete_global(session, row.id)
        assert deleted is True

    async with async_session_factory() as session:
        rows = await invariants.list_global(session)
        assert rows == []

    async with async_session_factory() as session:
        deleted_again = await invariants.delete_global(session, row.id)
        assert deleted_again is False


def test_global_invariant_table_has_no_ownership_columns() -> None:
    """The GlobalInvariant table has no user_id or chat_id column (D-02, structural)."""
    assert "user_id" not in GlobalInvariant.__table__.columns
    assert "chat_id" not in GlobalInvariant.__table__.columns
