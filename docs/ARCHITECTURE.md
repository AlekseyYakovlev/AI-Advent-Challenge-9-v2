# Architecture Guide

## Process Communication
- UI launches Agent via asyncio.create_subprocess_exec
- Healthcheck: GET /health every 3 seconds
- Orphan cleanup: psutil to find/kill processes on port 8001

## Context Compression Strategies

The system implements 4 context compression strategies that control what is sent to the LLM.
**Important:** Database history is NEVER modified - strategies only affect LLM context.

### 1. Sliding Window (sliding)
- **Behavior:** Keeps only the last 10 messages (RECENT_PAIR_COUNT * 2)
- **When triggered:** Total tokens exceed 75% of context_length
- **Best for:** Fast chats, maintaining recent context
- **Database:** All messages preserved

### 2. Sticky Facts (sticky)
- **Behavior:** Summarizes old messages into English summary, keeps recent messages
- **When triggered:** Total tokens exceed 75% of context_length
- **Best for:** Long consultations, extracting key facts
- **Accumulation:** [Old Summary] + [New Summary] + [Recent messages]
- **Re-summarization:** When summary exceeds 1500 tokens
- **Database:** All messages preserved, summary stored in settings.summary_text

### 3. Truncate Middle (truncate_middle)
- **Behavior:** Keeps first 5 messages + middle summary + last 10 messages
- **When triggered:** Total tokens exceed 75% of context_length
- **Best for:** Preserving initial instructions and recent context
- **Middle summary:** Generated in English, 2-3 sentences
- **Database:** All messages preserved

### 4. No Compression (no_compression)
- **Behavior:** Sends all messages to LLM without compression
- **When triggered:** Always (no compression applied)
- **Protection:** Raises ContextOverflowError if tokens exceed context_length
- **Best for:** Short conversations, when full context is critical
- **Database:** All messages preserved

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
