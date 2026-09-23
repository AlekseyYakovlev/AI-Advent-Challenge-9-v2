"""User-scoped CRUD for MCP server configs."""

import json
from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from shared.logger import get_logger
from shared.models import McpServerConfig

logger = get_logger(__name__)

ENV_MASK: str = "•••"


def load_args(row: McpServerConfig) -> list[str]:
    """Return the stored launch args as a list of strings, or [] if the JSON is unusable."""
    try:
        parsed = json.loads(row.args_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def load_env(row: McpServerConfig) -> dict[str, str]:
    """Return the stored environment variables as a str->str dict, or {} if unusable."""
    try:
        parsed = json.loads(row.env_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


class EnvMaskError(ValueError):
    """A masked env value was submitted for a key that has no stored value."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Masked env value for unknown variable {key}")
        self.key: str = key


def merge_env(stored: dict[str, str], incoming: dict[str, str]) -> dict[str, str]:
    """Merge an edited env dict over the stored one.

    The UI shows secret values masked, so a masked value means "leave unchanged".
    Keys missing from `incoming` are dropped because the UI always sends the full key list.
    A masked value for a key that is not stored (e.g. a renamed variable) cannot mean
    "unchanged", so it raises EnvMaskError instead of silently losing the variable.
    """
    merged: dict[str, str] = {}
    for key, value in incoming.items():
        if value == ENV_MASK:
            if key not in stored:
                raise EnvMaskError(key)
            merged[key] = stored[key]
            continue
        merged[key] = value
    return merged


async def list_servers(session: AsyncSession, user_id: int) -> list[McpServerConfig]:
    """Return the user's MCP server configs ordered by id."""
    result = await session.exec(
        select(McpServerConfig)
        .where(McpServerConfig.user_id == user_id)
        .order_by(McpServerConfig.id),
    )
    return list(result.all())


async def get_server(
    session: AsyncSession,
    user_id: int,
    server_id: int,
) -> McpServerConfig | None:
    """Return the config, or None if missing or owned by another user."""
    row = await session.get(McpServerConfig, server_id)
    if row is None or row.user_id != user_id:
        return None
    return row


def _normalize_cwd(cwd: str | None) -> str | None:
    """Strip a working directory, mapping empty values to None."""
    if cwd is None:
        return None
    stripped = cwd.strip()
    return stripped or None


async def create_server(
    session: AsyncSession,
    user_id: int,
    name: str,
    command: str,
    args: list[str],
    env: dict[str, str],
    cwd: str | None,
    enabled: bool,
) -> McpServerConfig:
    """Persist a new MCP server config for the user; a masked env value raises EnvMaskError."""
    # Nothing is stored yet, so any masked value is unresolvable.
    validated_env: dict[str, str] = merge_env({}, env)
    row = McpServerConfig(
        user_id=user_id,
        name=name.strip(),
        command=command.strip(),
        args_json=json.dumps(args),
        env_json=json.dumps(validated_env),
        cwd=_normalize_cwd(cwd),
        enabled=enabled,
    )
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.info("mcp_server_created", user_id=user_id, server_id=row.id, name=row.name)
    return row


async def update_server(
    session: AsyncSession,
    row: McpServerConfig,
    name: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    cwd_set: bool = False,
    enabled: bool | None = None,
) -> McpServerConfig:
    """Apply the supplied fields to a config; `cwd_set` lets callers clear the cwd.

    Raises EnvMaskError before touching the row if `env` holds a mask for an unknown key.
    """
    # Validate first so a rejected request leaves the row unmodified.
    merged_env: dict[str, str] | None = None
    if env is not None:
        merged_env = merge_env(load_env(row), env)
    if name is not None:
        row.name = name.strip()
    if command is not None:
        row.command = command.strip()
    if args is not None:
        row.args_json = json.dumps(args)
    if merged_env is not None:
        row.env_json = json.dumps(merged_env)
    if cwd_set:
        row.cwd = _normalize_cwd(cwd)
    if enabled is not None:
        row.enabled = enabled
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    try:
        await session.commit()
        await session.refresh(row)
    except Exception:
        await session.rollback()
        raise
    logger.info("mcp_server_updated", user_id=row.user_id, server_id=row.id)
    return row


async def delete_server(session: AsyncSession, row: McpServerConfig) -> None:
    """Delete a config."""
    user_id: int = row.user_id
    server_id: int | None = row.id
    try:
        await session.delete(row)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    logger.info("mcp_server_deleted", user_id=user_id, server_id=server_id)
