# Codebase Concerns

**Analysis Date:** 2026-09-19

## Tech Debt

### Stubbed Summarization Function

**Issue:** `summarize_if_needed()` in `agent/context_engine.py` (lines 127-136) is a no-op stub that returns history unchanged.

**Files:** `agent/context_engine.py::summarize_if_needed`

**Impact:** The function exists in the call chain (`build_llm_context` → `summarize_if_needed`) but does nothing. All heavy lifting falls on the four compression strategies instead. If a use case requires LLM-based summarization (rather than simple truncation), this code path is not ready.

**Fix approach:** Either remove the function entirely (simplifying the call stack) or implement LLM-based summarization for the `sticky` and `truncate_middle` strategies. Decision depends on product roadmap.

### Deprecated summary_text Field

**Issue:** `Settings.summary_text` field exists but is never automatically populated; `build_system_prompt()` still injects it into the prompt if non-empty (line 60-61 in `context_engine.py`).

**Files:** `shared/models.py::Settings.summary_text`, `agent/context_engine.py::build_system_prompt`

**Impact:** Dead code. The field can only be set via direct API calls to `PUT /api/v1/settings`. The ARCHITECTURE.md (line 49-52) notes that nothing in the codebase writes to it automatically. Maintaining this field adds schema complexity with zero active use.

**Fix approach:** Remove the field and its injection logic, or implement automatic summarization to activate it. Backward-compatibility API endpoint can still accept the field but ignore it.

### Settings Fallback Pattern Brittle

**Issue:** Global settings fallback is manual in every place that needs it. Functions like `get_effective_settings()` (line 36-49 in `context_engine.py`) and `_resolve_settings()` (line 66-79 in `agent/main.py`) duplicate the logic.

**Files:** `agent/context_engine.py::get_effective_settings`, `agent/main.py::_resolve_settings`, `agent/ws.py::_handle_chat_message` (line 152)

**Impact:** Any new settings-related code must remember to call the fallback; missing it silently falls back to defaults. CLAUDE.md (code conventions section) explicitly warns: "any new settings-related code must preserve this fallback."

**Fix approach:** Create a single centralized settings resolver in `shared/models.py` or use a dependency injection pattern in FastAPI.

## Known Bugs

### Context Overflow Irreversibly Deletes Message

**Issue:** When `ContextStrategy.NO_COMPRESSION` is active and tokens exceed `context_length`, the user message is deleted from the database as a side effect of overflow handling (lines 165-181 in `agent/ws.py`).

**Files:** `agent/ws.py::_handle_chat_message` (lines 171-181), `agent/context_engine.py::_apply_compression_strategy` (lines 174-186)

**Symptoms:** User receives "CONTEXT_OVERFLOW" error and sees no response, but their message disappears from the chat tree. Branching to the deleted message's parent and re-sending a different message is the workaround.

**Trigger:** Set `strategy="no_compression"`, `context_length=100`, send 10+ long messages (200+ tokens each).

**Workaround:** Switch strategy to `sliding`, `sticky`, or `truncate_middle` before the overflow condition is reached. The UI should display a warning as context usage approaches 75%.

### Debounced Facts Extraction Loses Rapid Messages

**Issue:** Facts extraction is debounced globally per chat (2 seconds, `FACTS_DEBOUNCE_SECONDS` in `context_engine.py` line 20). If a user sends 3 messages within 2 seconds, only the last message is considered for fact extraction.

**Files:** `agent/context_engine.py::extract_and_update_facts` (lines 419-430), `agent/context_engine.py::_run_debounced_facts` (lines 363-375)

**Impact:** Facts from intermediate messages are ignored. Critical user preferences or constraints stated in rapid-fire messages may not make it into the facts JSON.

**Trigger:** Send multiple messages within the 2-second debounce window in the UI or via WebSocket batch.

**Workaround:** Wait 2+ seconds between messages, or disable facts extraction and rely on full message history for context.

### LM Studio Emergency Unload Silently Fails

**Issue:** If a model load times out (120s), an emergency unload is attempted with a 5-second hard timeout (lines 194-195 in `agent/llm_client.py`). If that also fails, `_current_loaded_model` is cleared but the model may still be consuming GPU memory.

**Files:** `agent/llm_client.py::_load_model_locked` (lines 193-200), `agent/llm_client.py::_emergency_unload` (lines 262-276)

**Impact:** LM Studio process becomes unresponsive; next model load attempt may hang or fail. The supervisor's health checks (`ui/supervisor.py::_ping_health`) will not catch this because the process is still alive.

**Trigger:** Load a large model (10B+ params), hit the 120s timeout, network interruption during emergency unload.

