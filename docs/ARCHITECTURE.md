# Architecture Guide

## Process Communication
- UI launches Agent via asyncio.create_subprocess_exec
- Healthcheck: GET /health every 3 seconds
- Orphan cleanup: psutil to find/kill processes on port 8001

## Context Compression Strategies

The system implements 4 context compression strategies (`agent/context_engine.py::_apply_compression_strategy`)
that control what is sent to the LLM. All are deterministic slicing/dropping of messages —
**none of them summarize the dropped content**; that content simply isn't sent to the LLM this turn.
**Important:** Database history is NEVER modified - strategies only affect LLM context.

### 1. Sliding Window (sliding)
- **Behavior:** Keeps only the last `RECENT_MESSAGE_COUNT` (10) messages; everything older is dropped from the LLM context entirely
- **When triggered:** Total tokens exceed 75% of `context_length` (`SUMMARY_TRIGGER_RATIO`), or the chat already has more than 10 messages
- **Best for:** Fast chats, maintaining recent context
- **Database:** All messages preserved

### 2. Sticky Facts (sticky)
- **Behavior:** Keeps the first message of the active branch + the last 10 messages (no overlap); the middle is dropped, not summarized
- **When triggered:** Same threshold as above (75% of `context_length`)
- **Best for:** Long consultations where the original instruction/goal must stay visible, combined with the facts mechanism below
- **Database:** All messages preserved

### 3. Truncate Middle (truncate_middle)
- **Behavior:** Keeps the first 2 messages + the last 10 messages (no overlap); the middle is dropped, not summarized
- **When triggered:** Same threshold as above (75% of `context_length`)
- **Best for:** Preserving initial instructions and recent context
- **Database:** All messages preserved

### 4. No Compression (no_compression)
- **Behavior:** Sends all messages to LLM without compression
- **When triggered:** Always (no compression applied)
- **Protection:** Raises ContextOverflowError if tokens exceed context_length
- **Best for:** Short conversations, when full context is critical
- **Database:** All messages preserved

## Key-Value Facts Extraction

Independent of which compression strategy is selected, `agent/context_engine.py::extract_and_update_facts`
runs after every user message:
- Debounced 2s (`FACTS_DEBOUNCE_SECONDS`) per chat — rapid consecutive messages coalesce into one extraction call
- Calls the LLM to extract key facts (goal, constraints, preferences, decisions) as a JSON object
- Merges the result into `Settings.facts_json` (new keys overwrite old ones on conflict)
- `build_system_prompt` injects the accumulated facts into the system prompt on every turn, **for every strategy**, not just `sticky`

`Settings.summary_text` still exists in the schema and API (`PUT /api/v1/settings`) for backward
compatibility, and is injected into the system prompt when non-empty, but nothing in the current
codebase writes to it automatically — `summarize_if_needed()` is a no-op stub, and the field has no
UI control. It can only be populated by calling the settings API directly.

## Branching (message forking)

Branching is a separate feature from context compression, not a `ContextStrategy` value (an earlier
`"branching"` strategy enum value was migrated away — see `migrate_strategies.py`). It works directly
on the message tree:
- `Chat.current_leaf_message_id` marks a checkpoint; `POST /api/v1/chats/{id}/branch` repoints it to any
  earlier message without copying data
- Sending a new message after branching to an earlier point creates a sibling under that message's
  `parent_id`, forking the conversation into two independent branches
- The UI (`ui/static/app.js`) exposes "branch from message" plus prev/next sibling controls to fork and
  switch between branches

## Context Overflow Protection

When NO_COMPRESSION strategy is active and total tokens exceed context_length:
1. System raises ContextOverflowError
2. User message is deleted from database (no response generated)
3. WebSocket sends error with code "CONTEXT_OVERFLOW"
4. UI displays red banner and blocks input
5. User must change strategy or reduce conversation length

## Statistics Tracking

Each message stores token_count in database. Real-time statistics include:
- total_request_tokens: Sum of all user message tokens
- total_response_tokens: Sum of all assistant message tokens
- current_context_size: Tokens that will be sent to LLM (strategy-dependent)
- context_window_size: From settings.context_length
- context_usage_percent: Percentage of context window used

Statistics update after each message and are sent with WebSocket "done" message.

## LLM Integration

### DeepSeek (Cloud)
- OpenAI-compatible API
- Streaming via SSE
- Token counting with tiktoken (cl100k_base)

### LM Studio (Local)
- OpenAI-compatible API at /v1/
- Control API at /api/v0/ for model management
- Endpoints:
  - GET /v1/models - List available models
  - POST /api/v0/models/load - Load model into RAM/VRAM
  - POST /api/v0/models/unload - Unload model
- model_switch_lock prevents concurrent model loading
- Emergency unload (5s timeout) on load timeout
- Detection of "LM Studio not running" via ConnectError
