# Phase 21: Project docs in RAG (Day 31) - Context

**Gathered:** 2026-10-13
**Status:** Ready for planning

<domain>
## Phase Boundary

The user can load the project's documentation (a new `README.md` and the `docs/` folder) into one knowledge base in a few clicks and keep it current by adding changed files. In scope: writing `README.md`, folder selection and an accumulated pending list in the knowledge base form, relative paths as source names, a higher file-count limit, and addition of files to an existing ready knowledge base with replacement of updated files (DOC-01, KBUP-01..06).

Out of scope here: the `/help` command and the "project documentation base" mark (Phase 23), MCP servers (Phase 22), code and schema files in a knowledge base (KBUP-07), removal of a single file from a base (KBUP-08).

</domain>

<decisions>
## Implementation Decisions

### README
- **D-01:** `README.md` is written in English, like the documents in `docs/`.
- **D-02:** It covers: what the app is, the directory structure (`agent/`, `ui/`, `shared/`, `scripts/`, `tests/`, `docs/`), install and run, the main `.env` settings, and links to the four documents in `docs/`. It describes the code as built; every claim is checked against the source.

### Folder selection
- **D-03:** Choosing a folder takes every supported file (`.pdf`, `.txt`, `.md`) from every nested directory. No directories are skipped by name: hidden and service directories (`.git`, `.planning`, `node_modules`, `__pycache__`) are NOT filtered out. The user chose this explicitly over a skip list.
- **D-04:** Files with unsupported extensions are skipped, and the form shows how many were taken and how many were skipped before the upload starts (KBUP-01).
- **D-05:** The source name of a file chosen through a folder is its relative path including the chosen folder's own name, with forward slashes (choosing `docs` gives `docs/API_SPEC.md`). A file chosen singly keeps its plain file name.

### Pending list
- **D-06:** Selections accumulate: each new choice of files or of a folder is appended to one pending list instead of replacing it. Every entry is shown with its source name and size and can be removed before the upload starts (KBUP-02).
- **D-07:** Choosing something whose source name is already in the pending list does not add a second entry.

### Addition to an existing base
- **D-08:** A ready knowledge base stays ready and searchable while files are being added; earlier fragments keep answering. The base's card shows the progress of the addition. The base is not moved back to the "indexing" status that blocks search.
- **D-09:** If an addition fails, the base stays exactly as it was before the addition, and the card shows the error message. No partial fragments from the failed addition remain searchable.
- **D-10:** Added files are indexed with the base's own strategy, chunk size, overlap and embedding model; the user is not asked for these again (KBUP-05).
- **D-11:** Addition is offered only for a base in the ready status.

### Repeated files
- **D-12:** A file whose source name already exists in the base and whose content changed replaces that file's fragments, without a confirmation prompt (KBUP-06).
- **D-13:** A file whose source name already exists and whose content is unchanged is skipped.
- **D-14:** After an addition the user sees a summary: added N, replaced M, unchanged K.
- **D-15:** A file that is in the base but absent from the new selection is left untouched; an addition never deletes documents.

### Limits
- **D-16:** The file-count limit of one upload is raised from 10 to a named constant in `agent/kb_limits.py` and is shown in the form; the per-file and total-size limits stay. Exceeding a limit rejects the whole upload with a clear message and stores nothing (KBUP-04).

### Claude's Discretion
- The exact new value of the file-count limit. Because D-03 takes everything from a folder, it must be large enough for a documentation tree (a value around 200 is the starting suggestion) while the 100 MB total cap keeps the request bounded.
- How the same content under two different source names is stored. Today `KbDocument` has a unique constraint on (`kb_id`, `sha256`); KBUP-03 requires two files with the same base name in different folders to be separate sources, so identity must move to the source name.
- The layout of the pending list, the "add folder" control and the "add files" control on a base's card, within the existing Tailwind look.
- How the index is updated on addition and replacement (rebuild or incremental), as long as D-08 and D-09 hold.
- Wording of the Russian UI texts and of the summary.

