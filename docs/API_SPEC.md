# API Specification

## Agent (port 8001)
- GET  /health
- GET  /api/v1/chats
- POST /api/v1/chats
- DELETE /api/v1/chats/{chat_id}
- GET  /api/v1/chats/{chat_id}/tree
- GET  /api/v1/chats/{chat_id}/stats
- POST /api/v1/chats/{chat_id}/branch
- GET  /api/v1/settings?chat_id={id}
- PUT  /api/v1/settings
- WS   /ws/chat/{chat_id}

## LM Studio
- GET  /api/v1/lm-studio/models
- POST /api/v1/lm-studio/load-model
- POST /api/v1/lm-studio/unload-model/{model_id}

## Statistics Endpoint

### GET /api/v1/chats/{chat_id}/stats

Returns real-time statistics for a chat.

**Response:**
```json
{
  "total_request_tokens": 2450,
  "total_response_tokens": 5120,
  "current_context_size": 3500,
  "context_window_size": 4096,
  "context_usage_percent": 85.4,
  "message_count": 20
}
```

**Notes:**
- current_context_size reflects active compression strategy
- Updates automatically after each message
- Can be polled via REST or received via WebSocket "done" message

## Settings Schema

### Fields:
- id: int (primary key)
- chat_id: int | null (null for global settings)
- system_prompt: str
- temperature: float (0.0-2.0)
- context_length: int (512-131072, default 4096)
- max_tokens: int (256-128000)
- strategy: ContextStrategy (sliding | sticky | truncate_middle | no_compression)
- facts_json: str (JSON object with extracted facts)
- summary_text: str (accumulated conversation summary)

### ContextStrategy Values:
- sliding: Sliding Window
- sticky: Sticky Facts
- truncate_middle: Truncate Middle
- no_compression: No Compression

## WebSocket Messages

### Server → Client

**done:**
```json
{
  "type": "done",
  "message_id": 123,
  "stats": {
    "total_request_tokens": 2450,
    "total_response_tokens": 5120,
    "current_context_size": 3500,
    "context_window_size": 4096,
    "context_usage_percent": 85.4
  }
}
```

**error (context overflow):**
```json
{
  "type": "error",
  "code": "CONTEXT_OVERFLOW",
  "detail": "Context size (5200 tokens) exceeds context window (4096 tokens). Please change compression strategy or reduce conversation length."
}
```
