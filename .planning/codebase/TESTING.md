# Testing Patterns

**Analysis Date:** 2026-09-19

## Test Framework

**Runner:**
- `pytest` (8.3.0+)
- `pytest-asyncio` (0.24.0+) for async test support
- Config: `pytest.ini` in project root

**pytest.ini Configuration:**
```ini
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
testpaths = tests
pythonpath = .
```

**Assertion Library:**
- pytest built-in assertions (`assert x == y`, `assert x is not None`)
- No external assertion library needed

**Run Commands:**
```bash
pytest tests/ -v              # Run all tests with verbose output
pytest tests/test_context_engine.py -v          # Run single test file
pytest tests/test_context_engine.py::test_get_effective_settings_falls_back_to_global -v  # Run single test
pytest -k "cascade" -v        # Run tests matching pattern name
```

## Test File Organization

**Location:**
- All tests in `tests/` directory at project root
- Not co-located with source code (separated structure)
- Mirror source module names with `test_` prefix (e.g., `agent/llm_client.py` → `tests/test_lm_studio_client.py`)

**Naming:**
- Test files: `test_<domain>.py` (e.g., `test_cascade_delete.py`, `test_context_engine.py`, `test_concurrent_ws.py`)
- Test functions: `test_<scenario_description>()` (e.g., `test_delete_chat_cleans_in_memory_caches`, `test_load_model_timeout_triggers_emergency_unload`)
- Test names should describe the scenario being tested, not just the function name

**File Structure Example:**
```
tests/
├── conftest.py           # Shared fixtures (clean_test_db, client)
├── test_cascade_delete.py
├── test_concurrent_ws.py
├── test_context_engine.py
├── test_lm_studio_client.py
├── test_settings_fallback.py
└── ... other test files
```

## Test Structure

**Suite Organization:**
Each test file begins with module docstring and uses the `async def` pattern:

```python
"""Chat deletion cascade and in-memory cache cleanup tests."""

import asyncio
import pytest
from httpx import AsyncClient
from agent.state import chat_locks, ws_rate_limiter
from shared.database import async_session_factory
from shared.models import Chat, Message, Settings, TokenUsage


@pytest.mark.asyncio
async def test_delete_chat_cleans_in_memory_caches(client: AsyncClient) -> None:
    """DELETE should remove ws_rate_limiter and chat_locks entries."""
    # Arrange: set up test data
    chat_resp = await client.post("/api/v1/chats", json={"title": "Delete me"})
    chat_id = chat_resp.json()["id"]
    
    # Act: perform the action
    ws_rate_limiter[chat_id] = [1.0, 2.0]
    chat_locks[chat_id] = asyncio.Lock()
    resp = await client.delete(f"/api/v1/chats/{chat_id}")
    
    # Assert: verify the result
    assert resp.status_code == 204
    assert chat_id not in ws_rate_limiter
    assert chat_id not in chat_locks
```

**Patterns:**
- Module docstring (triple-quoted, one-line or multi-line)
- Import order: stdlib → third-party → local (same as production code)
- Each test function: one scenario, clear name
- Decorator: `@pytest.mark.asyncio` for async tests
- Docstring: one-line description of **what should happen**
- Three-part structure: Arrange → Act → Assert (implicit sections, no comments needed)
- Fixtures injected as parameters (e.g., `client: AsyncClient`, `lm_client: LMStudioClient`)

## Mocking

**Framework:** `respx` (HTTP mocking for httpx)

**Pattern:**
```python
@respx.mock
@pytest.mark.asyncio
async def test_load_model_success(lm_client: LMStudioClient) -> None:
    """Successful load returns LOADED status."""
    load_route = respx.post(f"{BASE_URL}/api/v0/models/load").mock(
        return_value=httpx.Response(200, json={"success": True}),
    )
    
    result = await lm_client.load_model("test-model", gpu_offload=0, context_length=4096)
    
    assert result.status == ModelLoadStatus.LOADED
    assert load_route.called
    request = load_route.calls.last.request
    assert json.loads(request.content) == {
        "model": "test-model",
        "gpu_offload": 0,
        "context_length": 4096,
    }
```

**Decorator Usage:**
- `@respx.mock` enables request mocking for the test function
- Stack order: `@respx.mock` then `@pytest.mark.asyncio`

**Mock Setup:**
- `respx.post(url).mock(return_value=httpx.Response(...))` — successful response
- `respx.post(url).mock(side_effect=httpx.TimeoutException(...))` — exception
- `respx.post(url).mock(side_effect=lambda _request: _stream_response(...))` — dynamic response

**Mock Verification:**
- `assert load_route.called` — verify the mock was invoked
- `load_route.calls.last.request` — inspect request sent to mock
- `json.loads(request.content)` — parse request body

**What to Mock:**
- External HTTP APIs (DeepSeek, LM Studio)
- Third-party services (assumed unreachable in tests)
- Long-running operations (model loads with timeouts)

**What NOT to Mock:**
- SQLite database (use real in-memory or test database)
- FastAPI HTTP client (use AsyncClient transport)
- WebSocket connections (use TestClient for sync tests)
- Local functions and classes (test their actual behavior)

## Fixtures and Factories

**Test Data Setup:**

Direct in-test creation:
```python
@pytest.mark.asyncio
async def test_get_effective_settings_falls_back_to_global() -> None:
    async with async_session_factory() as session:
        chat = Chat(title="Context chat")
        session.add(chat)
        await session.commit()
        await session.refresh(chat)
        
        global_row = Settings(chat_id=None, system_prompt="Global", temperature=0.4)
        session.add(global_row)
        await session.commit()
        
        effective = await get_effective_settings(session, chat.id)
        assert effective.system_prompt == "Global"
```

