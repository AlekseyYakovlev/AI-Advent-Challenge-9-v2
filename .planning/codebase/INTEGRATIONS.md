# External Integrations

**Analysis Date:** 2026-09-19

## APIs & External Services

**LLM Backends:**

1. **DeepSeek (Cloud)**
   - What it's used for: Primary cloud-based LLM inference with streaming token support
   - SDK/Client: httpx AsyncClient (OpenAI-compatible `/v1/chat/completions`)
   - Auth: Environment variable `DEEPSEEK_API_KEY` (passed as Bearer token in Authorization header)
   - Implementation: `agent/llm_client.py::LLMClient` class handles connection, streaming, and token counting
   - Endpoint: Dynamic base URL configured via `settings.LM_STUDIO_BASE_URL` or hardcoded for DeepSeek
   - Features: SSE streaming, supports `temperature`, `max_tokens`, `model` parameters

2. **LM Studio (Local)**
   - What it's used for: Local on-device LLM inference for offline operation
   - SDK/Client: httpx AsyncClient (OpenAI-compatible `/v1/chat/completions` plus control API)
   - Connection: Default `http://localhost:1234` (configurable via `LM_STUDIO_BASE_URL`)
   - Implementation: `agent/llm_client.py::LMStudioClient` class handles model loading/unloading and inference
   - Control Endpoints:
     - `GET /v1/models` - List loaded models
     - `POST /api/v0/models/load` - Load model with GPU offload and context length options
     - `POST /api/v0/models/unload` - Unload model from VRAM
   - Features: Concurrent load requests serialized via `model_switch_lock` with 5-second emergency-unload fallback
   - Error handling: `httpx.ConnectError` means "LM Studio not running" (distinct from HTTP errors)

## Data Storage

**Databases:**
- SQLite (file-based)
  - Connection: `sqlite+aiosqlite:///{DB_PATH}` (default: `app.db`)
  - Client: SQLAlchemy 2.x via SQLModel ORM
  - Async driver: aiosqlite
  - Features: WAL mode, foreign key constraints, busy timeout

**File Storage:**
- Local filesystem only (no S3, no cloud storage)
- UI static assets: `ui/static/` directory (index.html, app.js)
- Database file: `app.db` in project root (configurable via `DB_PATH` env var)

**Caching:**
- In-memory Python dictionaries per process (UI and Agent have separate caches)
- In-memory state cache cleared on chat deletion via `agent/state.py::cleanup_chat_caches`
- No Redis, Memcached, or external cache backend

## Authentication & Identity

**Auth Provider:**
- Custom (none - single-user, local-first by design)
- Implementation: No authentication layer; all requests are treated as same user
- Process isolation: UI and Agent communicate via HTTP only; no shared session state

**LLM API Keys:**
- DeepSeek: Provided via `DEEPSEEK_API_KEY` environment variable
- LM Studio: No authentication (local service assumed on trusted network)

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry, Datadog, or external service)
- Errors logged locally via structlog

**Logs:**
- structlog 24.4.0+ - Structured JSON logging to stdout
- Configuration: `shared/logger.py` provides `get_logger(__name__)` for all modules
- Levels: DEBUG, INFO, WARNING, ERROR per Python logging spec
- No file persistence; logs go to console only

**Health Checks:**
- UI supervisor pings Agent at `GET /health` every 3 seconds (`ui/supervisor.py`)
- If Agent crashes, supervisor auto-restarts it via `asyncio.create_subprocess_exec`

## CI/CD & Deployment

**Hosting:**
- Local single-machine deployment (no Docker, no Kubernetes)
- Two processes on same machine: UI (port 8000) + Agent (port 8001)

**CI Pipeline:**
- None detected (no GitHub Actions, Jenkins, or CI config files)
- Manual testing via pytest (see TESTING.md for test commands)

## Environment Configuration

**Required env vars:**
- `DEEPSEEK_API_KEY` - Bearer token for DeepSeek cloud LLM
- `LM_STUDIO_BASE_URL` - Base URL for local LM Studio (default: `http://localhost:1234`)
- `UI_PORT` - FastAPI server port for UI (default: 8000)
- `AGENT_PORT` - FastAPI server port for Agent (default: 8001)
- `DB_PATH` - SQLite database file path (default: `app.db`)
- `LLM_TIMEOUT` - Timeout for LLM HTTP requests in seconds (default: 60.0)

**Secrets location:**
- `.env` file (never committed to git)
- Template: `.env.example` (checked in)
- Loading: `pydantic-settings` via `shared/config.py::Settings` class

## Webhooks & Callbacks

**Incoming:**
- None (no webhook endpoints)

**Outgoing:**
- None (no event delivery to external services)

## Inter-Process Communication

**UI → Agent:**
- HTTP REST API calls to `http://127.0.0.1:{AGENT_PORT}/api/v1/...`
- WebSocket connection at `ws://127.0.0.1:{AGENT_PORT}/ws/chat/{chat_id}`

**Agent Startup:**
- UI supervisor (`ui/supervisor.py`) launches Agent via `asyncio.create_subprocess_exec("python", "-m", "agent.main")`
- No IPC broker, no message queue — pure HTTP/WebSocket

## Token Counting

**Provider:**
- tiktoken library (OpenAI's tokenizer)
- Encoding: `cl100k_base` (same encoding as GPT-3.5-turbo/GPT-4)
- Usage: `agent/llm_client.py::LLMClient.count_tokens()` for context window management
- Statistics: Token counts stored per message in database (`message.token_count`)

## Settings & Configuration API

**Global Settings (fallback):**
- Single row in `settings` table with `chat_id=NULL`
- Created on first access by `agent/main.py::_ensure_global_settings()`

**Per-Chat Settings (overrides):**
- One row per chat with `chat_id={chat_id}`
- Fields: `system_prompt`, `temperature`, `context_length`, `max_tokens`, `strategy` (ContextStrategy enum)
- API endpoint: `PUT /api/v1/chats/{chat_id}/settings`
- Fallback: If per-chat row missing, global defaults used via `agent/main.py::_resolve_settings()`

---

*Integration audit: 2026-09-19*
