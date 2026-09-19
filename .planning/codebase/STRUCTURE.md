# Codebase Structure

**Analysis Date:** 2026-09-19

## Directory Layout

```
AiAdventAgentV2/
├── agent/                  # Agent server process (FastAPI, LLM integration)
│   ├── __init__.py
│   ├── main.py             # FastAPI app, REST endpoints, lifespan
│   ├── ws.py               # WebSocket chat endpoint, message streaming
│   ├── context_engine.py   # Context building, compression strategies, stats
│   ├── llm_client.py       # OpenAI-compatible streaming, model management
│   ├── schemas.py          # Pydantic request/response models
│   └── state.py            # In-memory state (locks, streams, rate limits)
│
├── ui/                     # UI server process and frontend
│   ├── __init__.py
│   ├── main.py             # FastAPI app, static file serving
│   ├── supervisor.py       # Agent subprocess lifecycle management
│   └── static/             # Vanilla JavaScript frontend
│       ├── index.html      # HTML template + styling
│       └── app.js          # Client-side SPA logic
│
├── shared/                 # Cross-process shared code
│   ├── __init__.py
│   ├── config.py           # Environment configuration (ports, API keys)
│   ├── database.py         # Async SQLite engine, session factory, migrations
│   ├── logger.py           # Structured logging setup (structlog)
│   └── models.py           # SQLModel schemas (Chat, Message, Settings, TokenUsage)
│
├── tests/                  # Test suite
│   ├── conftest.py         # pytest fixtures, test DB setup
│   ├── test_context_engine.py          # Compression strategies, stats
│   ├── test_concurrent_ws.py           # Parallel WebSocket messages
│   ├── test_cascade_delete.py          # Chat deletion cascade
│   ├── test_settings_fallback.py       # Global/per-chat settings resolution
│   ├── test_strategies.py              # Individual compression strategies
│   ├── test_supervisor.py              # Agent restart on crash
│   ├── test_orphan_cleanup.py          # Port cleanup logic
│   ├── test_cors.py                    # CORS middleware
│   ├── test_database.py                # DB operations
│   ├── test_lm_studio_client.py        # Model loading
│   ├── test_model_switch_lock.py       # Concurrent model switches
│   ├── test_stats.py                   # Statistics calculation
│   ├── test_websocket_cors.py          # WebSocket CORS
│   ├── test_ws_origin_validation.py    # Origin header validation
│   ├── test_ws_security.py             # WebSocket security
│   └── _orphan_app.py                  # Helper app for orphan cleanup test
│
├── docs/                   # Project documentation
│   ├── ARCHITECTURE.md     # Detailed architecture patterns
│   ├── API_SPEC.md         # REST/WebSocket endpoint specifications
│   ├── TESTING_GUIDE.md    # Test coverage requirements
│   └── USER_GUIDE.md       # End-user instructions
│
├── logs/                   # Runtime logs (created on first run)
│   └── agent.log           # Agent subprocess logs
│
├── run.py                  # Entry point: cleanup orphans, start UI server
├── migrate_strategies.py   # One-off migration: branching → sliding (archive)
├── CLAUDE.md               # Project constraints and code conventions
├── app.db                  # SQLite database (created on first run, WAL mode)
├── app.db-shm             # SQLite WAL shared memory (auto-created)
├── app.db-wal             # SQLite WAL write-ahead log (auto-created)
├── pytest.ini              # pytest configuration (asyncio_mode = auto)
├── requirements.txt        # Python package dependencies
└── .env                    # Optional environment file (NOT committed)
```

## Directory Purposes

**agent/:**
- Purpose: FastAPI application running as a subprocess (port 8001). Handles all API logic, WebSocket streaming, LLM calls, and database operations.
- Contains: Main app, WebSocket handler, context building, LLM integrations, Pydantic schemas
- Key files: `main.py` (entry point), `ws.py` (streaming), `context_engine.py` (compression)

**ui/:**
- Purpose: FastAPI application (port 8000) that serves the static frontend and manages the Agent subprocess.
- Contains: Main app, supervisor, static HTML/JS files
- Key files: `main.py` (serves `/` and `/static`), `supervisor.py` (spawns/monitors agent)

**shared/:**
- Purpose: Code shared by both UI and Agent processes (no process-specific imports).
- Contains: Configuration, database, logging, SQLModel schemas
- Key files: `models.py` (data schema), `database.py` (async SQLite setup), `config.py` (env vars)

**tests/:**
- Purpose: Full test coverage with separate test database (not `app.db`).
- Contains: Unit, integration, and scenario tests per TESTING_GUIDE.md
- Key pattern: Every test uses fixtures from `conftest.py` to isolate state

**docs/:**
- Purpose: Project documentation for architecture, API contract, testing expectations, and user guide.
- Contains: Specifications, decision records, testing checklists
- Audience: Developers integrating with the system, users of the UI

**logs/:**
- Purpose: Runtime output directory (created on first run by supervisor).
- Contains: `agent.log` with Agent subprocess output (stdout/stderr)
- Management: Manually deleted; not cleaned up automatically

## Key File Locations

**Entry Points:**
- `run.py`: Start UI server (cleanup orphans first, then uvicorn)
- `ui/main.py`: UI FastAPI app (called by `run.py` via uvicorn)
- `agent/main.py`: Agent FastAPI app (spawned by AgentSupervisor as subprocess)

**Configuration:**
- `shared/config.py`: Runtime settings (UI_PORT, AGENT_PORT, DEEPSEEK_API_KEY, LM_STUDIO_BASE_URL)
- `.env`: Optional file with overrides (e.g., DEEPSEEK_API_KEY)
- `pytest.ini`: pytest config (asyncio_mode = auto for pytest-asyncio)