**Shared Fixtures:**

Location: `tests/conftest.py`
```python
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
```

**Fixture Scope:**
- `autouse=True` — runs before every test automatically (e.g., `clean_test_db` for database cleanup)
- Default scope: `function` (fresh for each test)
- Yield pattern: setup before `yield`, cleanup after

**Test Fixtures (Module-Level):**
```python
@pytest.fixture
def lm_client() -> LMStudioClient:
    """Fresh LM Studio client for each test."""
    return LMStudioClient(base_url=BASE_URL)
```

**Module-Level Cleanup:**
```python
@pytest.fixture(autouse=True)
def reset_debounce_state() -> None:
    """Clear module-level debounce state between tests."""
    _pending_messages.clear()
    for task in _debounce_tasks.values():
        if not task.done():
            task.cancel()
    _debounce_tasks.clear()
```

## Coverage

**Requirements:** Not enforced (no pytest-cov configuration)

**View Coverage:**
```bash
pip install pytest-cov
pytest tests/ --cov=agent --cov=shared --cov-report=html
# Open htmlcov/index.html in browser
```

**Target Coverage:** Aim for >80% coverage on critical paths
- Data models and schemas: 100%
- Business logic (context engine, LLM client): >85%
- API endpoints: >80%
- Error handling paths: >75%

## Test Types

**Unit Tests:**
- Scope: Single function or class in isolation
- Examples: `test_count_tokens()`, `test_parse_facts_json()`, `test_normalize_strategy()`
- Setup: Create test data directly, use mocks for dependencies
- Assertions: Return values, side effects, exception raising
- Located in: `tests/test_*.py` for individual modules

**Integration Tests:**
- Scope: Multiple components working together (e.g., API + database + settings)
- Examples: `test_get_effective_settings_falls_back_to_global()`, `test_per_chat_settings_override_global()`
- Setup: Create real database, use real AsyncClient to FastAPI app
- Assertions: Database state changes, HTTP response status and data
- Located in: `tests/test_*.py` (same files as unit tests, often end-to-end)

**E2E/Concurrent Tests:**
- Scope: Full request-response cycle under realistic conditions
- Examples: `test_five_parallel_ws_messages_no_integrity_error()` (ThreadPoolExecutor for concurrency)
- Setup: Real database, real WebSocket connections, real app
- Assertions: No database integrity errors, message counts correct, state consistency
- Located in: `tests/test_concurrent_ws.py`

## Common Patterns

**Async Testing:**
```python
@pytest.mark.asyncio
async def test_async_operation() -> None:
    """Test an async function."""
    result = await some_async_function()
    assert result is not None
```

**Async with Database Session:**
```python
@pytest.mark.asyncio
async def test_database_operation() -> None:
    async with async_session_factory() as session:
        # Create test data
        obj = Chat(title="Test")
        session.add(obj)
        await session.commit()
        await session.refresh(obj)
        
        # Use it
        assert obj.id is not None
```

**Error Testing:**
```python
@respx.mock
@pytest.mark.asyncio
async def test_load_model_timeout_triggers_emergency_unload() -> None:
    """Load timeout triggers emergency unload."""
    respx.post(f"{BASE_URL}/api/v0/models/load").mock(
        side_effect=httpx.TimeoutException("load timed out"),
    )
    emergency_route = respx.post(f"{BASE_URL}/api/v0/models/unload").mock(
        return_value=httpx.Response(200, json={"success": True}),
    )
    
    result = await lm_client.load_model("slow-model", gpu_offload=0, context_length=None)
    
    assert result.status == ModelLoadStatus.ERROR
    assert "timed out" in result.message
    assert emergency_route.called
```

**Concurrent Operations:**
```python
@pytest.mark.asyncio
async def test_concurrent_messages() -> None:
    """Test that concurrent operations don't cause integrity errors."""
    with TestClient(app) as client:
        chat_resp = client.post("/api/v1/chats", json={"title": "Concurrent"})
        chat_id = chat_resp.json()["id"]
        
        def send_message(index: int) -> None:
            with client.websocket_connect(f"/ws/chat/{chat_id}") as ws:
                ws.send_json({"content": f"message {index}", "model": MODEL})
                # read until done
                while True:
                    payload = ws.receive_json()
                    if payload.get("type") == "done":
                        break
        
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(send_message, i) for i in range(5)]
            for future in as_completed(futures):
                future.result()  # Raise any exception
```

**Debouncing/Async Timing:**
```python
@pytest.mark.asyncio
async def test_debounce_and_merge() -> None:
    """Test that operations debounce correctly."""
    # Trigger multiple times
    for i in range(3):
        trigger_operation()
    
    # Wait for debounce window
    await asyncio.sleep(DEBOUNCE_SECONDS + 0.2)
    
    # Verify final state
    result = await session.exec(select(Model))
    assert len(result.all()) == 1  # Only one actually processed
```

## Test Environment

**Database:**
- Separate test database: `test_app.db` (controlled via `settings.DB_PATH`)
- Auto-cleaned: `clean_test_db` fixture recreates before and removes after each test
- Schema: Same as production, created via `await init_db()`

**Environment Variables:**
- Set in `tests/conftest.py`: `os.environ.setdefault("DB_PATH", "test_app.db")`
- Imported after env setup: `from agent.main import app`
- Do not commit `.env` with secrets; tests use defaults

**Port Isolation:**
- No live server ports used (AsyncClient with ASGITransport)
- WebSocket tests use TestClient (sync wrapper around async app)
- No port conflicts, safe to run tests in parallel

---

*Testing analysis: 2026-09-19*
