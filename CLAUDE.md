# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the app (starts UI on :8000, which auto-spawns the Agent subprocess on :8001)
python run.py

# Run the full test suite
pytest tests/ -v

# Run a single test file / test
pytest tests/test_context_engine.py -v
pytest tests/test_context_engine.py::test_sliding_window_preserves_database -v

# Install dependencies
pip install -r requirements.txt
```

There is no configured linter/formatter (no ruff/mypy/flake8 config) — don't assume one exists.

## Architecture

This is a **two-process, single-user, local-first** AI chat app. There is no Docker, no npm/Node, no message broker, and no auth — see Hard Constraints below.

### Process split
- **UI process** (`ui/`, port 8000, entry `run.py`): serves the static frontend (`ui/static/`) and owns an `AgentSupervisor` (`ui/supervisor.py`) that spawns, health-checks (`GET /health` every 3s), and auto-restarts the Agent as a subprocess (`asyncio.create_subprocess_exec`). It also kills orphaned processes still bound to ports 8000/8001 on startup (via `psutil`).
- **Agent process** (`agent/`, port 8001): the actual FastAPI app with the REST API, WebSocket endpoint, DB access, and LLM calls. It is launched by the supervisor, not run directly in normal operation — running `uvicorn agent.main:app` standalone skips orphan cleanup/health monitoring.
- The two processes only talk over HTTP/WebSocket (`shared.config.settings.AGENT_PORT`); there is no shared in-process state between them.
- `shared/` (`config.py`, `database.py`, `logger.py`, `models.py`) is imported by both processes and is the only code-sharing mechanism.

### Data model — message tree, not message list
`shared/models.py` defines `Chat`, `Message`, `Settings`, `TokenUsage`. Messages form a **tree** via `Message.parent_id`, not a flat log:
- `Chat.current_leaf_message_id` points at the active leaf; the visible conversation is the path from that leaf up to the root (walked in `agent/main.py::_build_tree_path`).
- "Branching" (`POST /api/v1/chats/{id}/branch`) just repoints `current_leaf_message_id` to a different message — it does not copy data. Regenerating/editing a message creates a sibling under the same `parent_id`.
- Deleting a chat must also clear related in-memory caches via `agent.state.cleanup_chat_caches` — see `test_cascade_delete.py`.

### Settings: global vs per-chat
`Settings.chat_id` is nullable: `NULL` = global defaults, non-null = per-chat override. `agent/main.py::_resolve_settings` always falls back to global when no per-chat row exists — any new settings-related code must preserve this fallback (see `test_settings_fallback.py`).

### Context compression strategies (`agent/context_engine.py`)
`Settings.strategy` (`ContextStrategy` enum) controls what gets sent to the LLM — **the database always keeps full history regardless of strategy**; compression only affects the outbound context window:
- `sliding` — last 10 messages only.
- `sticky` — old messages summarized into `Settings.summary_text` (English), re-summarized once the summary exceeds 1500 tokens.
- `truncate_middle` — first 5 + summarized middle + last 10.
- `no_compression` — sends everything; if total tokens exceed `Settings.context_length`, raises `ContextOverflowError`, deletes the just-submitted user message, and sends a `CONTEXT_OVERFLOW` WebSocket error instead of a reply. This is the one strategy where an overflow is a hard stop, not a compression trigger.

All strategies trigger at 75% of `context_length`. Token counting uses `tiktoken` (`cl100k_base`). Full behavioral spec: `docs/ARCHITECTURE.md`, `docs/API_SPEC.md`; scenario-level test expectations: `docs/TESTING_GUIDE.md`.

### LLM integration (`agent/llm_client.py`)
Two backends behind the same chat flow:
- **DeepSeek** (cloud) — OpenAI-compatible, SSE streaming.
- **LM Studio** (local, `http://localhost:1234`) — OpenAI-compatible `/v1/` for inference plus a separate control API (`/api/v0/`) for model load/unload. A `model_switch_lock` serializes concurrent load requests, with a 5s emergency-unload fallback if a load hangs. A `ConnectError` there means "LM Studio isn't running," not a generic HTTP failure — surface it as such (see `list_lm_studio_models` in `agent/main.py`).

