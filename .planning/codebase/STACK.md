# Technology Stack

**Analysis Date:** 2026-09-19

## Languages

**Primary:**
- Python 3.x - Backend (API, database, LLM integration, logging, process management)

**Secondary:**
- JavaScript - Frontend (vanilla JS, no bundler, CDN-based)
- HTML/CSS - UI markup and styling (Tailwind CSS from CDN)

## Runtime

**Environment:**
- Python standard runtime (asyncio for I/O, no multiprocessing/os.fork)

**Package Manager:**
- pip
- Lockfile: Not detected (uses requirements.txt with pinned versions)

## Frameworks

**Core:**
- FastAPI 0.115.0+ - REST API and WebSocket server for both UI and Agent processes
- Uvicorn 0.30.0+ - ASGI application server (runs both `ui/main.py` and `agent/main.py`)

**Database & ORM:**
- SQLModel 0.0.22+ - ORM combining SQLAlchemy 2.x and Pydantic v2 for type-safe queries
- aiosqlite 0.20.0+ - Async SQLite driver (critical for concurrent database access)
- SQLAlchemy 2.x - Query engine (via SQLModel)

**Testing:**
- pytest 8.3.0+ - Test runner
- pytest-asyncio 0.24.0+ - Async test support (asyncio_mode=auto in `pytest.ini`)
- respx 0.21.0+ - HTTP request mocking for LM Studio and DeepSeek API testing

**Build/Dev:**
- None configured (no linter, formatter, or type checker in codebase)

## Key Dependencies

**Critical:**
- httpx 0.27.0+ - Async HTTP client for DeepSeek API and LM Studio control plane
- tiktoken 0.7.0+ - Token counting using `cl100k_base` encoding (OpenAI-compatible)
- Pydantic 2.9.0+ - Request/response validation and serialization (required by FastAPI and SQLModel)
- pydantic-settings 2.5.0+ - Environment variable loading from `.env` file

**Process & Logging:**
- structlog 24.4.0+ - Structured JSON logging to console/stdout
- psutil 6.0.0+ - Process introspection for orphan cleanup (`cleanup_port()` in `run.py`) and health monitoring

**Frontend (CDN):**
- Tailwind CSS (from `https://cdn.tailwindcss.com`) - Styling framework
- Marked.js (from `https://cdn.jsdelivr.net/npm/marked/`) - Markdown parsing for message rendering
- DOMPurify 3.1.6 (from `https://cdn.jsdelivr.net/npm/dompurify@3.1.6/`) - HTML sanitization to prevent XSS

## Configuration

**Environment:**
- Loaded via `pydantic-settings` from `.env` file (see `shared/config.py`)
- No hardcoded secrets; all credentials come from environment

**Required Environment Variables:**
- `DEEPSEEK_API_KEY` - API key for DeepSeek cloud LLM
- `LM_STUDIO_BASE_URL` - Base URL for local LM Studio (`http://localhost:1234`)
- `UI_PORT` - Port for UI server (default: 8000)
- `AGENT_PORT` - Port for Agent server (default: 8001)
- `DB_PATH` - SQLite database file path (default: `app.db`)
- `LLM_TIMEOUT` - HTTP timeout for LLM API calls (default: 60.0 seconds)

**Build:**
- `pytest.ini` - Test configuration (asyncio_mode=auto, testpaths=tests)
- `.env.example` - Template for environment variables

## Platform Requirements

**Development:**
- Python 3.8+ (with asyncio support)
- pip for dependency installation
- SQLite3 (usually bundled with Python)
- Chrome browser (for UI interaction)

**Production:**
- Python 3.8+ runtime
- SQLite3
- Accessible DeepSeek API endpoint (cloud LLM) OR LM Studio running on localhost:1234 (local LLM)
- HTTP port 8000 (UI) and 8001 (Agent) available

## Database Schema

**Engine:** SQLite with aiosqlite async driver

**Features Enabled:**
- WAL mode (`PRAGMA journal_mode=WAL`) - Write-Ahead Logging for concurrency
- Foreign key constraints (`PRAGMA foreign_keys=ON`) - Cascading deletes via SQLAlchemy
- Busy timeout (`PRAGMA busy_timeout=5000`) - 5-second wait on lock contention

**Tables (SQLModel ORM):**
- `chat` - Conversation trees with `current_leaf_message_id` pointer
- `message` - Tree structure via `parent_id`, stores content and token counts
- `settings` - Global defaults (chat_id=NULL) and per-chat overrides (chat_id=non-null)
- `token_usage` - Token tracking for request/response pairs (nullable, populated via LLM integration)

---

*Stack analysis: 2026-09-19*