**Workaround:** Manually restart LM Studio, or increase `LOAD_TIMEOUT` in `agent/llm_client.py` line 17.

## Security Considerations

### CORSMiddleware Allows All Origins on Agent

**Issue:** `agent/main.py` (lines 162-168) adds CORSMiddleware with `allow_origins=["*"]`.

**Files:** `agent/main.py::app.add_middleware`

**Risk:** CSRF attacks possible. However, this is mitigated by:
1. Single-user design (no shared state between users)
2. Local-only deployment (no external network access assumed)
3. WebSocket origin validation in `agent/ws.py::_validate_origin` (lines 40-68) provides a secondary check

**Recommendation:** Change `allow_origins` to an allowlist (`["http://localhost:8000", "http://127.0.0.1:8000"]`) or restrict to same-origin only and rely on WebSocket validation.

### WebSocket Origin Validation Allows "null" Origin

**Issue:** `agent/ws.py::_validate_origin()` (line 53-55) allows `origin == "null"` — a browser-sent header for local file:// URIs and some sandboxed contexts.

**Files:** `agent/ws.py::_validate_origin`

**Risk:** Low in single-user mode, but allows attacks from malicious file:// resources on the same machine.

**Recommendation:** Document this behavior or add a configuration flag to disable it in production-like deployments.

### API Key Exposed in Environment

**Issue:** `DEEPSEEK_API_KEY` is stored in environment variables (loaded via `shared/config.py::Settings`) and may be logged or exposed in error messages.

**Files:** `shared/config.py`, `agent/llm_client.py::LLMClient.__init__` (line 51)

**Risk:** If logs are printed or forwarded without redaction, the API key is compromised.

**Recommendation:** Add log sanitization to remove API keys from structured logs (e.g., redact `Authorization` headers).

### No Rate Limiting on REST Endpoints

**Issue:** Rate limiting is only applied to WebSocket messages (`agent/ws.py::_check_rate_limit`), not REST API endpoints (e.g., `POST /api/v1/chats`, `PUT /api/v1/settings`).

**Files:** `agent/ws.py` (line 71-81), but missing from `agent/main.py` routes

**Risk:** DOS via rapid API calls. An attacker can create/delete chats or spam settings updates faster than WebSocket messages.

**Recommendation:** Add per-IP rate limiting middleware for REST endpoints, or at least for mutation endpoints (POST, PUT, DELETE).

## Performance Bottlenecks

### Token Counting on Every Message

**Issue:** `llm_client.count_tokens()` is called on every user and assistant message during persistence, and again during context building (lines 29, 95, 118, 323-330 in various files).

**Files:** `agent/llm_client.py::count_tokens`, `agent/ws.py::_persist_user_message` (line 95), `agent/ws.py::_persist_assistant_message` (line 118)

**Impact:** Tiktoken encoding is CPU-intensive. For a 1000-message chat with 100-token average messages, this means ~2000 token counts per request.

**Improvement path:** Cache token counts in the Message table (already done), but avoid re-counting during stats computation. Use precomputed token_count field instead of calling `count_tokens()` again in `compute_chat_stats()` (line 325).

### All Messages Loaded into Memory

**Issue:** `_load_branch_messages()` (lines 101-124 in `context_engine.py`) loads the entire message tree path into memory as a list. For chats with 10k+ messages, this can be slow.

**Files:** `agent/context_engine.py::_load_branch_messages`

**Impact:** Memory overhead and query time for large chats. The compression strategies then iterate over this list (linear scans, no indexing).

**Improvement path:** Implement pagination or lazy-load messages; add database indexes on `chat_id` and `parent_id`.

### Facts Extraction Makes LLM Call Per Chat

**Issue:** After every user message, `extract_and_update_facts()` schedules an LLM call via `_extract_facts()` (line 387 in `context_engine.py`). Even with debouncing, this is one extra LLM round-trip per ~2 seconds of chat.

**Files:** `agent/context_engine.py::_extract_facts` (lines 378-416)

**Impact:** Increased latency and API costs. For 60 messages in a 2-minute chat, this is ~30 LLM calls (one every 2 seconds).

**Improvement path:** Implement batch fact extraction (extract facts from the last N messages instead of just the latest), or make it optional per-chat setting.

### No Database Indexes on Foreign Keys

**Issue:** `shared/models.py` defines FK relationships but migration/schema creation (via SQLModel) does not explicitly create indexes.

**Files:** `shared/models.py::Chat.current_leaf_message_id`, `shared/models.py::Message.chat_id`, `shared/models.py::Settings.chat_id`

**Impact:** Queries like "get all messages for a chat" require full table scans. For 100k+ messages, this is slow.

**Recommendation:** Add explicit index creation in `shared/database.py::init_db()` via `CREATE INDEX IF NOT EXISTS`.

