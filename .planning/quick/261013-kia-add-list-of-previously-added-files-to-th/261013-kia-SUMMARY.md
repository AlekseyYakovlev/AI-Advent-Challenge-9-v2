# Quick 261013-kia: Existing files list in the add-files modal

**One-liner:** Owner-scoped `GET /api/v1/kb/{kb_id}/files` plus a "Уже в базе" list (name + size) in the «Добавить файлы» modal, with stale-response protection.

## Commits
- dfe924a: feat - GET endpoint, `KbFileOut` schema, API tests
- 76b4b7e: feat - modal markup, `loadKbAddExistingFiles`, UI guard tests

## Changes
- `agent/kb_schemas.py`: `KbFileOut` (id, filename, size_bytes, page_count; no stored_name/sha256)
- `agent/kb_api.py`: `list_kb_files`, ownership via `_get_owned_kb` (404 for missing/foreign), ordered by filename, id
- `tests/test_kb_documents_api.py`: order, keys, empty, ready, 404 missing/foreign, 401
- `ui/static/index.html`, `ui/static/app.js`: `#kb-add-existing` section; loader uses createElement/textContent, drops responses when `state.kbAddTargetId !== kbId`, Russian loading/empty/error lines
- `tests/test_kb_add_ui.py`: new ids and guard tests

## Verification (actually run)
- Task 1 set (documents_api, add_api, scoping, kb_api): 99 passed (RED first: 6 failed before implementation)
- Task 2 set (add_ui, folder_ui, static_js_syntax, modal_close_policy): 107 passed
- Full suite with scratch DB_PATH: 2962 passed, 13 skipped, 0 failed
- Not verified: no real browser check of the modal; only source-level guard tests.

## Deviations
None. (Skipped count is 13 versus the plan's 11 baseline; no failures.)

## Self-Check: PASSED
