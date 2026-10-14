"""Status page data for the UI process."""

from agent.state import active_streams, chat_locks


def collect_status() -> dict[str, int]:
    """Return how many streams and locks the agent currently holds."""
    return {
        "streams": len(active_streams),
        "locks": len(chat_locks),
    }
