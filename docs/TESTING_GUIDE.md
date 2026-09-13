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