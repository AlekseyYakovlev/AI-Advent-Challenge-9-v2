---
phase: 14-first-rag-query-day-22
plan: 05
subsystem: rag-eval
tags: [rag, eval, hit-at-k, scripts]
requires: [14-01, 14-02]
provides:
  - scripts/rag_eval.py with build-kbs and run subcommands, hit@k scoring and table rendering
affects: [14-07]
key-files:
  created: [scripts/rag_eval.py, tests/test_rag_eval.py]
requirements-completed: [RAG-07]
completed: 2026-10-03
---

# Phase 14 Plan 05: Offline RAG evaluation runner Summary

Scratch-DB KB builder (one KB per embedder, identical strategy/size/overlap) and a runner that scores retrieval hit@1/3/5/k against expected file+article and collects answers with and without RAG at temperature 0, using the same `agent.rag` block/merge code as the chat.

## Tasks

| Task | Commit | Verification |
|------|--------|--------------|
| 1. Scoring and rendering core | 936484d | `pytest tests/test_rag_eval.py`: 10 passed |
| 2. build-kbs and run subcommands | 0d8e79e | `pytest tests/test_rag_eval.py`: 15 passed; `run --help`, `build-kbs --help`, `--help` exit 0 |

## Behavior notes

- Raw files: `raw/off_none_{qid}.json` and `raw/rag_{label}_{qid}.json`; tables and run_meta.json are always re-rendered from all raw files, so per-embedder `--only-kb` runs into one `--out` accumulate (tested).
- For synthesis questions the hit@k columns require every expected source; the lenient any-source flag is stored as `any_hitk` in raw files.
- `run_meta.json` records fixture sha256, provider, model, top_k, modes, temperature 0.0, context/max tokens, per-KB chunking/embedder/dim and an `identical_chunking` flag. The DeepSeek key is never printed or stored.
- `build-kbs` and `run` set `DB_PATH` and `KB_STORAGE_DIR` to scratch locations (default `eval_out/day22/eval.db`, `eval_out/day22/eval_kb`) before importing project modules, so `app.db` is never touched. An LLM HTTP error is recorded in the raw file (empty answer) and the run continues.

## Deviations from Plan

- [Rule 3 - Blocking] Worktree HEAD was not on the expected base; reset to 84ff870 per the startup check before any work.
- `build-kbs` always creates fresh KBs (no reuse of existing `day22-*` KBs on re-run); `--only-kb` preflight checks only the selected label.
- The tests use `kb_helpers` fake embedders, so scores there are structural only (fixed-strategy chunks have no article sections).

## Not verified

- `build-kbs` and `run` were not executed live (needs LM Studio and the corpus PDFs); only `--help` and the fake end-to-end tests were run. Live execution belongs to 14-07.
- Full `pytest tests`: 1256 passed, 1 skipped, 1 failed (`tests/test_titles_ws.py::test_failed_turn_never_schedules_title`, a SQLAlchemy error in an unrelated file); the file passes when rerun alone (15 passed), so it looks flaky and unrelated to this plan.

## Known Stubs

None.

## Self-Check: PASSED
scripts/rag_eval.py, tests/test_rag_eval.py and commits 936484d, 0d8e79e exist.
