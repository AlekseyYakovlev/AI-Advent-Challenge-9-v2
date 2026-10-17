"""REST routes of the per-user /support settings."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession

from agent import mcp_config
from agent.command_turn import load_help_config, stored_servers
from agent.dependencies import get_current_user, require_allowed_origin, require_json_content_type
from agent.rag import calibrated_threshold
from agent.support_command import DEFAULT_SUPPORT_SERVER_NAME, load_support_config
from shared.database import get_session
from shared.logger import get_logger
from shared.models import KnowledgeBase, SupportConfig, User

logger = get_logger(__name__)

router = APIRouter(tags=["support"])

_FLAG_FIELDS = (
    "lexical",
    "llm_rerank",
    "hybrid",
    "rewrite",
    "tools_memory",
    "tools_tasks",
    "tools_scheduler",
)


class SupportConfigIn(BaseModel):
    """Body of a /support settings update; an omitted field stays unchanged."""

    top_k: int | None = Field(default=None, ge=1, le=20)
    candidate_k: int | None = Field(default=None, ge=1, le=50)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    lexical: StrictBool | None = None
    llm_rerank: StrictBool | None = None
    hybrid: StrictBool | None = None
    rewrite: StrictBool | None = None
    tools_memory: StrictBool | None = None
    tools_tasks: StrictBool | None = None
    tools_scheduler: StrictBool | None = None
    mcp_server_ids: list[int] | None = Field(default=None, max_length=50)


class SupportConfigOut(BaseModel):
    """The /support settings with the read-only /help base and the resolved server choice."""

    top_k: int
    candidate_k: int
    threshold: float | None
    calibrated_threshold: float | None
    lexical: bool
    llm_rerank: bool
    hybrid: bool
    rewrite: bool
    mcp_server_ids: list[int]
    mcp_default: bool
    mcp_missing: list[str]
    tools_memory: bool
    tools_tasks: bool
    tools_scheduler: bool
    help_kb_id: int | None
    help_kb_name: str | None
    help_kb_status: str | None
    support_server_present: bool


async def _config_out(
    session: AsyncSession, user_id: int, row: SupportConfig
) -> SupportConfigOut:
    """Build the reply, showing the /help base only when the caller owns it."""
    help_config = await load_help_config(session, user_id)
    kb: KnowledgeBase | None = None
    if help_config.kb_id is not None:
        kb = await session.get(KnowledgeBase, help_config.kb_id)
        if kb is not None and kb.user_id != user_id:
            kb = None
    servers = await mcp_config.list_servers(session, user_id)
    existing = {server.id for server in servers}
    stored = stored_servers(row, "support")
    missing: list[str] = []
    if stored is None:
        server_ids = [s.id for s in servers if s.name == DEFAULT_SUPPORT_SERVER_NAME]
    else:
        server_ids = [entry["id"] for entry in stored if entry["id"] in existing]
        missing = [
            str(entry["name"] or entry["id"]) for entry in stored if entry["id"] not in existing
        ]
    return SupportConfigOut(
        top_k=row.top_k,
        candidate_k=row.candidate_k,
        threshold=row.threshold,
        calibrated_threshold=calibrated_threshold(kb.embedding_model) if kb is not None else None,
        lexical=bool(row.lexical),
        llm_rerank=bool(row.llm_rerank),
        hybrid=bool(row.hybrid),
        rewrite=bool(row.rewrite),
        mcp_server_ids=server_ids,
        mcp_default=stored is None,
        mcp_missing=missing,
        tools_memory=bool(row.tools_memory),
        tools_tasks=bool(row.tools_tasks),
        tools_scheduler=bool(row.tools_scheduler),
        help_kb_id=kb.id if kb is not None else None,
        help_kb_name=kb.name if kb is not None else None,
        help_kb_status=kb.status.value if kb is not None else None,
        support_server_present=any(s.name == DEFAULT_SUPPORT_SERVER_NAME for s in servers),
    )


def _apply_fields(row: SupportConfig, body: SupportConfigIn) -> list[str]:
    """Apply the sent scalar fields to the row and return the names that were set."""
    changed: list[str] = []
    if "threshold" in body.model_fields_set:
        row.threshold = body.threshold
        changed.append("threshold")
    if body.top_k is not None:
        row.top_k = body.top_k
        changed.append("top_k")
    if body.candidate_k is not None:
        row.candidate_k = body.candidate_k
        changed.append("candidate_k")
    for name in _FLAG_FIELDS:
        value = getattr(body, name)
        if value is not None:
            setattr(row, name, value)
            changed.append(name)
    row.candidate_k = max(row.candidate_k, row.top_k)
    return changed


@router.get("/api/v1/support/config")
async def get_support_config(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SupportConfigOut:
    """Return the caller's /support settings; defaults when never configured."""
    row = await load_support_config(session, current_user.id)
    return await _config_out(session, current_user.id, row)


@router.put(
    "/api/v1/support/config",
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def put_support_config(
    body: SupportConfigIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SupportConfigOut:
    """Store the search settings and tool choice of /support for the caller."""
    sent = body.model_fields_set
    chosen: list[dict[str, object]] | None = None
    if body.mcp_server_ids is not None:
        servers = {s.id: s for s in await mcp_config.list_servers(session, current_user.id)}
        for server_id in body.mcp_server_ids:
            if server_id not in servers:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, detail=f"MCP server {server_id} not found"
                )
        chosen = [
            {"id": server_id, "name": servers[server_id].name}
            for server_id in dict.fromkeys(body.mcp_server_ids)
        ]
    try:
        row = await session.get(SupportConfig, current_user.id)
        if row is None:
            row = SupportConfig(user_id=current_user.id)
            session.add(row)
        changed = _apply_fields(row, body)
        if "mcp_server_ids" in sent:
            row.mcp_servers_json = (
                json.dumps(chosen, ensure_ascii=False) if chosen is not None else None
            )
            changed.append("mcp_server_ids")
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(row)
    except SQLAlchemyError:
        await session.rollback()
        raise
    logger.info("support_config_updated", user_id=current_user.id, changed=changed)
    return await _config_out(session, current_user.id, row)
