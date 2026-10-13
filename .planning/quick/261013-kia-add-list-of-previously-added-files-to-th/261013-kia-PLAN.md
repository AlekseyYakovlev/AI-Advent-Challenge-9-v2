---
phase: quick-261013-kia
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - agent/kb_schemas.py
  - agent/kb_api.py
  - tests/test_kb_documents_api.py
  - ui/static/index.html
  - ui/static/app.js
  - tests/test_kb_add_ui.py
autonomous: true
requirements: [QUICK-261013-kia]

must_haves:
  truths:
    - "When a user opens the «Добавить файлы» modal for a knowledge base, the modal shows the files already in that base (name + size), sorted by name"
    - "An empty base, or a failed fetch, shows a short Russian line instead of a list; the add flow keeps working either way"
    - "The list belongs only to the base the modal was opened for: opening another base never shows the previous base's files (stale response is dropped)"
    - "Another user's base id returns 404 from the list endpoint (never 403), unauthenticated requests get 401"
    - "File names are inserted as text (textContent), never as HTML"
  artifacts:
    - path: "agent/kb_api.py"
      provides: "GET /api/v1/kb/{kb_id}/files listing the base's KbDocument rows"
      contains: "list_kb_files"
    - path: "agent/kb_schemas.py"
      provides: "KbFileOut response model"
      contains: "class KbFileOut"
    - path: "tests/test_kb_documents_api.py"
      provides: "API tests for the list endpoint (shape, order, scoping, 401)"
    - path: "ui/static/index.html"
      provides: "#kb-add-existing section inside #kb-add-modal"
      contains: "kb-add-existing-list"
    - path: "ui/static/app.js"
      provides: "loadKbAddExistingFiles called from openKbAddModal"
      contains: "function loadKbAddExistingFiles"
  key_links:
    - from: "ui/static/app.js::openKbAddModal"
      to: "/api/v1/kb/{id}/files (GET)"
      via: "loadKbAddExistingFiles(kb.id) -> fetch with credentials: 'include'"
      pattern: "loadKbAddExistingFiles\\("
    - from: "agent/kb_api.py::list_kb_files"
      to: "_get_owned_kb"
      via: "ownership check before selecting KbDocument rows"
      pattern: "_get_owned_kb"
---

<objective>
Show the previously added files of a knowledge base inside the «Добавить файлы» modal (built in phase 21-06).

Investigation result: no endpoint lists a base's documents. `KbOut` only carries `file_count`; `GET /api/v1/kb/{kb_id}` returns `KbOut`; `POST /api/v1/kb/{kb_id}/files` only adds. So a small read-only endpoint `GET /api/v1/kb/{kb_id}/files` is added (same path as the add route, different method), then the modal fetches and renders it.

Output: new GET route + response schema + API tests; modal markup section + JS loader + guard tests.
</objective>

<execution_context>
@C:/Users/Aleksey/.claude/plugins/cache/buildomator/bm/4.7.3/workflows/execute-plan.md
</execution_context>

<context>
@./CLAUDE.md
@.planning/phases/21-project-docs-in-rag-day-31/21-06-SUMMARY.md

<interfaces>
From agent/kb_api.py (existing):
- `router = APIRouter(prefix="/api/v1/kb", tags=["kb"])`
- `async def _get_owned_kb(session: AsyncSession, kb_id: int, user_id: int) -> KnowledgeBase` — raises the 404 `MSG_KB_NOT_FOUND` for missing AND foreign ids.
- Read-only route pattern to copy: `get_kb_route` (`@router.get("/{kb_id}", response_model=KbOut)`, deps `session: AsyncSession = Depends(get_session)`, `current_user: User = Depends(get_current_user)`; GET routes have no origin/content-type dependencies).
- Already imported: `select` (sqlmodel), `KbDocument`, `KnowledgeBase`, `User`.
- `POST /{kb_id}/files` = `add_files_route` (keep untouched).

From agent/kb_schemas.py: `class KbOut(BaseModel)`, `def kb_out(kb) -> KbOut`.

From shared/models.py `KbDocument`: `id`, `kb_id`, `filename` (source name, may be a relative path with `/`), `sha256`, `size_bytes`, `page_count: int | None`, `stored_name` (internal on-disk name — must NOT be exposed).

From tests/kb_helpers.py: `seed_kb(user_id, files: dict[str, str|bytes] | None = None, *, status=KbStatus.QUEUED, ...) -> int` (default files `{"doc.txt": RU_TEXT}`), `seed_user`, `document_rows(kb_id)`. Tests import it as `from kb_helpers import ...`.
Fixtures (tests/conftest.py): `authenticated_client` (has `.seeded_user_id`), `second_authenticated_client`, `client` (unauthenticated). Style reference: tests/test_kb_scoping.py (`test_foreign_kb_returns_404`, `test_unauthenticated_requests_get_401`).

