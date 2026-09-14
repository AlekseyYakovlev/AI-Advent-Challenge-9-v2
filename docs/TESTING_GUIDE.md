# Testing Guide

## Required Tests
- test_database.py: WAL, retry_on_locked_db, CASCADE
- test_settings_fallback.py: global → per-chat inheritance
- test_cascade_delete.py: delete chat → cleanup in-memory caches
- test_lm_studio_client.py: load/unload/timeout scenarios
- test_concurrent_ws.py: 5 parallel WS messages, no IntegrityError
- test_ws_security.py: origin validation, rate limiting, idle timeout
- test_supervisor.py: agent crash → restart within 5 seconds

## Fixtures
- Use conftest.py for shared fixtures
- Separate test_app.db for each test session
- respx for HTTP mocking

## Strategy Tests

### test_no_compression_blocks_on_overflow
- Set strategy to "no_compression"
- Set context_length to 100
- Send 10 long messages (200+ tokens each)
- Verify ContextOverflowError is raised
- Verify user message is deleted from database
- Verify error sent to WebSocket with code "CONTEXT_OVERFLOW"

### test_sliding_window_preserves_database
- Set strategy to "sliding"
- Set context_length to 500
- Send 20 long messages
- Verify all 20 messages preserved in database
- Verify only recent messages sent to LLM (check logs)

### test_sticky_facts_creates_summary
- Set strategy to "sticky"
- Set context_length to 500
- Send 20 long messages
- Verify all messages preserved in database
- Verify summary_text field populated in settings
- Verify summary is in English (check logs)

### test_truncate_middle_preserves_structure
- Set strategy to "truncate_middle"
- Set context_length to 500
- Send 25 messages
- Verify all 25 messages preserved in database
- Verify first 5 and last 10 messages sent to LLM
- Verify middle summary generated (check logs)

### test_strategy_change_unblocks_input
- Set strategy to "no_compression" with small context_length
- Trigger overflow
- Verify input blocked
- Change strategy to "sliding"
- Verify input unblocked
- Verify new messages work normally

### test_statistics_endpoint
- Create chat with messages
- Call GET /api/v1/chats/{chat_id}/stats
- Verify all fields present
- Verify current_context_size reflects strategy
- Verify context_usage_percent calculated correctly