**Core Logic:**
- `agent/ws.py`: WebSocket chat streaming, message persistence, context building orchestration
- `agent/context_engine.py`: Message tree loading, compression strategies, statistics, fact extraction
- `agent/llm_client.py`: OpenAI-compatible API client, token counting, LM Studio control

**Data Model:**
- `shared/models.py`: Chat, Message, Settings, TokenUsage (SQLModel tables)
- `shared/database.py`: Async SQLite engine, migrations, session factory

**Frontend:**
- `ui/static/index.html`: DOM structure, Tailwind styling, script includes
- `ui/static/app.js`: Client state management, API calls, WebSocket listener, UI rendering

**Testing:**
- `tests/conftest.py`: Fixtures (test DB, async session factory, mock agent)
- `docs/TESTING_GUIDE.md`: Test coverage checklist per module

## Naming Conventions

**Files:**
- `main.py`: Entry point for a module/package (e.g., `agent/main.py`, `ui/main.py`)
- `*_engine.py`: Core algorithmic/business logic (e.g., `context_engine.py`)
- `*_client.py`: HTTP/API client (e.g., `llm_client.py`)
- `schemas.py`: Pydantic validation models
- `state.py`: Global in-process state (dicts, caches)
- `test_*.py`: Test modules (one per feature/component)
- `conftest.py`: pytest fixtures and shared test config

**Directories:**
- Lowercase, underscores for multi-word names (e.g., `static/`, `docs/`, `logs/`)
- PascalCase not used for directories
- `tests/` contains all test files; no `test/` subdirectories by feature

**Functions:**
- snake_case throughout
- Prefix with `_` for private/internal (e.g., `_apply_compression_strategy()`)
- Async functions: `async def`, no prefix distinction
- Handlers/callbacks: often descriptive names ending in action (e.g., `_handle_chat_message()`, `_persist_user_message()`)

**Variables:**
- snake_case for local variables
- UPPER_CASE for module-level constants (e.g., `RECENT_MESSAGE_COUNT = 10`, `HEALTHCHECK_INTERVAL = 3.0`)
- Dict keys: snake_case (e.g., `{"chat_id": 1, "role": "user"}`)

**Types:**
- Class names: PascalCase (e.g., `Chat`, `Message`, `Settings`, `LLMClient`, `AgentSupervisor`)
- Enum values: snake_case in string value (e.g., `ContextStrategy.SLIDING_WINDOW` → `"sliding"` in DB/API)

## Where to Add New Code

**New Feature (e.g., export chat history):**
- Primary code: `agent/main.py` (new REST endpoint) + `agent/ws.py` (if WebSocket-related)
- Database schema change: `shared/models.py` + `shared/database.py` (new migration in `async def init_db()`)
- Tests: `tests/test_export.py` (new file following existing patterns)

**New Component/Module (e.g., plugin system):**
- Implementation: `agent/plugins.py` (if Agent-owned) or `shared/plugins.py` (if shared)
- Schemas: `agent/schemas.py` (add new request/response classes)
- Tests: `tests/test_plugins.py`
- Avoid: New packages outside `agent/`, `ui/`, `shared/`, `tests/`

**Utilities (e.g., text processing helpers):**
- Shared utilities: `shared/utils.py` (if used by both processes or multiple modules)
- Agent-only: `agent/utils.py` (if only Agent uses it)
- Frontend: `ui/static/utils.js` (if only frontend uses it)

**Database Migrations:**
- Location: `shared/database.py` in the `init_db()` function
- Pattern: Define an `async def migrate_*()` function, call it from `init_db()` in sequence
- Example: `migrate_add_context_length()` (see existing code)

**Tests:**
- Location: `tests/test_*.py`, one file per feature area
- Async tests: Use `pytest.mark.asyncio` or rely on `asyncio_mode = auto` in `pytest.ini`
- Fixtures: Add to `tests/conftest.py` if reusable; keep local if test-specific

**Configuration:**
- New settings: Add to `shared/config.py::Settings` class with default value
- Load from env: Pydantic automatically maps `ENV_VAR_NAME` to `env_var_name` in the class
- Example: `DEEPSEEK_API_KEY: str = ""`

**Static Files:**
- New styles: Add to `<style>` block in `ui/static/index.html` or use Tailwind classes
- New scripts: Add to `ui/static/app.js` or create new `.js` file and include in `index.html`
- CDN libraries: Add `<script>` tags in `<head>`; avoid npm/bundler per hard constraints

## Special Directories

**logs/:**
- Purpose: Agent subprocess logs (stdout/stderr redirected here)
- Generated: Yes (created by supervisor on first agent launch)
- Committed: No (created at runtime, should be in `.gitignore`)
- Cleanup: Manual deletion recommended (no automatic rotation)

**app.db, app.db-shm, app.db-wal:**
- Purpose: SQLite database and write-ahead log files
- Generated: Yes (by `shared/database.py::init_db()` on first run)
- Committed: No (runtime data, should be in `.gitignore`)
- Cleanup: Delete `app.db*` to reset database state

**.env:**
- Purpose: Optional environment variable file for local overrides
- Generated: No (user must create if needed)
- Committed: No (never commit credentials, in `.gitignore`)
- Example: `DEEPSEEK_API_KEY=sk-...` (see `shared/config.py` for all available vars)

**__pycache__, *.pyc:**
- Purpose: Python bytecode cache
- Generated: Yes (automatic)
- Committed: No (in `.gitignore`)
- Cleanup: `rm -rf **/__pycache__` if needed; tools handle this

---

*Structure analysis: 2026-09-19*