From ui/static/app.js:
- `function openKbAddModal(kb, opener)` (~line 4503): sets `state.kbAddTargetId = kb.id`, resets pending list, title, params, shows modal, then `loadKbLimits().then(...)`.
- `function closeKbModal(modalId)`: for `'kb-add-modal'` sets `state.kbAddTargetId = null`.
- `function formatKbFileSize(bytes)` -> "N КБ"/"N.N МБ" (reuse it).
- `AGENT_BASE`, `$(id)`; fetch pattern with `credentials: 'include'` and 401 -> `window.location.href = '/static/login.html?expired=1'` (see `submitKbAdd`).

From tests/test_kb_add_ui.py: `ADD_MODAL_IDS` list (each id must appear exactly once in index.html), `_add_modal()` returns modal markup, helpers `_app_js_source`, `_function_source`, `_index_html` from tests/test_limits_ui.py, `check_js_balance` from tests/test_static_js_syntax.py.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: GET /api/v1/kb/{kb_id}/files endpoint + API tests</name>
  <files>agent/kb_schemas.py, agent/kb_api.py, tests/test_kb_documents_api.py</files>
  <behavior>
    - Seeded base with files {"b.txt", "a/c.md", "A.txt"} -> 200, JSON list of objects with exactly keys id, filename, size_bytes, page_count, ordered by filename then id (SQL default ordering of `KbDocument.filename`), size_bytes equals stored byte length.
    - Response objects never contain stored_name or sha256.
    - Base with no documents (seed_kb(user_id, {})) -> 200 and [].
    - Works for any status (queued/ready) — listing is read-only.
    - Missing id 9999 -> 404 with detail MSG_KB_NOT_FOUND; another user's base via second_authenticated_client -> 404 (not 403).
    - Unauthenticated `client` -> 401.
    - Existing POST /api/v1/kb/{kb_id}/files behavior untouched (existing test_kb_add_api.py still passes).
  </behavior>
  <action>
In agent/kb_schemas.py add `class KbFileOut(BaseModel)` (docstring "A source file of a knowledge base as shown to the client.") with fields `id: int`, `filename: str`, `size_bytes: int`, `page_count: int | None`. Deliberately exclude `stored_name` and `sha256` (internal storage details).

