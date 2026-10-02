"""Opt-in live check: a title request through the DeepSeek provider (closes backlog 999.11)."""

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from agent import providers, titles
from shared.database import async_session_factory
from tests.conftest import _create_user

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DEEPSEEK") != "1",
    reason="live paid DeepSeek call; set RUN_LIVE_DEEPSEEK=1",
)

PLACEHOLDER_KEY = "your_deepseek_api_key_here"
USER_TEXT = "Как настроить WebSocket в FastAPI?"
ASSISTANT_TEXT = "Используйте декоратор @app.websocket и обрабатывайте WebSocketDisconnect."
TITLE_MAX_LEN = 50


async def test_live_deepseek_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """A title generated through the seeded DeepSeek provider is short and non-empty."""
    key = dotenv_values(Path(__file__).resolve().parent.parent / ".env").get("DEEPSEEK_API_KEY")
    if not key or key == PLACEHOLDER_KEY:
        pytest.skip("no real DEEPSEEK_API_KEY in the repository .env")
    monkeypatch.setenv("DEEPSEEK_API_KEY", key)

    user_id = await _create_user("live_title_user", "pw-live-12345")
    async with async_session_factory() as session:
        await providers.ensure_seeded(session, user_id)
        rows = await providers.list_providers(session, user_id)
    row = next((r for r in rows if r.name == providers.DEEPSEEK_PROVIDER_NAME), None)
    assert row is not None, "DeepSeek provider was not seeded"

    result = await providers.check_provider(row)
    assert result.status == "ok", f"{result.code}: {result.message}"

    model_ids = [m["id"] for m in result.models]
    model_id = "deepseek-chat" if "deepseek-chat" in model_ids else model_ids[0]
    print(f"live model id: {model_id}")

    title = await titles.request_title(
        USER_TEXT, ASSISTANT_TEXT, model_id, providers.build_client(row),
    )
    assert isinstance(title, str) and title
    assert len(title) <= TITLE_MAX_LEN
    assert title != USER_TEXT
    print(f"live title: {title}")
