"""REST routes for manual control of a chat's task memory."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession

from agent import task_memory
from agent.dependencies import get_current_user, require_allowed_origin, require_json_content_type
from agent.rag_api import _get_owned_chat
from agent.schemas import TaskGoalUpdate, TaskStateOut
from agent.state import chat_locks
from shared.database import get_session
from shared.logger import get_logger
from shared.models import Chat, User

logger = get_logger(__name__)

router = APIRouter(tags=["task-memory"])

MSG_RAG_REQUIRED = "Память задачи доступна только в чатах с включённым RAG"
MSG_ITEM_NOT_FOUND = "Пункт памяти задачи не найден"


async def _get_rag_chat(session: AsyncSession, chat_id: int, user_id: int) -> Chat:
    """Load an owned chat (404 otherwise) and require RAG to be on (409 otherwise)."""
    chat = await _get_owned_chat(session, chat_id, user_id)
    if not await task_memory.chat_has_rag(session, chat_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MSG_RAG_REQUIRED)
    return chat


def _chat_lock(chat_id: int) -> asyncio.Lock:
    """Return the per-chat lock, creating it when missing."""
    if chat_id not in chat_locks:
        chat_locks[chat_id] = asyncio.Lock()
    return chat_locks[chat_id]


@router.put(
    "/api/v1/chats/{chat_id}/task-memory/goal",
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def put_task_goal(
    chat_id: int,
    body: TaskGoalUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskStateOut:
    """Replace the goal of the chat's task memory."""
    chat = await _get_rag_chat(session, chat_id, current_user.id)
    async with _chat_lock(chat_id):
        try:
            doc = task_memory.set_goal(await task_memory.load_doc(session, chat_id), body.goal)
            await task_memory.stage_doc(session, chat, doc)
            await session.commit()
        except SQLAlchemyError:
            await session.rollback()
            raise
    logger.info("task_memory_goal_edited", chat_id=chat_id, user_id=current_user.id)
    return TaskStateOut(**task_memory.task_state_dict(doc))


@router.delete(
    "/api/v1/chats/{chat_id}/task-memory/items/{item_id}",
    dependencies=[Depends(require_allowed_origin)],
)
async def delete_task_item(
    chat_id: int,
    item_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskStateOut:
    """Remove one clarified point or constraint by its id."""
    chat = await _get_rag_chat(session, chat_id, current_user.id)
    async with _chat_lock(chat_id):
        try:
            updated = task_memory.remove_item(await task_memory.load_doc(session, chat_id), item_id)
            if updated is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=MSG_ITEM_NOT_FOUND
                )
            await task_memory.stage_doc(session, chat, updated)
            await session.commit()
        except SQLAlchemyError:
            await session.rollback()
            raise
    logger.info("task_memory_item_deleted", chat_id=chat_id, user_id=current_user.id)
    return TaskStateOut(**task_memory.task_state_dict(updated))


@router.post(
    "/api/v1/chats/{chat_id}/task-memory/reset",
    dependencies=[Depends(require_allowed_origin), Depends(require_json_content_type)],
)
async def reset_task_memory(
    chat_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskStateOut:
    """Clear the chat's whole task memory."""
    await _get_rag_chat(session, chat_id, current_user.id)
    async with _chat_lock(chat_id):
        try:
            await task_memory.stage_clear(session, chat_id)
            await session.commit()
        except SQLAlchemyError:
            await session.rollback()
            raise
    logger.info("task_memory_reset", chat_id=chat_id, user_id=current_user.id)
    return TaskStateOut(**task_memory.task_state_dict(task_memory.TaskMemoryDoc()))