</decisions>

<specifics>
## Specific Ideas

- The demonstration path is: choose `README.md`, then choose the `docs` folder, see both in one list, create one base; later change a document and add it again to see "replaced 1".
- Sources under answers must read as project paths (`docs/ARCHITECTURE.md`), because Phase 23 shows them in `/help` answers.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and roadmap
- `.planning/REQUIREMENTS.md` — DOC-01, KBUP-01..06 and the Out of Scope table
- `.planning/ROADMAP.md` — Phase 21 goal and success criteria
- `.planning/PROJECT.md` — constraints (vanilla JS frontend, no new process model) and key decisions

### Knowledge base behaviour as built
- `docs/API_SPEC.md` — knowledge base routes and their limits
- `docs/ARCHITECTURE.md` — indexing pipeline, FAISS + SQLite storage, events
- `docs/USER_GUIDE.md` — current upload flow described for users
- `docs/TESTING_GUIDE.md` — required scenario coverage for knowledge base tests
- `.planning/milestones/v3.0-ROADMAP.md` — Phase 13 (knowledge base indexing) decisions

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `agent/kb_api.py` — `create_kb` (multipart upload, validation, 202 + background job), `_clean_filenames` (currently reduces every upload name to its base name), `_store_uploads` (streams to disk, computes sha256, enforces size limits)
- `agent/kb_indexer.py` — `spawn_index_job`, `run_index_job`, `_parse_documents`, `_embed_all`, `_build_and_write_index`, `_fail`, `recover_orphaned_kb_jobs`
- `agent/kb_limits.py` — `MAX_FILES = 10`, `MAX_FILE_BYTES`, `MAX_TOTAL_BYTES`, `ALLOWED_EXTENSIONS`
- `shared/models.py` — `KnowledgeBase` (status, progress counters), `KbDocument` (unique on `kb_id` + `sha256`, `filename`), `KbChunk` (`source`, `title`, id doubles as the FAISS vector id)
- `ui/static/index.html` — `<input type="file" id="kb-files" multiple accept=".pdf,.txt,.md">`
- `ui/static/app.js` — `renderKbFileList`, `submitKbCreate`, `buildKbCard`, `kbProgressBar`, `kbStatusChip`, `bindKbUi`

### Established Patterns
- Uploads are validated completely before anything is stored; a rejected upload leaves no rows and no files.
- Indexing runs as a background job with throttled progress events over `/ws/events`; the card re-renders from those events.
- Frontend text is set with `textContent`; no HTML insertion without `DOMPurify.sanitize()`.
- Frontend behaviour is guarded by Node-free source tests (`tests/test_*_ui.py`) and `tests/test_static_js_syntax.py`.
- Browser checks run on an isolated copy at ports 18000/18001 (`scripts/e2e_kb_playwright.py` is the closest script).

### Integration Points
- A new route for addition to an existing base next to `create_kb` in `agent/kb_api.py`.
- `KbChunk.source` feeds search results, sources under answers and citations (`agent/rag.py`, `agent/rag_cite.py`), so the relative path must flow through unchanged.
- Existing tests: `tests/test_kb_api.py`, `tests/test_kb_indexer.py`, `tests/test_kb_storage.py`, `tests/test_kb_lifecycle.py`, `tests/kb_helpers.py`.

</code_context>

<deferred>
## Deferred Ideas

- Code and schema files (`.py`, `.json`, `.yaml`, `.sql`) in a knowledge base — KBUP-07, later in Week 7.
- Removing a single file from a base — KBUP-08.
- A skip list for service directories — rejected for now by the user (D-03); revisit if choosing a project root proves noisy.
- Automatic re-indexing of project documents at start-up — out of scope for the milestone.

</deferred>

---

*Phase: 21-project-docs-in-rag-day-31*
*Context gathered: 2026-10-13*
