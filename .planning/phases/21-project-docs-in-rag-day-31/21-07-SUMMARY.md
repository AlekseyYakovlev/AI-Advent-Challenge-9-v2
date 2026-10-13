---
phase: 21-project-docs-in-rag-day-31
plan: 07
subsystem: docs-and-browser-verification
tags: [docs, readme, playwright, kb, folder-upload]
requires: ["21-02", "21-04", "21-06"]
provides:
  - "Documents describing folder upload, pending list, relative-path sources, new limit, addition and replacement"
  - "tests/test_docs_kb_upload.py guard against stale upload rules"
  - "scripts/e2e_kb_folder_playwright.py isolated-copy browser check"
affects: []
tech-stack:
  added: []
  patterns: ["browser-encoded FormData read in the page when Playwright cannot read a File-part body"]
key-files:
  created: [scripts/e2e_kb_folder_playwright.py, tests/test_docs_kb_upload.py]
  modified: [docs/API_SPEC.md, docs/ARCHITECTURE.md, docs/USER_GUIDE.md, docs/TESTING_GUIDE.md, README.md, scripts/e2e_kb_playwright.py]
decisions:
  - "Multipart file names are verified from the FormData the browser itself encodes (Playwright returns an empty body for File parts)"
  - "README section names the panel in English because tests/test_readme.py forbids Cyrillic in README.md"
metrics:
  tasks: 2
  completed: 2026-10-13
---

# Phase 21 Plan 07: Documents and browser verification Summary

The four documents and the README now describe folder upload, the pending list, relative-path sources, the 200-file limit and addition/replacement as built, a guard test fails if a stale rule or wrong limit returns, and a new isolated-copy Chromium script confirmed part A (no LM Studio) while part B (end-to-end with LM Studio) was blocked and is not verified.

## Commits
- c47f9e4: `scripts/e2e_kb_folder_playwright.py` added; `scripts/e2e_kb_playwright.py` step 4 no longer asserts the removed duplicate rule (now: the same file chosen twice is listed once)
- b3df3f8: API_SPEC, ARCHITECTURE, USER_GUIDE, TESTING_GUIDE, README and `tests/test_docs_kb_upload.py`

(The script was committed first so that the guard test, which requires the script file to exist, passes at every commit.)

## Verification (observed)
- `pytest tests/test_docs_kb_upload.py tests/test_docs_day30.py tests/test_readme.py -q`: 70 passed.
- Full suite `DB_PATH=test_plan07.db pytest tests/ -q`: 2759 passed, 11 skipped, 0 failed.
- `python -m py_compile` of both scripts: ok. `grep -c "уже добавлен" scripts/e2e_kb_playwright.py`: 0. `grep -cE "(^|[^0-9])800[01]([^0-9]|$)" scripts/e2e_kb_folder_playwright.py`: 0.

## Browser run: `python scripts/e2e_kb_folder_playwright.py`

Exit code: **3** (part A passed; part B blocked because LM Studio on `localhost:1234` was not reachable). Playwright 1.63.0, Chromium headless, isolated copy on ports 18000/18001; the ports were free again at the end; the user's app on 8000/8001 was not contacted.

```
PASS: isolated copy patched to the isolated ports
PASS: scratch database and KB storage are isolated from app.db
PASS: isolated app instance is up
PASS: A1 limits hint equals GET /api/v1/kb/limits
PASS: A2 one file chosen gives one list entry named README.md
PASS: A3 directory accepted on a webkitdirectory input - set_input_files; playwright 1.63.0
PASS: A3 folder entries carry relative paths and the taken/skipped report
PASS: A4 choosing the same folder again adds nothing
PASS: A5 removal and re-adding update the list and the header
PASS: A6 the multipart request carries relative paths as file names - status=422; body source=browser-encoded FormData; missing=[]
PASS: A7 a folder over the limit turns the header red and disables submit
PASS: A8 no browser page errors in part A
PASS: isolated ports are free again
summary: 13 passed, 0 failed
BLOCKED: part B skipped (LM Studio or the embedding model is unavailable)
```

Part B (B1-B7: stored names, search, add flow, `Добавлено: 1, заменено: 1, без изменений: 4`, card stays `готово`) was **not run**. Its code was written but never executed, so it may need adjustment on first real run. It is recorded as "part B blocked", not passed. Human check remains: run the script on a machine with LM Studio and the nomic embedding model and expect exit code 0.

## Research assumptions
- **A1 (third argument of `FormData.append` becomes the multipart file name): confirmed at the browser-encoder level only.** Playwright's `request.post_data_buffer` is empty for a body with File parts in Chromium (observed: `body_len=0`), so the script reads the multipart text the browser encodes from the same `FormData` (`new Response(formData).text()` through a `fetch` wrapper). It contains `filename="docs/a/README.md"`, `filename="docs/b/README.md"`, `filename="docs/.hidden/secret.md"` and `filename="README.md"`. The server-side confirmation (stored names, check B2) is part of the blocked part B.
- **A2 (shape of `webkitRelativePath`): confirmed.** Entries read `docs/GUIDE.md`, `docs/a/README.md` etc. with forward slashes, root folder name first, no backslashes.
- **A3 (Playwright sets a directory on a `webkitdirectory` input): confirmed.** `page.set_input_files("#kb-folder", <directory>)` worked directly on 1.63.0 (no file-chooser or page-side fallback used); the hidden directory `.hidden` was included.

## Interpretation for the user to confirm

KBUP-03 and roadmap criterion 3 mention a "knowledge base file list". This phase reads it as the pending list of the create form and the add modal; test-search results and answer sources show the same relative-path names, and check B2 (blocked, see above) is meant to prove the stored names. No per-base document list on the card or in the API was added and the documents do not describe one; a per-base list would be new scope. Please confirm this reading.

## State of the conditional files
- `tests/test_readme.py`: untouched (the new heading sits before `## Tests`, so the heading order assertion still holds).
- `ui/static/app.js`: untouched.
- `agent/kb_api.py`: untouched.

## Product defects found
None. No part A check failed because of product code. (An early A6 failure was a script limitation: Playwright cannot read multipart bodies with File parts; fixed in the script, not by weakening the check.)

## Deviations from Plan

**1. [Rule 3 - Blocking] README wording in English only**
- The plan says the README names the "База знаний" panel, but `tests/test_readme.py::test_readme_exists_in_english` forbids Cyrillic. The README says "Knowledge base panel" instead. Commit b3df3f8.

**2. [Rule 1 - Bug in the plan's method] A6 reads the browser-encoded FormData**
- `post_data_buffer` is empty for File parts, so the check reads the multipart text produced by the browser from the same `FormData`. Check content (four required `filename="..."` values) is unchanged. Commit c47f9e4.

**3. Commit order**
- The script commit precedes the documents commit because `test_testing_guide_lists_new_files` requires the script to exist.

## Assumption Drift (advisory)
- Found during: Task 2. Planned: Playwright's `post_data_buffer` shows the multipart request body (A1). Actual: empty for File-part bodies in Chromium. Why: DevTools does not expose blob-backed bodies; the in-page encoder is used instead.

## Known Stubs
None.

## Threat Flags
None.

## Self-Check: PASSED
- scripts/e2e_kb_folder_playwright.py, tests/test_docs_kb_upload.py, docs/*.md and README.md exist; commits c47f9e4 and b3df3f8 exist in git log.
