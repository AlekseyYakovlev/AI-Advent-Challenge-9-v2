---
phase: 23-help-command-verification-and-report-day-31
plan: 05
subsystem: verification
tags: [help, eval, calibration, rag, mcp]
requires:
  - phase: 23-04
    provides: /help over WebSocket and HTTP
provides:
  - "scripts/help_eval.py: isolated-copy driver (check, measure, spike, run)"
  - "frozen 10-question control set"
  - "recorded threshold measurement and prompt spike"
affects: [23-06, 23-08]
key-files:
  created:
    - scripts/help_eval.py
    - tests/test_help_eval.py
    - tests/fixtures/help/control_set.json
    - eval_out/day31/threshold.json
    - eval_out/day31/spike.json
    - eval_out/day31/CALIBRATION.md
  modified: []
requirements-completed: [VER-10]
completed: 2026-10-13
---

# Phase 23 Plan 05: Control-run driver, threshold and prompt spike

The default `/help` threshold was measured on the real indexed documentation and stays 0.45. The 10-question control set is frozen. The combined quotes-plus-git-tools prompt worked on both models on the first attempt, so no prompt text changed.

## Tasks

| Task | Commit | Result |
|------|--------|--------|
| 1. Driver `scripts/help_eval.py` | 822acac | 984-line script, 17 unit tests |
| 2. Measure threshold, freeze control set | 38583c2 | `threshold.json`, `CALIBRATION.md` (Environment, Threshold), frozen fixture |
| 3. Prompt spike on both models | abfdec9 | `spike.json`, `CALIBRATION.md` (Prompt spike, Consequences) |

## Results

- **Default threshold:** 0.45, unchanged. Documentation scores 0.6378, 0.5584, 0.6066, 0.6665, 0.5621; outside question 0.3944. No code or test default was changed.
- **Corpus commit of the measurement:** 822acac (`README.md` and `docs/*.md` before Plan 23-06 adds the `/help` sections; files last changed in ba40d5b; no uploaded file was dirty). 268 chunks, `structural`, size 2000, overlap 0, `text-embedding-bge-m3`.
- **Spike verdicts:** `Verdict local: works`, `Verdict deepseek: works`. Wording revisions: 0.
- **Model ids found:** local `qwen/qwen3.5-9b`; DeepSeek `deepseek-flash` (chosen at run time from the provider's model list).
- **Control set:** frozen 2026-10-13T18:30:24Z, categories docs 5, git 2, mixed 2, out_of_corpus 1; key facts of the documentation items copied from `README.md`, `docs/USER_GUIDE.md`, `docs/ARCHITECTURE.md`, `docs/API_SPEC.md`.

Findings for the report (also in `CALIBRATION.md`, "Consequences for the control run"):
- DeepSeek omitted the «Цитаты:» block on the git and mixed spike questions and said the answer came from git only; the local model quoted on all three. Not a failed expectation.
- The pure git question `git-branch` scores 0.4697 in the documentation search, above the threshold, so git-only answers carry unrelated documentation sources.
- The threshold was measured at corpus commit 822acac; the control run indexes the final documentation, so the report must name both commits.

## Key variable and secret handling

The DeepSeek key lives in the variable `caps.deepseek_key` (class `Capabilities`, `repr=False`). It is used in exactly these places: read from `.env` by `read_env_value`; sent as the `Authorization` header to `api.deepseek.com` in the preflight and in `rag_judge.check_access`; put into the child-process environment dict in `build_run_env`; and passed in the `scrub` secret list used by `save_json`, `ask_all` and error output. It is never written to the copy's `.env`, `run_meta.json`, a log or stdout. Checked: no file under `eval_out/day31/` and not `scripts/help_eval.py` contains the key value; `git grep sk-` on `eval_out/day31` finds nothing; the scratch directory (copy, database, app log) is removed in `finally`. The user's app on 8000/8001 and `app.db` were not touched (`app.db` is opened only for one column in `mode=ro`); branch stays `Day31`.

## Verification (actually run)

- `pytest tests/test_help_eval.py tests/test_port_literals.py -q` -> 19 passed (17 in `test_help_eval.py` plus 2 port-literal tests); the frozen-fixture test added in Task 2 makes 18 in `test_help_eval.py`.
- `python scripts/help_eval.py check` -> exit 0: `embedding: ok`, `local model: ok`, `deepseek: ok`.
- `python scripts/help_eval.py measure --allow-draft` -> exit 0, `threshold.json` written.
- `python scripts/help_eval.py spike` -> exit 0, six HTTP requests all answered 200, `spike.json` written.
- Task 2 and Task 3 verify commands -> `ok`; `pytest tests/test_help_eval.py tests/test_help_command.py tests/test_help_api.py -q` 68 passed; `tests/test_help_command.py tests/test_help_turn.py` 59 passed.
- Full suite `pytest tests -q`: 3093 passed, 13 skipped, 1 failed (`tests/test_kb_api.py::test_create_happy_path`). The failed test passes alone and together with `test_help_eval.py` (68 passed); this plan adds no code under `agent/` or `shared/`, so the failure is a timing flake in an existing test, not caused by it. Left as it is (out of scope).
- Not run: `scripts/help_eval.py run` (graded run, belongs to Plan 23-08).

## Deviations from Plan

None - plan executed as written. Details that the plan left open: `measure` also records the top-5 sources per question; `build_run_env` runs with `DB_PATH` pointed to the scratch database before any application module is imported (a precondition for the isolation guard to hold, because the parent process imports `shared.config` for `check_access`); the DeepSeek preflight lists models with `httpx` before calling `check_access` so the model id is the one the API serves.

## Known Stubs

None.

## Open verification items

None. The graded control run (`python scripts/help_eval.py run`) is Plan 23-08 work, not an unfinished step of this plan.

## Self-Check: PASSED

Files present: `scripts/help_eval.py`, `tests/test_help_eval.py`, `tests/fixtures/help/control_set.json`, `eval_out/day31/threshold.json`, `eval_out/day31/spike.json`, `eval_out/day31/CALIBRATION.md`. Commits 822acac, 38583c2, abfdec9 exist.