### WebSocket chat flow (`agent/ws.py`)
Single endpoint `WS /ws/chat/{chat_id}` streams tokens and emits a final `done` message carrying live stats (`agent.context_engine.compute_chat_stats`): request/response token totals, current (strategy-dependent) context size, and usage %. These are also available via `GET /api/v1/chats/{chat_id}/stats` for polling.

## Hard constraints (never violate)

- No Docker, npm, Node.js, Redis, RabbitMQ, Celery.
- No `multiprocessing` or `os.fork` — the UI/Agent split uses `asyncio.create_subprocess_exec` only.
- No external identity providers, no RBAC/fine-grained roles. Simple multi-user auth was added in the "Week 3" milestone (see `.planning/PROJECT.md`): username/password login, every account has equal "admin" capability, session via HTTP-only cookie (never JWT/localStorage). All new data (memory, tasks, invariants, profiles) is scoped by `user_id`; only global project invariants remain shared across users.
- IPC between UI and Agent is FastAPI REST + WebSockets only, with in-memory state per process (no external cache/broker).
- Frontend is vanilla JS + CDN libraries only (Tailwind, Marked.js, DOMPurify) — no bundler, no npm packages.

## Code conventions

- Type hints everywhere; `async`/`await` for all I/O.
- `structlog` for logging (`shared/logger.py`) — never `print()` (the one exception is `run.py`'s startup banner, which is intentional CLI-facing output).
- `datetime.now(timezone.utc)`, never `datetime.utcnow()`.
- Import order: stdlib → third-party → local.
- SQLModel: use `sa_column=Column(ForeignKey(..., ondelete="CASCADE"))` for FK cascade behavior; **never** `Field(ondelete=...)` — it's silently ignored by SQLModel.
- Use `.is_(None)` instead of `== None` in SQLAlchemy `where()` clauses.
- Always `await session.commit()` after writes and `await session.rollback()` in exception handlers (see the try/except pattern in `agent/main.py::delete_chat` and `update_settings`).
- Avoid bare `except:`; never insert HTML without `DOMPurify.sanitize()` on the frontend; never hardcode secrets (use `.env`, loaded via `shared/config.py`); wrap `websocket.receive_json()` in `asyncio.wait_for` with a timeout.

## Testing

- `pytest` + `pytest-asyncio` (`asyncio_mode = auto`, see `pytest.ini`); HTTP mocking via `respx`.
- Tests use a separate DB from `app.db` — see `tests/conftest.py` for fixture setup.
- `docs/TESTING_GUIDE.md` enumerates the required scenario coverage per test file (e.g. `test_concurrent_ws.py` must send 5 parallel WS messages with no `IntegrityError`; `test_supervisor.py` must confirm agent crash-recovery within 5 seconds) — check it before adding tests for strategies, cascades, or the supervisor.

<!-- GSD:project-start source:PROJECT.md -->
## Project

**AiAdventAgentV2 — Week 3: Agent Memory & Task State**

A local-first, two-process AI chat application (FastAPI UI + Agent servers, vanilla JS frontend) that is being extended with an explicit agent memory model, personalization, and a formal task state machine with invariant enforcement. This is coursework for the "AI Advent Challenge" (9th cohort) — Week 3, Days 11-15 — where each day is a self-contained assignment building on the previous one's output, implemented in its own git branch and merged to `main` once its acceptance criteria pass.

**Core Value:** The agent must demonstrably separate and manage distinct kinds of state — short-term dialog, working task data, long-term profile/knowledge, and task lifecycle — making explicit, inspectable decisions about what goes where, rather than dumping everything into one undifferentiated context window.

### Constraints

- **Frontend**: Vanilla JS + CDN libraries only (Tailwind, Marked.js, DOMPurify) — no bundler, no npm packages. Any auth UI (login form, profile editor, memory/task panels) must follow this.
- **Process model**: No Docker, no `multiprocessing`/`os.fork`, no Redis/RabbitMQ/Celery — UI/Agent split stays `asyncio.create_subprocess_exec` only; IPC stays REST + WebSocket.
- **Auth mechanism**: HTTP-only session cookie (not JWT/localStorage) — matches local-first, single-deployment nature of the app and works uniformly for REST + WebSocket.
- **Data scope**: All new data (memory layers, profiles, tasks, per-chat invariants) is scoped by `user_id`; only global project invariants remain shared across users.
- **Branching**: One branch per phase, named after the day (`Day11`...`Day15`) except the auth foundation phase, which is named `Auth`. Branches are pushed and merged to `main`, never deleted.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3.x - Backend (API, database, LLM integration, logging, process management)
- JavaScript - Frontend (vanilla JS, no bundler, CDN-based)
- HTML/CSS - UI markup and styling (Tailwind CSS from CDN)
## Runtime
- Python standard runtime (asyncio for I/O, no multiprocessing/os.fork)
- pip
- Lockfile: Not detected (uses requirements.txt with pinned versions)
## Frameworks
- FastAPI 0.115.0+ - REST API and WebSocket server for both UI and Agent processes
- Uvicorn 0.30.0+ - ASGI application server (runs both `ui/main.py` and `agent/main.py`)
- SQLModel 0.0.22+ - ORM combining SQLAlchemy 2.x and Pydantic v2 for type-safe queries
- aiosqlite 0.20.0+ - Async SQLite driver (critical for concurrent database access)
- SQLAlchemy 2.x - Query engine (via SQLModel)
- pytest 8.3.0+ - Test runner
- pytest-asyncio 0.24.0+ - Async test support (asyncio_mode=auto in `pytest.ini`)
- respx 0.21.0+ - HTTP request mocking for LM Studio and DeepSeek API testing
- None configured (no linter, formatter, or type checker in codebase)
## Key Dependencies
- httpx 0.27.0+ - Async HTTP client for DeepSeek API and LM Studio control plane
- tiktoken 0.7.0+ - Token counting using `cl100k_base` encoding (OpenAI-compatible)
- Pydantic 2.9.0+ - Request/response validation and serialization (required by FastAPI and SQLModel)
- pydantic-settings 2.5.0+ - Environment variable loading from `.env` file
- structlog 24.4.0+ - Structured JSON logging to console/stdout
- psutil 6.0.0+ - Process introspection for orphan cleanup (`cleanup_port()` in `run.py`) and health monitoring
- Tailwind CSS (from `https://cdn.tailwindcss.com`) - Styling framework
- Marked.js (from `https://cdn.jsdelivr.net/npm/marked/`) - Markdown parsing for message rendering
- DOMPurify 3.1.6 (from `https://cdn.jsdelivr.net/npm/dompurify@3.1.6/`) - HTML sanitization to prevent XSS
## Configuration
- Loaded via `pydantic-settings` from `.env` file (see `shared/config.py`)
- No hardcoded secrets; all credentials come from environment
- `DEEPSEEK_API_KEY` - API key for DeepSeek cloud LLM
- `LM_STUDIO_BASE_URL` - Base URL for local LM Studio (`http://localhost:1234`)
- `UI_PORT` - Port for UI server (default: 8000)
- `AGENT_PORT` - Port for Agent server (default: 8001)
- `DB_PATH` - SQLite database file path (default: `app.db`)
- `LLM_TIMEOUT` - HTTP timeout for LLM API calls (default: 60.0 seconds)
- `pytest.ini` - Test configuration (asyncio_mode=auto, testpaths=tests)
- `.env.example` - Template for environment variables
## Platform Requirements
- Python 3.8+ (with asyncio support)
- pip for dependency installation
- SQLite3 (usually bundled with Python)
- Chrome browser (for UI interaction)
- Python 3.8+ runtime
- SQLite3
- Accessible DeepSeek API endpoint (cloud LLM) OR LM Studio running on localhost:1234 (local LLM)
- HTTP port 8000 (UI) and 8001 (Agent) available
## Database Schema
- WAL mode (`PRAGMA journal_mode=WAL`) - Write-Ahead Logging for concurrency
- Foreign key constraints (`PRAGMA foreign_keys=ON`) - Cascading deletes via SQLAlchemy
- Busy timeout (`PRAGMA busy_timeout=5000`) - 5-second wait on lock contention
- `chat` - Conversation trees with `current_leaf_message_id` pointer
- `message` - Tree structure via `parent_id`, stores content and token counts
- `settings` - Global defaults (chat_id=NULL) and per-chat overrides (chat_id=non-null)
- `token_usage` - Token tracking for request/response pairs (nullable, populated via LLM integration)
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- Python modules: `lowercase_with_underscores.py` (e.g., `llm_client.py`, `context_engine.py`, `ws.py`)
- Packages: lowercase directories (e.g., `agent/`, `shared/`, `ui/`, `tests/`)
- Test files: `test_<module_name>.py` (e.g., `test_cascade_delete.py`, `test_lm_studio_client.py`)
- Database: `app.db` (production), `test_app.db` (test)
- Regular functions: `lowercase_with_underscores` (e.g., `async def get_effective_settings()`, `def _validate_origin()`)
- Private helper functions: prefix with `_` (e.g., `_ensure_global_settings()`, `_parse_sse_stream()`, `_build_tree_path()`)
- Async functions: `async def function_name()` (all I/O-bound operations are async)
- Descriptive names with action verbs: `build_`, `create_`, `persist_`, `extract_`, `compute_`
- Local variables: `lowercase_with_underscores`
- Constants: `UPPER_CASE` (e.g., `LOAD_TIMEOUT`, `IDLE_TIMEOUT_SECONDS`, `RATE_LIMIT_MAX`)
- Private module-level variables: `_lowercase_with_underscores` (e.g., `_encoding`, `_base_url`)
- Type-hinted variables: all variable declarations include type annotations
- `PascalCase` for all classes (e.g., `HealthResponse`, `LMStudioClient`, `ContextOverflowError`)
- Data models inherit from `BaseModel` (Pydantic) or `SQLModel` (database models)
- Enums: `PascalCase` class name with `UPPER_CASE` values (e.g., `class ContextStrategy(str, Enum)` with `SLIDING_WINDOW = "sliding"`)
- Exception classes: `PascalCase` + `Error` suffix (e.g., `ContextOverflowError`)
- All modules that use logging declare `logger = get_logger(__name__)` after imports
- Example: `from shared.logger import get_logger` → `logger = get_logger(__name__)`
## Code Style
- No formatter configured (no Black, ruff, or autopep8)
- Hand-formatted code should follow PEP 8 conventions manually
- Line length: no hard limit, but keep lines readable (typically under 100 characters)
- Indentation: 4 spaces
- **Everywhere:** All function parameters and return types must have type hints
- Union types: Use `|` syntax (Python 3.10+) instead of `Union[A, B]` (e.g., `str | None`, `int | None`)
- Optional: Use `str | None` instead of `Optional[str]`
- Collections: Use built-in generics (e.g., `list[Message]`, `dict[str, Any]`, `set[str]`)
- Async generators: Use `AsyncGenerator[str, None]` from `typing` or `collections.abc`
- No linter configured (no ruff, flake8, or mypy)
- Code relies on manual review and test coverage
- Never use bare `except:` — always catch specific exceptions
- Use `.is_(None)` instead of `== None` in SQLAlchemy `where()` clauses
- Module docstring: single-line summary at the top of every file (e.g., `"""FastAPI agent application and REST API."""`)
- Function/method docstring: single-line or multi-line summary describing **what it does**
- Class docstring: single-line summary of the class purpose
- Format: Use triple-quoted strings (`"""..."""`), no type hints in docstring (already in signature)
- Example: 
## Import Organization
- Use parentheses for clarity when importing multiple items from one module:
- No path aliases configured (no @ aliases or absolute path rewriting)
- Imports are relative to project root: `from agent.main import app`, `from shared.models import Chat`
## Error Handling
- Use **specific exception catching**, never bare `except:`
- Wrap database operations in try/except/finally:
- Always rollback on exception, raise to propagate
- Use `finally` for cleanup (close resources, pop from state dictionaries)
- Raise `HTTPException(status_code=status.HTTP_NNN, detail="message")` from FastAPI endpoints
- Specific status codes: `HTTP_201_CREATED`, `HTTP_204_NO_CONTENT`, `HTTP_404_NOT_FOUND`, `HTTP_400_BAD_REQUEST`
- Return descriptive error messages in the `detail` field
- Define custom exceptions in relevant modules (e.g., `ContextOverflowError` in `agent/context_engine.py`)
- Use specific exception types to signal domain errors (overflow, timeout, connection errors)
- Catch and handle domain-specific exceptions, re-raise HTTP errors
- `httpx.TimeoutException` — LLM API timeouts, requires logging and fallback
- `httpx.ConnectError` — LM Studio unreachable, surface as "LM Studio is not running"
- `asyncio.TimeoutError` — WebSocket idle timeout, close connection gracefully
- `asyncio.CancelledError` — Task cancellation, clean up and propagate
- `WebSocketDisconnect` — Client disconnect, remove from active connections
- `ValidationError` — Pydantic validation, return error details in response
## Logging
- Every file: `logger = get_logger(__name__)`
- Logging levels: `logger.info()`, `logger.warning()`, `logger.error()`, `logger.debug()`
- Format: log message (string key) + key=value pairs:
- Message key format: `snake_case_action` (e.g., `agent_starting`, `ws_origin_check`, `strategy_selected`)
- Include context in pairs: `chat_id=`, `model_id=`, `error=`, `reason=`
- **INFO:** Application lifecycle (startup/shutdown), chat creation, settings changes, strategy selection
- **WARNING:** Recoverable issues (rate limit exceeded, broadcast failure, origin rejected)
- **ERROR:** Exceptions, context overflow, LLM failures, database errors
- **DEBUG:** Detailed operation traces (stream start, token counts, context sizes)
- Secrets, API keys, or sensitive user data
- Full exception tracebacks (log `str(exc)` for message only)
- Personal information or credentials
## Comments
- Avoid obvious comments; code should be self-documenting through clear naming
- Comment non-obvious logic: algorithm choices, concurrency guards, performance optimizations
- Comment workarounds and TODOs:
- Comment **why**, not **what** the code does
- Not used (Python, not TypeScript)
- Use module and function docstrings instead (triple-quoted strings)
## Function Design
- Helper functions: 5-15 lines (simple operations)
- Main functions: up to 50 lines (complex logic with multiple steps)
- Break up longer functions into private helpers with `_` prefix
- Use explicit positional parameters: `async def function(session: AsyncSession, chat_id: int, model: str)`
- Avoid `*args` and `**kwargs` unless forwarding to another function
- Use Query/Path parameters in FastAPI route handlers
- Type hints on all parameters (no defaults inferred)
- Explicit return type in signature (never omitted)
- Return early for guard clauses:
- Return structured responses (Pydantic models, dataclasses, dict with explicit keys)
- For async generators: return type `AsyncGenerator[str, None]`
- All I/O operations use `async`/`await`: database, HTTP, WebSocket
- Wrap long-running operations with `asyncio.wait_for(..., timeout=...)` for safety
- Use `asyncio.Lock()` for per-chat or per-resource synchronization (e.g., model-switch lock)
- Never use `asyncio.run()` inside async functions (already in async context)
## Module Design
- Import public functions/classes explicitly in `__init__.py` if exposing to other packages
- Or rely on direct imports (e.g., `from agent.context_engine import get_effective_settings`)
- Private functions start with `_` (not exported even if imported)
- Not used in this project
- Direct imports preferred for clarity (e.g., `from agent.main import app` not `from agent import app`)
- FastAPI uses `Depends()` for injecting session, query params
- Example: `async def endpoint(session: AsyncSession = Depends(get_session))`
- Database sessions passed explicitly, not global
- Minimal global state: `lm_studio_client` in `agent/main.py`, logger in each module
- Per-chat in-memory state stored in `agent/state.py` dictionaries (ws_rate_limiter, chat_locks, active_streams)
- State cleanup required on chat delete (see `agent/state.py::cleanup_chat_caches()`)
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## System Overview
```text
```
## Component Responsibilities
| Component | Responsibility | File |
|-----------|----------------|------|
| **UI Server** | Static file serving, agent process management | `ui/main.py` |
| **Agent Supervisor** | Agent subprocess lifecycle, health checks, orphan cleanup | `ui/supervisor.py` |
| **Agent Server** | REST API, WebSocket chat, LLM calls, context building | `agent/main.py`, `agent/ws.py` |
| **Context Engine** | Message tree loading, compression strategies, statistics | `agent/context_engine.py` |
| **LLM Client** | OpenAI-compatible streaming, token counting, model management | `agent/llm_client.py` |
| **Database** | Async SQLite, migrations, session management | `shared/database.py` |
| **Models** | SQLModel schemas for Chat, Message, Settings, TokenUsage | `shared/models.py` |
| **Frontend** | JavaScript SPA, WebSocket listener, UI state management | `ui/static/app.js` |
## Pattern Overview
- **Process isolation:** UI and Agent are separate Python processes, communicating only via HTTP/WebSocket
- **Message tree, not flat log:** Every message has a `parent_id`, enabling branching and non-linear conversations
- **Database-first history:** Full conversation history always persists; compression only affects LLM context window, never modifies stored messages
- **Per-chat settings with global fallback:** Settings can be overridden per-chat; `NULL` chat_id means global defaults
- **Pluggable LLM backends:** OpenAI-compatible API for DeepSeek (cloud) and LM Studio (local)
- **Async-first:** All I/O is async (asyncio, aiosqlite, httpx) with typed dependencies
- **No Docker, no npm, no external services:** Single SQLite DB, standard Python packages only
## Layers
- Purpose: User-facing chat UI, message rendering, tree branching controls
- Location: `ui/static/`
- Contains: HTML/CSS (Tailwind, Marked.js, DOMPurify), JavaScript state management
- Depends on: HTTP/WebSocket API exposed by Agent
- Used by: End user's browser
- Purpose: Static file serving, agent lifecycle management
- Location: `ui/main.py`
- Contains: File serving, CORS middleware, lifespan hooks
- Depends on: `ui/supervisor.py`, `shared/config.py`
- Used by: Browser (static files), supervisor owns Agent process
- Purpose: Monitor and restart the Agent subprocess
- Location: `ui/supervisor.py`
- Contains: Process spawning via `asyncio.create_subprocess_exec`, health polling, orphan cleanup
- Depends on: `psutil`, `httpx`, logging
- Used by: UI server's lifespan context manager
- Purpose: Main API and WebSocket endpoint
- Location: `agent/main.py`, `agent/ws.py`
- Contains: REST endpoints for chats/messages/settings, WebSocket chat streaming
- Depends on: `agent/context_engine.py`, `agent/llm_client.py`, `shared/database.py`
- Used by: Frontend (HTTP/WebSocket)
- Purpose: Build LLM context and calculate usage statistics
- Location: `agent/context_engine.py`
- Contains: Four compression strategies (sliding, sticky, truncate_middle, no_compression), fact extraction
- Depends on: SQLAlchemy queries, LLM token counting
- Used by: WebSocket handler during message streaming
- Purpose: Streaming completions and local model management
- Location: `agent/llm_client.py`
- Contains: `LLMClient` (streaming), `LMStudioClient` (control API)
- Depends on: `httpx`, `tiktoken`
- Used by: WebSocket handler, context engine (for facts extraction)
- **Database:** `shared/database.py` — async engine, session factory, migrations
- **Models:** `shared/models.py` — SQLModel schemas (Chat, Message, Settings, TokenUsage)
- **Config:** `shared/config.py` — environment-based settings (ports, API keys, LM Studio URL)
- **Logging:** `shared/logger.py` — structlog JSON output, bound loggers per module
- Purpose: Concurrency guards, rate limiting, active stream tracking
- Location: `agent/state.py`
- Contains: `active_streams`, `chat_locks`, `ws_rate_limiter`, cleanup function
- Used by: WebSocket handler, broadcast functions
## Data Flow
### Primary Request Path (User sends a message)
### Settings/Chat Management Flow
- GET `/api/v1/chats` → Query all chats ordered by `created_at DESC`
- POST `/api/v1/chats` with `{title}` → Insert `Chat`, return with `id`
- GET `/api/v1/chats/{chat_id}/tree` → Walk from `current_leaf_message_id` up to root via `parent_id`, reverse to root-first order
- POST `/api/v1/chats/{chat_id}/branch` with `{message_id}` → Repoint `chat.current_leaf_message_id` to message_id (no data copy)
- GET `/api/v1/settings?chat_id={id}` → Resolve per-chat or global; return effective settings
- PUT `/api/v1/settings` with `{chat_id?, system_prompt?, temperature?, ...}` → Create or update row, cascade to all future turns
- GET `/api/v1/lm-studio/models` → List available models
- POST `/api/v1/lm-studio/load-model` with `{model_id, gpu_offload}` → Load via control API
- POST `/api/v1/lm-studio/unload-model/{model_id}` → Unload
- `state.py::cleanup_chat_caches(chat_id)` called on chat deletion to remove in-memory locks, streams, rate-limit records
## Key Abstractions
- Purpose: Enable non-linear conversations (branching, re-editing, rewinding)
- Examples: `shared/models.py::Message`, `agent/main.py::_build_tree_path()`
- Pattern: Each message has `parent_id` (nullable). `Chat.current_leaf_message_id` marks the active tip. Walking parent links reconstructs the branch.
- Purpose: Manage LLM context window without modifying stored history
- Examples: `agent/context_engine.py::_apply_compression_strategy()`
- Pattern: Deterministic slicing of messages—no summarization, no database updates. Only affects outbound LLM calls.
- Purpose: Support per-chat customization while maintaining global defaults
- Examples: `agent/context_engine.py::get_effective_settings()`
- Pattern: Query `Settings` with `chat_id == <id>`; if not found, query with `chat_id.is_(None)` (global row).
- Purpose: Serialize message processing within a single chat (prevent race conditions on `current_leaf_message_id`)
- Examples: `agent/state.py::chat_locks`, `agent/ws.py::_handle_chat_message()` async with pattern
- Pattern: `dict[int, asyncio.Lock]` keyed by chat ID, auto-cleanup on chat deletion.
- Purpose: Detect Agent crashes and auto-restart
- Examples: `ui/supervisor.py::_healthcheck_loop()`, `_ping_health()`
- Pattern: Every 3 seconds (configurable), GET `/health`. On timeout/404, restart subprocess.
## Entry Points
- Location: `run.py`
- Triggers: `python run.py` from CLI
- Responsibilities:
- Location: `ui/main.py::app` (FastAPI instance)
- Entry via: `uvicorn ui.main:app` (wrapped in lifespan)
- Responsibilities:
- Location: `agent/main.py::app` (FastAPI instance)
- Entry via: `uvicorn agent.main:app` (spawned by supervisor, not run directly)
- Responsibilities:
- Location: `agent/main.py::websocket_chat_endpoint`, delegates to `agent/ws.py::ws_chat()`
- Route: `WS /ws/chat/{chat_id}`
- Flow: Accept → validate origin → message loop → handle_chat_message
## Architectural Constraints
- **Threading:** Single-threaded event loop per process (asyncio). No `multiprocessing` or `os.fork`. Agent is a separate OS process via `create_subprocess_exec`.
- **Global state per process:** In-memory dicts in `agent/state.py` (active_streams, chat_locks, ws_rate_limiter). Each process instance is independent; UI and Agent do not share memory.
- **SQLite single-writer:** Database uses WAL mode and 5s busy timeout. Multiple async tasks within Agent can query concurrently, but only one write at a time. Per-chat locks (`agent/state.py::chat_locks`) further serialize writes to a single chat's message tree.
- **Circular dependencies:** None detected. Import order: stdlib → third-party → local. `shared/` is imported by both processes and does not import from `ui/` or `agent/`.
- **Foreign key enforcement:** Enabled via SQLite pragma. `ondelete="CASCADE"` on Message.chat_id cleans up messages when chat is deleted; `ondelete="SET NULL"` on Chat.current_leaf_message_id allows safe deletion of a message.
## Anti-Patterns
### Bare `except:` blocks
```python
```
### Hardcoded secrets
### Modifying database history for compression
### Omitting `await session.commit()` or `await session.rollback()`
```python
```
### Duplicate `parent_id` logic
## Error Handling
## Cross-Cutting Concerns
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
