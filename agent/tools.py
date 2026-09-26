"""Tool-call registry and strictly sequential dispatcher."""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlmodel.ext.asyncio.session import AsyncSession

from agent import memory, tasks
from agent.mcp_tools import McpToolBinding, call_mcp_tool
from agent.schemas import (
    CreateTaskArgs,
    PauseTaskArgs,
    ResumeTaskArgs,
    SaveLongTermMemoryArgs,
    SaveWorkingMemoryArgs,
    TransitionTaskArgs,
)
from shared.logger import get_logger
from shared.models import TaskState

logger = get_logger(__name__)

ToolHandler = Callable[[AsyncSession, int, int, dict[str, Any]], Awaitable[dict[str, Any]]]

TOOL_REGISTRY: dict[str, ToolHandler] = {}
TOOL_SCHEMAS: dict[str, type[BaseModel]] = {}
TOOL_DESCRIPTIONS: dict[str, str] = {}

_SCOPE_KEYS = ("chat_id", "user_id")


def register_tool(
    name: str,
    args_model: type[BaseModel],
    description: str,
) -> Callable[[ToolHandler], ToolHandler]:
    """Decorator that records a handler, its Pydantic argument model, and its description."""

    def _wrap(fn: ToolHandler) -> ToolHandler:
        TOOL_REGISTRY[name] = fn
        TOOL_SCHEMAS[name] = args_model
        TOOL_DESCRIPTIONS[name] = description
        return fn

    return _wrap


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip Pydantic's `title` keys so the model sees a clean JSON Schema."""
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        if isinstance(prop, dict):
            prop.pop("title", None)
    return schema


def build_tool_schemas() -> list[dict[str, Any]]:
    """Build the OpenAI `tools=[...]` schema list from the registered Pydantic models."""
    schemas: list[dict[str, Any]] = []
    for name, args_model in TOOL_SCHEMAS.items():
        parameters = _clean_schema(args_model.model_json_schema())
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": TOOL_DESCRIPTIONS[name],
                    "parameters": parameters,
                },
            },
        )
    return schemas


