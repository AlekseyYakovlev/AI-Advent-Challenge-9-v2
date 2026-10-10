---
phase: 17-mini-chat-with-rag-and-task-memory-day-25
plan: 05
subsystem: rag-eval
tags: [rag, evaluation, websocket, dialog, task-memory]
requires:
  - phase: 17-01
    provides: TASK_MEMORY_ENABLED flag and task-memory snapshot
  - phase: 17-02
    provides: frozen tests/fixtures/rag/dialog_scenarios.json
provides:
  - "`python scripts/rag_eval.py dialog` multi-turn driver over the real WebSocket chat on an isolated copy"
  - "pure per-turn checks (turn_verdict, memory_check, article_in_sources, compute_checks, assert_isolated)"
affects: [17-08, 17-09, 17-10]
tech-stack:
  added: [websockets>=15]
  patterns: [isolated app copy with env-flag baseline, resume by skipping existing raw files]
key-files:
  created: [scripts/rag_dialog.py, tests/test_dialog_eval.py]
  modified: [scripts/rag_eval.py, requirements.txt]
key-decisions:
  - "Baseline is an env flag (TASK_MEMORY_ENABLED=false) on a restarted isolated app; no request field, no UI switch"
  - "Implementation lives in scripts/rag_dialog.py; rag_eval.py only registers and dispatches the sub-command"
metrics:
  tasks: 3
  files: 5
completed: 2026-10-10
---

# Phase 17 Plan 05: Scenario dialog driver Summary

Multi-turn driver that plays the frozen scenarios through `WS /ws/chat/{chat_id}` on an isolated copy (UI :18000, Agent :18001) in a main and a baseline (`TASK_MEMORY_ENABLED=false`) configuration and records per-turn answer, whole `done.rag`, task-memory snapshot and automatic checks.

## Task 1: package legitimacy checkpoint

Resolved before this continuation. The user's verbatim answer to the question about adding `websockets` was: **"Approved (Recommended)"**, meaning exactly `websockets>=15` is added to requirements.txt and no other package. `requirements.txt` gained that single line (one added line in the diff) and nothing else.

## Commits

- 4ab36c2 `feat(17-05)`: requirements line, pure checks, isolation guards, loader, 17 tests
- 8d3cf5b `feat(17-05)`: runner (preflight, scratch copy, app start/teardown, login, WS turn loop, run_meta.json), `dialog` sub-command in rag_eval.py, 15 more tests

## What was built

- `scripts/rag_dialog.py`: `turn_verdict`, `memory_check`, `article_in_sources`, `compute_checks`, `assert_isolated`, `load_scenarios`; runner `run_config`, `rag_settings`, `drain_turn` (token/done/error frames, timeout and closed socket become error records), `preflight_dialog`, `prepare_run` (tree copy via `e2e_kb_playwright.prepare_copy`, DB copied through a read-only SQLite backup, KB directory copied, random owner password written only in the copy), `start_app` (`asyncio.create_subprocess_exec`), `run_scenario`, `dialog_command`.
- `scripts/rag_eval.py`: `_add_dialog_parser`, `dialog_command` dispatch; other sub-commands untouched.
- Exit codes: 0 all files written, 1 scenario aborted, 2 preflight refusal (bad `--runs`/`--scenarios`, draft fixture, missing source DB/KB, busy ports, LM Studio unreachable, unsafe ports/DB path).
- Output: `raw/{run}_{scenario}.json` and `run_meta.json` (fixture sha256 of bytes and of LF-normalised bytes, configuration, per-run flag, UTC timestamps, per-scenario counts, git commit). Existing raw files are skipped unless `--force`. No cookie, password or key is stored.

## Verification actually run

- `python -m pytest tests/test_dialog_eval.py tests/test_dialog_fixture.py -q`: 34 passed (after Task 2).
- `python -m pytest tests/test_dialog_eval.py tests/test_rag_eval.py tests/test_rag_eval_cite.py -q`: 73 passed (after Task 3).
- `python scripts/rag_eval.py dialog --help` lists `--runs`, `--scenarios`, `--check`, `--turn-timeout`, `--history-turns`, `--force`.
- Greps: `TASK_MEMORY_ENABLED` appears twice in rag_dialog.py; no `multiprocessing`/`os.fork`; no 8000/8001 literals; `create_subprocess_exec` present once; no manual-verdict fields.

## Not verified

- No live run: `--check` and the real runs were not executed. This worktree has no `eval_out/day23/eval.db` / `eval_kb` (they exist only in the main checkout) and LM Studio was not started. The app start, login, KB verification and the real WebSocket loop are therefore covered only by unit tests with fake sockets and canned frames. Plan 17-09 performs the live runs.
- `history_turns` is sent in the per-chat RAG PUT; the `RagConfigIn` model in this checkout does not yet list it (later plans of the phase add it), so a live run depends on those plans.

## Deviations from Plan

- [Rule 3 - Blocking] The worktree started at an older commit than the wave base; it was reset to the specified base `c1e5d69` per the startup check (no local work lost, tree was clean).
- `.planning/HANDOFF.json` showed as modified in the worktree from an external source; it was not touched or staged.

## Assumption Drift (advisory)

None material.

## Known Stubs

None.

## Threat Flags

None. Mitigations T-17-SC, T-17-22 to T-17-25 implemented as planned (checkpoint approval, `assert_isolated`, read-only DB copy, no secrets in output, every turn recorded).

## Self-Check: PASSED

- scripts/rag_dialog.py, tests/test_dialog_eval.py present; commits 4ab36c2 and 8d3cf5b present.
