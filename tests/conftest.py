"""Shared pytest fixtures and test environment setup."""

import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DB_PATH", "test_app.db")

from agent.main import app
from shared.config import settings
from shared.database import engine, init_db


@pytest.fixture(autouse=True)
async def clean_test_db() -> None:
    """Remove and recreate the test database before each test."""
    db_path = Path(settings.DB_PATH)
    if db_path.exists():
        db_path.unlink()
    await init_db()
    yield
    await engine.dispose()
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
async def client() -> AsyncClient:
    """Async HTTP client wired to the Agent FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