## Fragile Areas

### Message Tree Walks Assume Unbroken Chain

**Issue:** `_load_branch_messages()` and `_build_tree_path()` walk up the parent_id chain until reaching a message with `parent_id = None`. If the chain is broken (e.g., parent message deleted, orphaned FK), the walk terminates silently.

**Files:** `agent/context_engine.py::_load_branch_messages` (lines 106-116), `agent/main.py::_build_tree_path` (lines 130-145)

**Impact:** Incomplete chat history returned to user and LLM. Silent corruption is worse than a loud error.

**Safe modification:** Add a break condition with logging if a non-null parent_id is not found in the database:
```python
if message is None and current_id is not None:
    logger.error("broken_parent_chain", missing_message_id=current_id, chat_id=chat_id)
```

### Token Counting Divergence

**Issue:** Tiktoken's `cl100k_base` encoding used by the app may not match the LLM's actual tokenizer (e.g., DeepSeek or LM Studio models may use different encodings).

**Files:** `agent/llm_client.py::LLMClient.__init__` (line 34)

**Impact:** `current_context_size` in stats may differ from the LLM's actual token count. A context marked as "85% full" might be 90%+ to the LLM, causing unexpected overflow.

**Improvement path:** Fetch the actual token count from the LLM API if supported, or use a model-specific tokenizer.

### WebSocket Message Buffering Without Backpressure

**Issue:** `_handle_chat_message()` streams tokens to the client via `websocket.send_json()` (lines 198-200 in `agent/ws.py`) without checking if the WebSocket buffer is full or the client is slow.

**Files:** `agent/ws.py::_handle_chat_message` (lines 198-200)

**Impact:** If the client is slow or offline, tokens accumulate in the server's buffer, consuming memory. A high-speed model (e.g., 100+ tokens/sec) + slow client can exhaust memory in seconds.

**Fix approach:** Check `websocket.send_json()` return value or await confirmation, or implement a circular buffer with overflow handling.

### LM Studio Model Switch Lock Serializes All Requests

**Issue:** `_model_switch_lock` (line 134 in `agent/llm_client.py`) serializes model loads globally. If a user is loading a model, all other users' chat requests that need a different model must wait.

**Files:** `agent/llm_client.py::LMStudioClient._model_switch_lock`

**Impact:** In a multi-user scenario, this becomes a bottleneck. Single-user mode is not affected.

**Recommendation:** Document this as a single-user-only limitation, or implement per-model locking instead of global.

## Scaling Limits

### Single SQLite Database, No Sharding

**Issue:** `shared/database.py` uses a single SQLite file at `settings.DB_PATH`. SQLite has limits on concurrent writes (5s busy timeout via PRAGMA).

**Files:** `shared/database.py` (line 37: `PRAGMA busy_timeout=5000`)

**Limit:** Concurrent write operations block each other; throughput plateaus around 1-2k writes/sec depending on message size. A high-volume multi-user server will hit this quickly.

**Scaling path:** Migrate to PostgreSQL or implement message archival/partitioning.

### Message Tree Growth Unbounded

**Issue:** Messages are never deleted (except via cascade on chat deletion). Over years, a single chat can accumulate 100k+ messages, slowing tree walks and pagination.

**Files:** `shared/models.py::Message`, `agent/context_engine.py::_load_branch_messages`

**Recommendation:** Implement message archival (mark old messages as archived, exclude from tree walks) or hard deletion policies.

### Facts JSON Unlimited Growth

**Issue:** `Settings.facts_json` is a TEXT field with no size limit. If facts extraction runs forever, it could accumulate gigabytes of JSON.

**Files:** `shared/models.py::Settings.facts_json`

**Improvement path:** Cap the facts object size (e.g., max 10k characters), or implement a rolling window (keep only facts from the last N messages).

## Dependencies at Risk

### tiktoken Version Pinning

**Issue:** `requirements.txt` specifies `tiktoken>=0.7.0` (loose pin). Tiktoken changes encoding behavior in minor versions, which could cause token count mismatches across deployments.

**Files:** `requirements.txt` (line 23)

**Recommendation:** Pin to a specific version: `tiktoken==0.7.0` or use `tiktoken>=0.7.0,<0.8.0`.

### SQLModel Async Maturity

**Issue:** SQLModel async support is relatively new (0.0.22+). Edge cases with cascading deletes, FK constraints, and concurrent sessions may not be fully tested by the ecosystem.

**Files:** `shared/database.py`, `shared/models.py`

**Recommendation:** Monitor SQLModel/SQLAlchemy releases for CASCADE-related bug fixes; test cascade delete scenarios thoroughly (already done in `tests/test_cascade_delete.py`).

