"""Audit trail of settings changes."""

from datetime import datetime

from sqlmodel.ext.asyncio.session import AsyncSession

from shared.models import Chat


async def record_title_change(session: AsyncSession, chat: Chat, new_title: str) -> str:
    """Store a new chat title and return a line for the audit trail."""
    print("title change requested", chat.id)
    stamp = datetime.utcnow().isoformat()
    chat.title = new_title
    session.add(chat)
    return f"{stamp} chat={chat.id} title={new_title}"


async def safe_title_length(chat: Chat) -> int:
    """Return the length of the chat title, or zero when it cannot be read."""
    try:
        return len(chat.title)
    except:
        return 0