In agent/kb_api.py import `KbFileOut` alongside `KbOut`, and add a route placed directly after `get_kb_route`: `@router.get("/{kb_id}/files", response_model=list[KbFileOut])` named `async def list_kb_files(kb_id: int, session=Depends(get_session), current_user=Depends(get_current_user)) -> list[KbFileOut]` with full type hints and a one-line docstring. Body: `await _get_owned_kb(session, kb_id, current_user.id)` first (404 for missing/foreign, per the project's never-403 rule), then select `KbDocument` where `KbDocument.kb_id == kb_id` ordered by `KbDocument.filename, KbDocument.id`, return `KbFileOut(...)` per row. No origin / content-type dependency (it is a GET, same as get_kb_route). No logging needed beyond what exists (read-only); if you add one, use `logger.debug("kb_files_listed", kb_id=..., count=...)`.

Create tests/test_kb_documents_api.py (module docstring, type hints, `from kb_helpers import seed_kb`, `from agent.kb_api import MSG_KB_NOT_FOUND`) covering every behavior bullet above. Write the tests first, run them (fail with 405 since only POST exists on that path), then implement.
  </action>
  <verify>
    <automated>pytest tests/test_kb_documents_api.py tests/test_kb_add_api.py tests/test_kb_scoping.py tests/test_kb_api.py -q</automated>
  </verify>
  <done>GET /api/v1/kb/{id}/files returns the owned base's documents (id, filename, size_bytes, page_count) sorted by filename; 404 for missing/foreign, 401 unauthenticated; all listed test files pass.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: "Уже в базе" list in the add-files modal + guard tests</name>
  <files>ui/static/index.html, ui/static/app.js, tests/test_kb_add_ui.py</files>
  <behavior>
    - index.html: #kb-add-modal contains, once each, ids kb-add-existing, kb-add-existing-count, kb-add-existing-list, kb-add-existing-status; the section appears before the file pickers in the modal markup.
    - app.js: `async function loadKbAddExistingFiles(kbId)` exists; it fetches `/api/v1/kb/${kbId}/files` with `credentials: 'include'`, handles 401 redirect, drops the response when `state.kbAddTargetId !== kbId`, uses textContent (no innerHTML) and reuses `formatKbFileSize`.
    - `openKbAddModal` calls `loadKbAddExistingFiles(kb.id)`.
    - Russian texts present: "Уже в базе", "В базе пока нет файлов.", "Загрузка списка файлов…", "Не удалось загрузить список файлов."
    - JS stays balanced (check_js_balance).
  </behavior>
  <action>
index.html: inside `#kb-add-form`, as the first child block (before the "Файлы (PDF, TXT, MD)" block), add a `<div id="kb-add-existing">` with a header line `<span class="block text-sm text-slate-400 mb-1">Уже в базе <span id="kb-add-existing-count"></span></span>`, a `<ul id="kb-add-existing-list" class="space-y-1 text-xs text-slate-300 max-h-40 overflow-y-auto">` and a `<p id="kb-add-existing-status" class="text-xs text-slate-500" role="status">`. Tailwind classes consistent with the existing `#kb-add-file-list`. Do not add any Escape/backdrop close handler (close-policy test in tests/test_modal_close_policy.py must keep passing).

app.js: add `async function loadKbAddExistingFiles(kbId)` right after `openKbAddModal`. Steps: clear `kb-add-existing-list` (replaceChildren()), clear count, set status "Загрузка списка файлов…"; `fetch(`${AGENT_BASE}/api/v1/kb/${kbId}/files`, { credentials: 'include' })`; on 401 redirect to '/static/login.html?expired=1' like submitKbAdd; after every await, return early if `state.kbAddTargetId !== kbId` (modal closed or reopened for another base — stale response must not render). On !ok or network error (try/catch) set status "Не удалось загрузить список файлов." (adding files still works). On success: if the array is empty, status "В базе пока нет файлов."; otherwise clear status, set count to `(${files.length})`, and for each file create an `<li class="flex justify-between gap-2">` with a `<span class="truncate">` whose textContent = filename and `title` = filename, plus a `<span class="shrink-0 text-slate-500">` with textContent = `formatKbFileSize(file.size_bytes)`. Build only with createElement/textContent — never innerHTML (filenames are user-controlled; per CLAUDE.md no unsanitized HTML). In `openKbAddModal` call `loadKbAddExistingFiles(kb.id);` (not awaited) after showing the modal. Keep everything else in openKbAddModal unchanged. The list is refreshed every open, so after a successful addition the next open shows the new files — no extra wiring in submitKbAdd needed.

tests/test_kb_add_ui.py: append the four new ids to `ADD_MODAL_IDS`; add tests: (a) the new ids sit inside `_add_modal()` and `kb-add-existing` precedes `kb-add-files` in it; (b) `_function_source(src, "openKbAddModal")` contains `loadKbAddExistingFiles(kb.id)`; (c) `_function_source(src, "loadKbAddExistingFiles")` contains `/api/v1/kb/${kbId}/files`, `credentials: 'include'`, `state.kbAddTargetId !== kbId`, `formatKbFileSize`, `textContent`, `expired=1`, and does NOT contain `innerHTML`; (d) the four Russian strings are present in app.js/index.html; (e) the function source passes `check_js_balance` (use it the same way existing tests in this file do).
  </action>
  <verify>
    <automated>pytest tests/test_kb_add_ui.py tests/test_kb_folder_ui.py tests/test_static_js_syntax.py tests/test_modal_close_policy.py -q</automated>
  </verify>
  <done>Opening «Добавить файлы» lists the base's existing files with sizes (or an empty/error line); guard tests pass; close policy unchanged.</done>
</task>

<task type="auto">
  <name>Task 3: Full suite regression</name>
  <files>(none — verification only)</files>
  <action>Run the full test suite with an isolated DB (e.g. DB_PATH=test_quick_kia.db, as the 21-06 run did) to confirm no regressions in KB, scoping or static-guard tests. Fix any failure caused by this change. Do not start or kill the user's app on ports 8000/8001; any optional browser check goes on an isolated copy at 18000/18001.</action>
  <verify>
    <automated>pytest tests/ -q</automated>
  </verify>
  <done>Full suite passes (previous baseline: all passed, 11 skipped) with the new tests included.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| browser -> agent REST | kb_id path param and session cookie are untrusted |
| DB -> DOM | filenames are user-supplied source names rendered in the modal |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-kia-01 | Information disclosure | list_kb_files | mitigate | `_get_owned_kb` ownership check before the query; foreign id -> 404, test with second_authenticated_client |
| T-kia-02 | Information disclosure | KbFileOut | mitigate | stored_name / sha256 excluded from the schema; test asserts keys are exactly id, filename, size_bytes, page_count |
| T-kia-03 | Tampering (XSS) | loadKbAddExistingFiles | mitigate | DOM built via createElement + textContent only; guard test forbids innerHTML in the function |
| T-kia-04 | Spoofing | list_kb_files | mitigate | get_current_user dependency; unauthenticated -> 401 test |
</threat_model>

<verification>
- pytest tests/test_kb_documents_api.py tests/test_kb_add_ui.py -q pass
- pytest tests/ -q passes
</verification>

<success_criteria>
The «Добавить файлы» modal shows the list of files already in the selected knowledge base, served by an owner-scoped GET endpoint, with backend and UI guard tests.
</success_criteria>

<output>
Create `.planning/quick/261013-kia-add-list-of-previously-added-files-to-th/261013-kia-SUMMARY.md` when done
</output>
