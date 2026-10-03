"""Tests for the env-secret resolver and provider tables."""

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from shared.config import is_valid_env_name, resolve_env_secret, settings
from shared.database import (
    async_session_factory,
    migrate_add_scheduledtask_provider_id,
)
from shared.models import LlmProvider, LlmProviderSeed
from tests.conftest import _create_user


@pytest.mark.parametrize("name", ["DEEPSEEK_API_KEY", "_X", "a1"])
def test_valid_env_names(name: str) -> None:
    """Well-formed names are accepted."""
    assert is_valid_env_name(name)


@pytest.mark.parametrize("name", ["", "1ABC", "MY-KEY", "MY KEY", "A" * 101, "KEY=1"])
def test_invalid_env_names(name: str) -> None:
    """Malformed names are rejected."""
    assert not is_valid_env_name(name)


def _env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    file = tmp_path / ".env"
    file.write_text(content, encoding="utf-8")
    monkeypatch.setattr(settings, "LLM_PROVIDER_ENV_FILE", str(file))


def test_resolve_from_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A name declared in the file resolves to its value."""
    _env_file(tmp_path, monkeypatch, "OPENROUTER_KEY=sk-file\n")
    assert resolve_env_secret("OPENROUTER_KEY") == "sk-file"


def test_process_env_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """For a declared name the process environment overrides the file."""
    _env_file(tmp_path, monkeypatch, "OPENROUTER_KEY=sk-file\n")
    monkeypatch.setenv("OPENROUTER_KEY", "sk-env")
    assert resolve_env_secret("OPENROUTER_KEY") == "sk-env"


def test_undeclared_process_var_is_not_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Arbitrary process variables never resolve."""
    _env_file(tmp_path, monkeypatch, "OPENROUTER_KEY=sk-file\n")
    monkeypatch.setenv("PATH", "/usr/bin")
    assert resolve_env_secret("PATH") is None


def test_builtin_deepseek_from_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The builtin DeepSeek name resolves from the process env without a file entry."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    assert resolve_env_secret("DEEPSEEK_API_KEY") == "sk-ds"


def test_builtin_deepseek_empty_is_none() -> None:
    """The conftest default (empty) resolves to None."""
    assert resolve_env_secret("DEEPSEEK_API_KEY") is None


@pytest.mark.parametrize("name", [None, "", "bad-name"])
def test_invalid_or_empty_name_is_none(name: str | None) -> None:
    """Missing or malformed names resolve to None."""
    assert resolve_env_secret(name) is None


def test_declared_empty_value_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A declared name with an empty value resolves to None."""
    _env_file(tmp_path, monkeypatch, "EMPTY_KEY=\n")
    assert resolve_env_secret("EMPTY_KEY") is None


async def test_migration_adds_column_once(tmp_path: Path) -> None:
    """The scheduledtask migration is idempotent."""
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mig.db'}")
    try:
        async with eng.begin() as conn:
            await conn.execute(text("CREATE TABLE scheduledtask (id INTEGER PRIMARY KEY)"))
            await migrate_add_scheduledtask_provider_id(conn)
            await migrate_add_scheduledtask_provider_id(conn)
            rows = (await conn.execute(text("PRAGMA table_info(scheduledtask)"))).fetchall()
        assert [r[1] for r in rows].count("provider_id") == 1
    finally:
        await eng.dispose()


async def test_unique_name_per_user() -> None:
    """Two providers with the same name for one user violate the constraint."""
    uid = await _create_user("prov-u1", "pw")
    async with async_session_factory() as session:
        session.add(LlmProvider(user_id=uid, name="A", base_url="http://x"))
        await session.commit()
        session.add(LlmProvider(user_id=uid, name="A", base_url="http://y"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_user_delete_cascades() -> None:
    """Deleting the user removes provider and seed rows."""
    uid = await _create_user("prov-u2", "pw")
    async with async_session_factory() as session:
        session.add(LlmProvider(user_id=uid, name="A", base_url="http://x"))
        session.add(LlmProviderSeed(user_id=uid, seed_key="lm_studio"))
        await session.commit()
        await session.execute(text("DELETE FROM user WHERE id = :id"), {"id": uid})
        await session.commit()
        for table in ("llmprovider", "llmproviderseed"):
            count = (await session.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar()
            assert count == 0
