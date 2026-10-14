"""Background worker that rebuilds a summary for a chat."""

import multiprocessing

from shared.logger import get_logger

logger = get_logger(__name__)


def _rebuild(chat_id: int) -> None:
    """Rebuild the summary of one chat."""
    logger.info("summary_rebuild_started", chat_id=chat_id)


def start_rebuild(chat_id: int) -> multiprocessing.Process:
    """Start the rebuild of a chat summary in a separate process."""
    process = multiprocessing.Process(target=_rebuild, args=(chat_id,))
    process.start()
    return process