async def dispatch_tool_calls(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    tool_calls: list[dict[str, Any]],
    *,
    mcp_bindings: dict[str, McpToolBinding] | None = None,
    allowed_tools: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Execute tool calls strictly sequentially, in the order returned by the LLM.

    Never gather these concurrently and never open a new session or lock: the
    caller already holds the per-chat lock and the open session, and a later
    call in a turn may depend on an earlier one. Names found in `mcp_bindings`
    are routed to the user's live MCP session; everything else uses the built-ins.
    allowed_tools, when given, restricts built-in tools to that set (headless
    scheduler runs, D-04); MCP bindings are unaffected.
    """
    results: list[dict[str, Any]] = []
    for call in tool_calls:
        name = call.get("function", {}).get("name")
        tool_call_id = call.get("id")
        raw_arguments = call.get("function", {}).get("arguments") or ""

        binding = (mcp_bindings or {}).get(name) if isinstance(name, str) else None
        if binding is not None:
            results.append(await _dispatch_mcp_call(binding, tool_call_id, raw_arguments))
            continue

        try:
            raw_args = json.loads(raw_arguments)
        except json.JSONDecodeError:
            results.append(
                {
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "ok": False,
                    "content": json.dumps({"error": "malformed arguments"}),
                    "write": None,
                    "arguments": raw_arguments,
                    "mcp": None,
                },
            )
            continue

        if name not in TOOL_REGISTRY or (allowed_tools is not None and name not in allowed_tools):
            if name in TOOL_REGISTRY:
                logger.warning("tool_call_not_allowed", tool=name)
            results.append(
                {
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "ok": False,
                    "content": json.dumps({"error": f"unknown tool {name}"}),
                    "write": None,
                    "arguments": raw_arguments,
                    "mcp": None,
                },
            )
            continue

        args_model = TOOL_SCHEMAS[name]
        try:
            validated = args_model.model_validate(raw_args)
        except ValidationError as exc:
            results.append(
                {
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "ok": False,
                    "content": json.dumps({"error": str(exc)}),
                    "write": None,
                    "arguments": raw_arguments,
                    "mcp": None,
                },
            )
            continue

        validated_args = validated.model_dump()
        for scope_key in _SCOPE_KEYS:
            if scope_key in raw_args:
                logger.warning(
                    "tool_args_scope_override_ignored",
                    tool=name,
                    tool_call_id=tool_call_id,
                )

        result = await TOOL_REGISTRY[name](session, user_id, chat_id, validated_args)
        logger.info(
            "tool_call_dispatched",
            tool=name,
            tool_call_id=tool_call_id,
            chat_id=chat_id,
        )
        ok = result.get("status") != "error"
        results.append(
            {
                "tool_call_id": tool_call_id,
                "name": name,
                "ok": ok,
                "content": json.dumps(result),
                "write": (
                    None
                    if not ok
                    else {
                        "id": result.get("id"),
                        "key": result.get("key"),
                        "layer": result.get("layer"),
                    }
                ),
                "arguments": raw_arguments,
                "mcp": None,
            },
        )
    return results


async def _dispatch_mcp_call(
    binding: McpToolBinding,
    tool_call_id: str | None,
    raw_arguments: str,
) -> dict[str, Any]:
    """Run one namespaced MCP call and shape its outcome like a built-in tool result."""
    mcp_info = {
        "server_id": binding.server_id,
        "server_name": binding.server_name,
        "tool": binding.tool_name,
    }

    def _failure(text: str) -> dict[str, Any]:
        return {
            "tool_call_id": tool_call_id,
            "name": binding.exposed_name,
            "ok": False,
            "content": json.dumps(
                {
                    "server": binding.server_name,
                    "tool": binding.tool_name,
                    "is_error": True,
                    "error": text,
                },
                ensure_ascii=False,
            ),
            "write": None,
            "arguments": raw_arguments,
            "mcp": mcp_info,
            "result_text": text,
            "truncated": False,
        }

    if raw_arguments.strip():
        try:
            parsed: Any = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return _failure("malformed arguments")
        if not isinstance(parsed, dict):
            return _failure("arguments must be a JSON object")
        arguments: dict[str, Any] = parsed
    else:
        arguments = {}

    outcome = await call_mcp_tool(binding, arguments)
    if not outcome["ok"]:
        failure = _failure(outcome["text"])
        failure["truncated"] = outcome["truncated"]
        return failure

    return {
        "tool_call_id": tool_call_id,
        "name": binding.exposed_name,
        "ok": True,
        "content": json.dumps(
            {
                "server": binding.server_name,
                "tool": binding.tool_name,
                "is_error": False,
                "content": outcome["text"],
                "truncated": outcome["truncated"],
            },
            ensure_ascii=False,
        ),
        "write": None,
        "arguments": raw_arguments,
        "mcp": mcp_info,
        "result_text": outcome["text"],
        "truncated": outcome["truncated"],
    }


@register_tool(
    "save_working_memory",
    SaveWorkingMemoryArgs,
    "Save a value to the temporary scratchpad for the CURRENT chat's task data. "
    "Overwritten per key; does not persist to other chats.",
)
async def _save_working_memory(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Write a working-memory row scoped to the current chat."""
    row = await memory.save_working_memory(session, user_id, chat_id, args["key"], args["content"])
    return {"status": "saved", "layer": "working", "key": row.key, "id": row.id}


@register_tool(
    "save_long_term_memory",
    SaveLongTermMemoryArgs,
    "Save a durable fact, decision, or preference about the user that should "
    "persist across ALL of the user's chats.",
)
async def _save_long_term_memory(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Write a long-term-memory row scoped to the user (cross-chat, D-02)."""
    row = await memory.save_long_term_memory(session, user_id, args["key"], args["content"])
    return {"status": "saved", "layer": "long_term", "key": row.key, "id": row.id}


@register_tool(
    "create_task",
    CreateTaskArgs,
    "Create a new task when you recognize a distinct, trackable unit of work in this chat. "
    "The task starts in the 'planning' state. Do NOT use this tool for state changes on an "
    "existing task.",
)
async def _create_task(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Create a task scoped to the current chat and user."""
    row = await tasks.create_task(
        session, user_id, chat_id, args["title"], args["description"], args["goal"],
    )
    return {"status": "created", "id": row.id, "title": row.title, "state": row.state.value}


@register_tool(
    "transition_task",
    TransitionTaskArgs,
    "Move an EXISTING task to a new lifecycle state. Requires the explicit numeric task_id "
    "of a task in this chat -- there is no implicit 'current task'. Valid states are "
    "planning, execution, validation, done. Cannot be used to cancel a task -- cancellation "
    "is a manual user action.",
)
async def _transition_task(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Transition a task owned by this chat/user; returns an error dict otherwise."""
    try:
        row = await tasks.transition_task(
            session,
            user_id,
            chat_id,
            args["task_id"],
            TaskState(args["new_state"]),
            args.get("note", ""),
        )
    except tasks.TaskNotFoundError:
        return {
            "status": "error",
            "code": "not_found",
            "error": f"task {args['task_id']} not found in this chat",
        }
    except tasks.IllegalTransitionError as exc:
        return {
            "status": "error",
            "code": "illegal_transition",
            "error": str(exc),
            "task_id": exc.task_id,
            "from_state": exc.from_state.value,
            "to_state": exc.to_state.value,
        }
    return {"status": "transitioned", "id": row.id, "title": row.title, "state": row.state.value}


@register_tool(
    "pause_task",
    PauseTaskArgs,
    "Pause an existing task in this chat so it can be resumed later. Requires the explicit "
    "numeric task_id of a task in this chat -- there is no implicit 'current task'. Pausing "
    "does NOT change the task's lifecycle state.",
)
async def _pause_task(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Pause a task owned by this chat/user; returns an error dict otherwise."""
    try:
        row = await tasks.set_paused(session, user_id, chat_id, args["task_id"], True)
    except tasks.TaskNotFoundError:
        return {
            "status": "error",
            "code": "not_found",
            "error": f"task {args['task_id']} not found in this chat",
        }
    except tasks.IllegalTransitionError as exc:
        return {
            "status": "error",
            "code": "illegal_transition",
            "error": str(exc),
            "task_id": exc.task_id,
            "from_state": exc.from_state.value,
            "to_state": exc.to_state.value,
        }
    return {
        "status": "paused",
        "id": row.id,
        "title": row.title,
        "state": row.state.value,
        "is_paused": row.is_paused,
    }


@register_tool(
    "resume_task",
    ResumeTaskArgs,
    "Resume a previously paused task in this chat. Requires the explicit numeric task_id "
    "of a task in this chat -- there is no implicit 'current task'.",
)
async def _resume_task(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Resume a task owned by this chat/user; returns an error dict otherwise."""
    try:
        row = await tasks.set_paused(session, user_id, chat_id, args["task_id"], False)
    except tasks.TaskNotFoundError:
        return {
            "status": "error",
            "code": "not_found",
            "error": f"task {args['task_id']} not found in this chat",
        }
    except tasks.IllegalTransitionError as exc:
        return {
            "status": "error",
            "code": "illegal_transition",
            "error": str(exc),
            "task_id": exc.task_id,
            "from_state": exc.from_state.value,
            "to_state": exc.to_state.value,
        }
    return {
        "status": "resumed",
        "id": row.id,
        "title": row.title,
        "state": row.state.value,
        "is_paused": row.is_paused,
    }
