"""Tests for user-scoped MCP server config persistence, env merging and cascade delete."""

from collections.abc import Callable, Coroutine
from typing import Any

from agent.mcp_config import (
    ENV_MASK,
    create_server,
    delete_server,
    get_server,
    list_servers,
    load_args,
    load_env,
    update_server,
)
from shared.database import async_session_factory, engine
from shared.models import User

SeedUser = Callable[[str, str], Coroutine[Any, Any, Any]]


async def _create(user_id: int, name: str = "srv", **overrides: Any) -> Any:
    """Create a server config with sensible defaults, returning the persisted row."""
    params: dict[str, Any] = {
        "name": name,
        "command": "python",
        "args": ["server.py"],
        "env": {},
        "cwd": None,
        "enabled": True,
    }
    params.update(overrides)
    async with async_session_factory() as session:
        return await create_server(session, user_id, **params)


async def test_create_and_list_scoped_by_user(seed_user: SeedUser) -> None:
    """Each user only sees their own configs, ordered by id."""
    user_a = await seed_user("alice", "pw")
    user_b = await seed_user("bob", "pw")
    first = await _create(user_a.id, "a-one")
    second = await _create(user_a.id, "a-two")
    other = await _create(user_b.id, "b-one")

    async with async_session_factory() as session:
        a_rows = await list_servers(session, user_a.id)
        b_rows = await list_servers(session, user_b.id)

    assert [row.id for row in a_rows] == [first.id, second.id]
    assert [row.id for row in b_rows] == [other.id]


async def test_get_server_other_user_returns_none(seed_user: SeedUser) -> None:
    """A config owned by another user is indistinguishable from a missing one."""
    user_a = await seed_user("alice", "pw")
    user_b = await seed_user("bob", "pw")
    a_server = await _create(user_a.id)

    async with async_session_factory() as session:
        assert await get_server(session, user_b.id, a_server.id) is None
        assert await get_server(session, user_a.id, a_server.id) is not None
        assert await get_server(session, user_a.id, 9999) is None


async def test_args_roundtrip_with_spaces(seed_user: SeedUser) -> None:
    """Args survive as an exact list of strings, without shell parsing."""
    user = await seed_user("alice", "pw")
    args = ["C:\\Program Files\\My Dir", "--flag", ""]
    created = await _create(user.id, args=args)

    async with async_session_factory() as session:
        row = await get_server(session, user.id, created.id)

    assert row is not None
    assert load_args(row) == args


async def test_update_env_merge_preserves_masked_values(seed_user: SeedUser) -> None:
    """Masked keys keep their stored value, new keys are added, absent keys are dropped."""
    user = await seed_user("alice", "pw")
    created = await _create(user.id, env={"API_KEY": "secret1", "OLD": "x"})

    async with async_session_factory() as session:
        row = await get_server(session, user.id, created.id)
        assert row is not None
        await update_server(session, row, env={"API_KEY": ENV_MASK, "NEW": "v"})

    async with async_session_factory() as session:
        row = await get_server(session, user.id, created.id)

    assert row is not None
    assert load_env(row) == {"API_KEY": "secret1", "NEW": "v"}


async def test_update_env_never_persists_mask_for_unknown_key(seed_user: SeedUser) -> None:
    """A masked value for a key with nothing stored is skipped, not saved literally."""
    user = await seed_user("alice", "pw")
    created = await _create(user.id, env={})

    async with async_session_factory() as session:
        row = await get_server(session, user.id, created.id)
        assert row is not None
        await update_server(session, row, env={"GHOST": ENV_MASK})
        assert load_env(row) == {}


async def test_update_partial_fields_and_clear_cwd(seed_user: SeedUser) -> None:
    """Only supplied fields change; cwd is cleared only when cwd_set is True."""
    user = await seed_user("alice", "pw")
    created = await _create(user.id, "orig", command="node", args=["a.js"], cwd="C:\\work")
    original_updated_at = created.updated_at

    async with async_session_factory() as session:
        row = await get_server(session, user.id, created.id)
        assert row is not None
        await update_server(session, row, name="renamed")
        assert row.name == "renamed"
        assert row.command == "node"
        assert load_args(row) == ["a.js"]
        assert row.cwd == "C:\\work"

        await update_server(session, row, cwd=None, cwd_set=True)
        assert row.cwd is None
        assert row.updated_at > original_updated_at


async def test_update_enabled_toggle(seed_user: SeedUser) -> None:
    """The enabled flag persists when toggled off."""
    user = await seed_user("alice", "pw")
    created = await _create(user.id)

    async with async_session_factory() as session:
        row = await get_server(session, user.id, created.id)
        assert row is not None
        await update_server(session, row, enabled=False)

    async with async_session_factory() as session:
        row = await get_server(session, user.id, created.id)

    assert row is not None
    assert row.enabled is False


async def test_delete_server(seed_user: SeedUser) -> None:
    """A deleted config is no longer retrievable."""
    user = await seed_user("alice", "pw")
    created = await _create(user.id)

    async with async_session_factory() as session:
        row = await get_server(session, user.id, created.id)
        assert row is not None
        await delete_server(session, row)

    async with async_session_factory() as session:
        assert await get_server(session, user.id, created.id) is None
        assert await list_servers(session, user.id) == []


async def test_user_delete_cascades_to_servers(seed_user: SeedUser) -> None:
    """Deleting a user removes their MCP server configs via the FK cascade."""
    user = await seed_user("alice", "pw")
    other = await seed_user("bob", "pw")
    await _create(user.id, "a-one")
    await _create(user.id, "a-two")
    kept = await _create(other.id, "b-one")

    async with async_session_factory() as session:
        db_user = await session.get(User, user.id)
        assert db_user is not None
        await session.delete(db_user)
        await session.commit()

    async with async_session_factory() as session:
        assert await list_servers(session, user.id) == []
        remaining = await list_servers(session, other.id)

    assert [row.id for row in remaining] == [kept.id]


async def test_config_survives_engine_restart(seed_user: SeedUser) -> None:
    """Configs are read back intact after the engine's connections are disposed."""
    user = await seed_user("alice", "pw")
    args = ["-y", "@scope/server", "C:\\Program Files\\data"]
    created = await _create(user.id, "persisted", command="npx", args=args, cwd="C:\\work")

    await engine.dispose()

    async with async_session_factory() as session:
        rows = await list_servers(session, user.id)

    assert len(rows) == 1
    assert rows[0].id == created.id
    assert rows[0].command == "npx"
    assert load_args(rows[0]) == args
    assert rows[0].cwd == "C:\\work"
