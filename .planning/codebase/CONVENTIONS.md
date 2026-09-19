# Coding Conventions

**Analysis Date:** 2026-09-19

## Naming Patterns

**Files:**
- Python modules: `lowercase_with_underscores.py` (e.g., `llm_client.py`, `context_engine.py`, `ws.py`)
- Packages: lowercase directories (e.g., `agent/`, `shared/`, `ui/`, `tests/`)
- Test files: `test_<module_name>.py` (e.g., `test_cascade_delete.py`, `test_lm_studio_client.py`)
- Database: `app.db` (production), `test_app.db` (test)

**Functions and Methods:**
- Regular functions: `lowercase_with_underscores` (e.g., `async def get_effective_settings()`, `def _validate_origin()`)
- Private helper functions: prefix with `_` (e.g., `_ensure_global_settings()`, `_parse_sse_stream()`, `_build_tree_path()`)
- Async functions: `async def function_name()` (all I/O-bound operations are async)
- Descriptive names with action verbs: `build_`, `create_`, `persist_`, `extract_`, `compute_`

**Variables:**
- Local variables: `lowercase_with_underscores`
- Constants: `UPPER_CASE` (e.g., `LOAD_TIMEOUT`, `IDLE_TIMEOUT_SECONDS`, `RATE_LIMIT_MAX`)
- Private module-level variables: `_lowercase_with_underscores` (e.g., `_encoding`, `_base_url`)
- Type-hinted variables: all variable declarations include type annotations

**Classes:**
- `PascalCase` for all classes (e.g., `HealthResponse`, `LMStudioClient`, `ContextOverflowError`)
- Data models inherit from `BaseModel` (Pydantic) or `SQLModel` (database models)
- Enums: `PascalCase` class name with `UPPER_CASE` values (e.g., `class ContextStrategy(str, Enum)` with `SLIDING_WINDOW = "sliding"`)
- Exception classes: `PascalCase` + `Error` suffix (e.g., `ContextOverflowError`)

**Module Logger:**
- All modules that use logging declare `logger = get_logger(__name__)` after imports
- Example: `from shared.logger import get_logger` → `logger = get_logger(__name__)`

## Code Style

**Formatting:**
- No formatter configured (no Black, ruff, or autopep8)
- Hand-formatted code should follow PEP 8 conventions manually
- Line length: no hard limit, but keep lines readable (typically under 100 characters)
- Indentation: 4 spaces

**Type Hints:**
- **Everywhere:** All function parameters and return types must have type hints
- Union types: Use `|` syntax (Python 3.10+) instead of `Union[A, B]` (e.g., `str | None`, `int | None`)
- Optional: Use `str | None` instead of `Optional[str]`
- Collections: Use built-in generics (e.g., `list[Message]`, `dict[str, Any]`, `set[str]`)
- Async generators: Use `AsyncGenerator[str, None]` from `typing` or `collections.abc`

**Linting:**
- No linter configured (no ruff, flake8, or mypy)
- Code relies on manual review and test coverage
- Never use bare `except:` — always catch specific exceptions
- Use `.is_(None)` instead of `== None` in SQLAlchemy `where()` clauses

**Docstrings:**
- Module docstring: single-line summary at the top of every file (e.g., `"""FastAPI agent application and REST API."""`)
- Function/method docstring: single-line or multi-line summary describing **what it does**
- Class docstring: single-line summary of the class purpose
- Format: Use triple-quoted strings (`"""..."""`), no type hints in docstring (already in signature)
- Example: 
  ```python
  async def build_llm_context(session: AsyncSession, chat_id: int) -> list[dict[str, str]]:
      """Build the message list for LLM consumption based on the active strategy."""
  ```

## Import Organization

**Order:**
1. Standard library (`import asyncio`, `from typing import Any`, `from datetime import datetime`)
2. Third-party (`import httpx`, `from fastapi import ...`, `from sqlmodel import ...`)
3. Local application (`from agent.schemas import ...`, `from shared.logger import ...`)

**Multi-line imports:**
- Use parentheses for clarity when importing multiple items from one module:
  ```python
  from agent.schemas import (
      BranchRequest,
      ChatCreate,
      ChatResponse,
      HealthResponse,
      MessageResponse,
  )
  ```

**Path aliases:**
- No path aliases configured (no @ aliases or absolute path rewriting)
- Imports are relative to project root: `from agent.main import app`, `from shared.models import Chat`

## Error Handling

**Pattern:**
- Use **specific exception catching**, never bare `except:`
- Wrap database operations in try/except/finally:
  ```python
  try:
      session.add(obj)
      await session.commit()
  except Exception:
      await session.rollback()
      raise
  ```
- Always rollback on exception, raise to propagate
- Use `finally` for cleanup (close resources, pop from state dictionaries)