## Missing Critical Features

### No Automatic Message Summarization

**Issue:** `summarize_if_needed()` is stubbed; compression strategies only truncate. For long chats (100+ messages), context is lost without LLM summarization.

**Files:** `agent/context_engine.py::summarize_if_needed`

**Blocks:** Users with long research sessions or brainstorms cannot keep full context within token limits without manually copying summaries into the system prompt.

**Priority:** Medium—workaround exists (use `sticky` strategy + facts extraction).

### No Message Retention or Archival Policy

**Issue:** Messages accumulate forever. No automatic deletion, archival, or age-based culling.

**Files:** `shared/models.py::Message`

**Blocks:** Long-running deployments will slow down over time as the database grows.

**Priority:** Low for single-user, High for multi-user at scale.

### No Transaction Batching for Concurrent Writes

**Issue:** Each message write is one transaction. When handling 5 concurrent WebSocket requests (test case in `tests/test_concurrent_ws.py`), each transaction competes for the SQLite lock.

**Files:** `agent/ws.py::_handle_chat_message`, `agent/ws.py::_persist_user_message`, `agent/ws.py::_persist_assistant_message`

**Blocks:** High-concurrency deployments may see "database is locked" errors.

**Priority:** Low for single-user, High for multi-user.

### Limited Error Recovery for LLM Failures

**Issue:** If `llm_client.stream_chat()` fails mid-stream, the user message is deleted (line 214 in `agent/ws.py`) without retry logic. The user must resend.

**Files:** `agent/ws.py::_handle_chat_message` (lines 201-216)

**Blocks:** Users cannot recover from transient LLM failures (e.g., DeepSeek rate limit, LM Studio crash).

**Recommendation:** Implement exponential backoff retry (up to 3 attempts) before deleting the message.

### No Monitoring or Metrics Collection

**Issue:** Token usage is tracked, but no metrics for response latency, error rates, LLM API costs, or per-model performance.

**Files:** No dedicated metrics file; only `compute_chat_stats()` for per-chat stats

**Blocks:** Cannot identify performance regressions, cost anomalies, or user experience issues without manual log analysis.

**Priority:** Low for single-user, Medium for production multi-user.

## Test Coverage Gaps

### Facts Extraction Correctness Not Tested

**Issue:** `tests/test_context_engine.py` tests fallback behavior but not whether `extract_and_update_facts()` actually calls the LLM or produces correct JSON.

**Files:** Tests at `tests/test_context_engine.py` (lines 78+)

**Risk:** Facts could silently fail or produce invalid JSON, and tests would not catch it.

**Recommendation:** Add tests for fact extraction with mocked LLM responses.

### Database Schema Migration Edge Cases

**Issue:** `shared/database.py::migrate_add_context_length()` handles one schema change, but not:
  - Rollback on migration failure
  - Concurrent migrations (two processes calling `init_db()`)
  - Partial migrations (table exists but migration was interrupted)

**Files:** `shared/database.py::migrate_add_context_length` (lines 73-92)

**Risk:** Schema corruption or silent failures during deployment.

**Recommendation:** Add transaction wrapping and idempotency checks.

### Very Large Message Counts Not Tested

**Issue:** No test for chat with 10k+ messages. Compression strategies and tree walks may be slow or crash.

**Files:** Tests use max ~25 messages (see `test_strategies.py` line 100)

**Risk:** Production chat with years of history could become unusable.

**Recommendation:** Add a stress test with 10k message chat.

### Broken Parent_ID Chain Not Tested

**Issue:** No test for a message with a non-existent parent_id. Tree walks will silently terminate.

**Files:** Test coverage for cascade delete exists, but not for orphaned FK.

**Risk:** Silent data corruption.

**Recommendation:** Add test that manually creates an orphaned message and verifies the walk stops gracefully.

### WebSocket Reconnection Not Tested

**Issue:** UI has reconnection logic (`app.js`), but no test for reconnecting mid-stream or after a dropped connection recovers.

**Files:** Frontend logic in `ui/static/app.js`, no backend test

**Risk:** Partial messages, duplicate messages, or missed "done" signals on reconnection.

**Recommendation:** Add test that simulates WebSocket close and reconnect during a streaming response.

### LM Studio Emergency Unload Not Triggered in Tests

**Issue:** `_emergency_unload()` is called on load timeout, but no test exercises this path.

**Files:** `agent/llm_client.py::_emergency_unload`, no test coverage in `tests/test_lm_studio_client.py`

**Risk:** Emergency unload logic could be broken and not caught until production failure.

**Recommendation:** Add test that mocks a 120s timeout and verifies emergency unload is called.

---

*Concerns audit: 2026-09-19*
