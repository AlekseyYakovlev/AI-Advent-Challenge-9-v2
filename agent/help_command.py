"""The /help command: recognition, per-user settings, service notices and tool selection."""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from agent import mcp_config
from agent.mcp_tools import McpToolset, build_mcp_toolset
from agent.tools import TOOL_REGISTRY, build_tool_schemas
from shared.logger import get_logger
from shared.models import HelpConfig, KbStatus, KnowledgeBase

logger = get_logger(__name__)

NOTICE_FRAME_TYPE = "help_notice"
NOTICE_USAGE = "usage"
NOTICE_NO_BASE = "no_base"
DEFAULT_MCP_SERVER_NAME = "local-git"
DEFAULT_HELP_THRESHOLD: float = 0.45
DEFAULT_HELP_TOP_K = 5
DEFAULT_HELP_CANDIDATE_K = 20

TOOL_GROUPS: dict[str, tuple[str, ...]] = {
    "memory": ("save_working_memory", "save_long_term_memory"),
    "tasks": ("create_task", "transition_task", "pause_task", "resume_task"),
    "scheduler": ("schedule_task", "list_scheduled_tasks", "cancel_scheduled_task"),
}

HELP_USAGE_TEXT = (
    "Использование: /help <вопрос> — ответ по документации проекта и состоянию git. "
    "Пример: /help как запустить приложение?"
)
HELP_NO_BASE_TEXT = (
    "База документации не выбрана. Откройте «Настройки» → «Справка по проекту (/help)» "
    "и выберите готовую базу знаний с документацией проекта (README.md и docs/). "
    "Если база ещё индексируется, дождитесь статуса «готово»."
)
HELP_BASE_NOT_READY_TEXT = (
    "Выбранная база «{name}» ещё не готова. Дождитесь окончания индексации или выберите "
    "другую базу в «Настройки» → «Справка по проекту (/help)»."
)
HELP_MCP_WARNING_TEXT = "Сервер {name} недоступен, его инструменты не использовались."

_HELP_PATTERN = re.compile(r"^\s*/help(?:\s+(?P<q>.*))?$", re.IGNORECASE | re.DOTALL)


def parse_help(content: str) -> str | None:
    """Return the question of a /help message (empty when absent), or None for other text."""
    match = _HELP_PATTERN.match(content)
    if match is None:
        return None
    return (match.group("q") or "").strip()


def is_help_command(raw: Any) -> bool:
    """Tell whether a raw chat payload is a /help message."""
    if not isinstance(raw, dict):
        return False
    content = raw.get("content")
    return isinstance(content, str) and parse_help(content) is not None


def notice_frame(kind: str, text: str) -> dict[str, Any]:
    """Build the service notice frame; such a notice is never stored in the chat."""
    return {"type": NOTICE_FRAME_TYPE, "kind": kind, "text": text, "stored": False}


async def load_help_config(session: AsyncSession, user_id: int) -> HelpConfig:
    """Return the user's /help settings, or unsaved defaults when none are stored."""
    row = await session.get(HelpConfig, user_id)
    if row is None:
        return HelpConfig(user_id=user_id)
    return row


async def load_help_kb(
    session: AsyncSession, user_id: int, config: HelpConfig
) -> KnowledgeBase | None:
    """Return the marked base when it exists, is owned by the user and is ready."""
    if config.kb_id is None:
        return None
    kb = await session.get(KnowledgeBase, config.kb_id)
    if kb is None:
        return None
    if kb.user_id != user_id:
        logger.warning("help_kb_foreign", kb_id=config.kb_id, user_id=user_id)
        return None
    if kb.status != KbStatus.READY:
        return None
    return kb


async def resolve_help_notice(
    session: AsyncSession, user_id: int | None, question: str
) -> dict[str, Any] | None:
    """Return the notice frame for a /help turn that must not call the model, else None."""
    if not question:
        logger.info("help_notice_sent", kind=NOTICE_USAGE, user_id=user_id)
        return notice_frame(NOTICE_USAGE, HELP_USAGE_TEXT)
    text = HELP_NO_BASE_TEXT
    if user_id is not None:
        config = await load_help_config(session, user_id)
        if await load_help_kb(session, user_id, config) is not None:
            return None
        if config.kb_id is not None:
            marked = await session.get(KnowledgeBase, config.kb_id)
            if marked is not None and marked.user_id == user_id:
                text = HELP_BASE_NOT_READY_TEXT.format(name=marked.name)
    logger.info("help_notice_sent", kind=NOTICE_NO_BASE, user_id=user_id)
    return notice_frame(NOTICE_NO_BASE, text)


def stored_servers(config: HelpConfig) -> list[dict[str, Any]] | None:
    """Return the stored server choice, or None when the default applies."""
    raw = config.mcp_servers_json
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("help_servers_malformed", user_id=config.user_id)
        return None
    if not isinstance(data, list):
        logger.warning("help_servers_malformed", user_id=config.user_id)
        return None
    return [
        {"id": item.get("id"), "name": item.get("name")}
        for item in data
        if isinstance(item, dict) and isinstance(item.get("id"), int)
    ]


@dataclass
class HelpTools:
    """The tools of one /help turn and the warnings about chosen servers that are unavailable."""

    schemas: list[dict[str, Any]] = field(default_factory=list)
    toolset: McpToolset = field(default_factory=McpToolset.empty)
    allowed_tools: frozenset[str] = frozenset()
    warnings: list[dict[str, str]] = field(default_factory=list)


async def build_help_tools(
    session: AsyncSession, user_id: int, config: HelpConfig
) -> HelpTools:
    """Select the built-in groups and whole MCP servers the user chose for /help turns."""
    rows = await mcp_config.list_servers(session, user_id)
    by_id = {row.id: row for row in rows if row.id is not None}
    chosen_ids: list[int] = []
    missing_names: list[str] = []
    stored = stored_servers(config)
    if stored is None:
        chosen_ids = [row.id for row in rows if row.name == DEFAULT_MCP_SERVER_NAME and row.id]
        if not chosen_ids:
            missing_names.append(DEFAULT_MCP_SERVER_NAME)
    else:
        for entry in stored:
            if entry["id"] in by_id:
                chosen_ids.append(entry["id"])
            else:
                missing_names.append(str(entry["name"] or entry["id"]))

    toolset = McpToolset.empty()
    if chosen_ids:
        try:
            toolset = await build_mcp_toolset(
                session, user_id, set(TOOL_REGISTRY), only_server_ids=set(chosen_ids)
            )
        except Exception as exc:  # an MCP problem never breaks a /help turn
            logger.warning("help_mcp_toolset_failed", user_id=user_id, error_type=type(exc).__name__)
            toolset = McpToolset.empty()

    live_ids = {binding.server_id for binding in toolset.bindings.values()}
    names: list[str] = []
    for server_id in chosen_ids:
        if server_id not in live_ids:
            names.append(by_id[server_id].name)
    names = missing_names + names
    warnings: list[dict[str, str]] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        warnings.append({"server": name, "text": HELP_MCP_WARNING_TEXT.format(name=name)})

    allowed: set[str] = set()
    for group, enabled in (
        ("memory", config.tools_memory),
        ("tasks", config.tools_tasks),
        ("scheduler", config.tools_scheduler),
    ):
        if enabled:
            allowed.update(TOOL_GROUPS[group])
    schemas = [
        schema for schema in build_tool_schemas() if schema["function"]["name"] in allowed
    ] + list(toolset.schemas)
    return HelpTools(
        schemas=schemas,
        toolset=toolset,
        allowed_tools=frozenset(allowed),
        warnings=warnings,
    )