**HTTP Errors:**
- Raise `HTTPException(status_code=status.HTTP_NNN, detail="message")` from FastAPI endpoints
- Specific status codes: `HTTP_201_CREATED`, `HTTP_204_NO_CONTENT`, `HTTP_404_NOT_FOUND`, `HTTP_400_BAD_REQUEST`
- Return descriptive error messages in the `detail` field

**Custom Exceptions:**
- Define custom exceptions in relevant modules (e.g., `ContextOverflowError` in `agent/context_engine.py`)
- Use specific exception types to signal domain errors (overflow, timeout, connection errors)
- Catch and handle domain-specific exceptions, re-raise HTTP errors

**Specific Exception Types:**
- `httpx.TimeoutException` — LLM API timeouts, requires logging and fallback
- `httpx.ConnectError` — LM Studio unreachable, surface as "LM Studio is not running"
- `asyncio.TimeoutError` — WebSocket idle timeout, close connection gracefully
- `asyncio.CancelledError` — Task cancellation, clean up and propagate
- `WebSocketDisconnect` — Client disconnect, remove from active connections
- `ValidationError` — Pydantic validation, return error details in response

## Logging

**Framework:** structlog (JSON-structured logging)

**Pattern:**
- Every file: `logger = get_logger(__name__)`
- Logging levels: `logger.info()`, `logger.warning()`, `logger.error()`, `logger.debug()`
- Format: log message (string key) + key=value pairs:
  ```python
  logger.info("chat_created", chat_id=chat.id, title=chat.title)
  logger.error("llm_stream_failed", chat_id=chat_id, error=str(exc))
  ```
- Message key format: `snake_case_action` (e.g., `agent_starting`, `ws_origin_check`, `strategy_selected`)
- Include context in pairs: `chat_id=`, `model_id=`, `error=`, `reason=`

**When to log:**
- **INFO:** Application lifecycle (startup/shutdown), chat creation, settings changes, strategy selection
- **WARNING:** Recoverable issues (rate limit exceeded, broadcast failure, origin rejected)
- **ERROR:** Exceptions, context overflow, LLM failures, database errors
- **DEBUG:** Detailed operation traces (stream start, token counts, context sizes)

**Never log:**
- Secrets, API keys, or sensitive user data
- Full exception tracebacks (log `str(exc)` for message only)
- Personal information or credentials

## Comments

**When to Comment:**
- Avoid obvious comments; code should be self-documenting through clear naming
- Comment non-obvious logic: algorithm choices, concurrency guards, performance optimizations
- Comment workarounds and TODOs:
  ```python
  # TODO: Cache this result when query patterns stabilize
  # FIXME: Handle edge case where strategy='no_compression' and context exceeds length
  ```
- Comment **why**, not **what** the code does

**JSDoc/TSDoc:**
- Not used (Python, not TypeScript)
- Use module and function docstrings instead (triple-quoted strings)

## Function Design

**Size:** Keep functions focused on a single responsibility
- Helper functions: 5-15 lines (simple operations)
- Main functions: up to 50 lines (complex logic with multiple steps)
- Break up longer functions into private helpers with `_` prefix

**Parameters:**
- Use explicit positional parameters: `async def function(session: AsyncSession, chat_id: int, model: str)`
- Avoid `*args` and `**kwargs` unless forwarding to another function
- Use Query/Path parameters in FastAPI route handlers
- Type hints on all parameters (no defaults inferred)

**Return Values:**
- Explicit return type in signature (never omitted)
- Return early for guard clauses:
  ```python
  if chat is None:
      raise HTTPException(status_code=404, detail="...")
      return  # guard — no more processing
  ```
- Return structured responses (Pydantic models, dataclasses, dict with explicit keys)
- For async generators: return type `AsyncGenerator[str, None]`

**Async/Await:**
- All I/O operations use `async`/`await`: database, HTTP, WebSocket
- Wrap long-running operations with `asyncio.wait_for(..., timeout=...)` for safety
- Use `asyncio.Lock()` for per-chat or per-resource synchronization (e.g., model-switch lock)
- Never use `asyncio.run()` inside async functions (already in async context)

## Module Design

**Exports:**
- Import public functions/classes explicitly in `__init__.py` if exposing to other packages
- Or rely on direct imports (e.g., `from agent.context_engine import get_effective_settings`)
- Private functions start with `_` (not exported even if imported)

**Barrel Files:**
- Not used in this project
- Direct imports preferred for clarity (e.g., `from agent.main import app` not `from agent import app`)

**Dependency Injection:**
- FastAPI uses `Depends()` for injecting session, query params
- Example: `async def endpoint(session: AsyncSession = Depends(get_session))`
- Database sessions passed explicitly, not global

**Module-Level State:**
- Minimal global state: `lm_studio_client` in `agent/main.py`, logger in each module
- Per-chat in-memory state stored in `agent/state.py` dictionaries (ws_rate_limiter, chat_locks, active_streams)
- State cleanup required on chat delete (see `agent/state.py::cleanup_chat_caches()`)

---

*Convention analysis: 2026-09-19*
