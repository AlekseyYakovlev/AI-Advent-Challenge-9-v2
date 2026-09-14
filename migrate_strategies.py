"""Migrate old strategy values to new enum."""
import asyncio

from sqlalchemy import text

from shared.database import async_session_factory
from shared.models import ContextStrategy


async def migrate() -> None:
    """Migrate 'branching' strategy to 'sliding' for all existing settings."""
    async with async_session_factory() as session:
        count_result = await session.execute(
            text("SELECT COUNT(*) FROM settings WHERE strategy = 'branching'"),
        )
        pending = count_result.scalar_one()

        if pending > 0:
            await session.execute(
                text(
                    "UPDATE settings SET strategy = :sliding "
                    "WHERE strategy = 'branching'"
                ),
                {"sliding": ContextStrategy.SLIDING_WINDOW.value},
            )
            await session.commit()
            print(
                f"Migrated {pending} settings records from 'branching' to 'sliding'",
            )
        else:
            print("No migration needed")


if __name__ == "__main__":
    asyncio.run(migrate())
