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
- No authentication (single-user mode by design).
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
