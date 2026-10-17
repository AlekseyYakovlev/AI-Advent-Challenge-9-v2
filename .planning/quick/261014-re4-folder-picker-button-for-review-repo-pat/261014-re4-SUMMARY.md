# Quick 261014-re4: Folder picker button for review repo path

Settings -> "Ревью Pull Request" now has a "Выбрать папку…" button that fills "Путь к репозиторию" from a native Windows folder dialog opened by the agent through PowerShell.

## Commits
- cabc232: pick-folder route, agent/folder_picker.py, tests/test_folder_picker.py
- 7263b80: button in index.html, pickReviewFolder() in app.js, UI source test, API_SPEC.md and USER_GUIDE.md

## What was built
- `agent/folder_picker.py`: fixed PowerShell FolderBrowserDialog script passed via -EncodedCommand, spawned with `asyncio.create_subprocess_exec`, 300 s timeout with `_kill_tree`, error codes TIMEOUT/UNAVAILABLE/UNSUPPORTED. The chosen path is never logged.
- `POST /api/v1/reviews/pick-folder`: session only (API key 403, anonymous 401), origin and JSON content type checked, global `_pick_lock` (409 FOLDER_PICKER_BUSY), 504/503/501 mapping. Stores nothing.
- Frontend: button after the path input, value written via `.value`, no auto-save, no innerHTML.

## Verification (actually run)
- `pytest tests/test_review_settings_ui.py tests/test_static_js_syntax.py tests/test_folder_picker.py tests/test_review_api.py tests/test_port_literals.py -q`: 82 passed.
- No real dialog was opened; all tests use a fake process or a mocked pick_folder. Ports 8000/8001 untouched.
- Not verified: the real Windows dialog (needs a GUI). The full `pytest tests/` run exceeded the 10-minute foreground limit and was not observed to completion.

## Deviations
None.
