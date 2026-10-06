# Quick 261006-lrs: MCP disable toggle fix (backlog 999.12) Summary

Persistent `McpServerConfig.auto_connect` flag: "Отключить" sets it False (survives restart), "Подключить" restores True, lazy auto-connect skips rows with it off.

## Commits
- 749f4e4: model field, idempotent migration, connect/disconnect routes, auto-connect filter, tests
- c30a3e6: UI hint "Отключён вручную", ROADMAP 999.12 entry and phase dir removed

## Verification (observed)
- `pytest tests/test_mcp_auto_connect.py tests/test_mcp_api.py tests/test_mcp_tools.py tests/test_mcp_chat_ws.py -q`: 61 passed.
- Full `pytest tests/ -q`: 1881 passed, 11 skipped, 1 failed (`tests/test_titles_ws.py::test_failed_turn_never_schedules_title`, sqlalchemy error). Re-running `tests/test_titles_ws.py` alone: 15 passed. Looks like a flaky pre-existing issue unrelated to MCP; not investigated further.
- ROADMAP/phase dir cleanup check: passed.
- Not verified: migration against a real existing app.db and manual UI check (follows existing migration pattern only); tests ran against fresh DB.

## Deviations
None. Tests were written together with the implementation rather than a separate RED commit.
Out of scope as planned: tool-schema tokens in usage meter.
