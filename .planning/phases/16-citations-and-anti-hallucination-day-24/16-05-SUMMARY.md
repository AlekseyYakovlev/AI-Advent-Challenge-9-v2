---
phase: 16-citations-and-anti-hallucination-day-24
plan: 05
subsystem: rag-evaluation
tags: [rag, eval, citations, judge, day24]
requires: ["16-01", "16-03"]
provides:
  - "scripts/rag_eval.py cite subcommand (CITE-04)"
  - "scripts/rag_judge.py faithfulness rubric"
affects: [16-07]
tech-stack:
  added: []
  patterns: ["shared pure citation module reused by the eval", "manual and judge columns carried across re-renders"]
key-files:
  created: [tests/test_rag_eval_cite.py]
  modified: [scripts/rag_eval.py, scripts/rag_judge.py, tests/test_rag_judge.py]
decisions:
  - "Three cite runs: strict (threshold retrieval), strict_off (same retrieval, Phase 15 prompt), strict_baseline (plain top-k, optional via --runs)"
  - "Cite answer budget 8192 tokens (4096 left reasoning-model strict answers empty)"
  - "Empty answers are their own kind and never count as refusals or as missing quotes"
metrics:
  tasks: 3
  completed: 2026-10-04
---

# Phase 16 Plan 05: Day 24 evaluation tooling Summary

`python scripts/rag_eval.py cite` runs the 10 frozen control questions through the chat's gate and citation rules (strict gate with no model call below the threshold, `process_answer` for quotes), and `scripts/rag_judge.py --rubric faithfulness` fills an optional DeepSeek judge column on the resulting sheet. No live run was done (plan 16-07 does it).

## What was built

- `rag_eval.py`: `CITE_RUNS`, `classify_reply`, `_cite_question`, `run_cite` (raw records `raw/{run}_{id}.json`), `cite_metrics`, renderers (`cite_{run}.md`, `cite_summary.md`, `answers.csv`, `answers.md`), `_render_cite_outputs`, `cite_command` with `--render-only`, `--runs`, `--max-tokens` (default 8192). Retrieval defaults (model, top_k, candidate_k, kb) come from `eval_out/day23/run_meta.json`; the threshold comes from `resolve_threshold`; no threshold constants were changed.
- Automatic columns: sources present, quotes present (model exact / fuzzy / unverified, auto separately), correct «не знаю» / false refusal. Manual `verdict`/`comment` and `judge_*` cells survive re-renders through `_load_existing_verdicts`.
- `rag_judge.py`: `FAITHFULNESS_VERDICTS`, `FAITHFULNESS_SYSTEM_PROMPT`, `build_faithfulness_messages`, `parse_judge_reply(verdicts=...)`, `--rubric`, `read_sheet` (header kept), faithfulness judges only rows with kind `answer` and non-empty quotes. Relevance path unchanged (its defaults are now resolved inside `judge_command`).

## Verification (observed)

- `python -m pytest tests/test_rag_eval_cite.py tests/test_rag_eval.py tests/test_rag_judge.py -q`: 65 passed (23 in test_rag_judge including all pre-existing).
- `python scripts/rag_eval.py cite --help` shows `--runs`, `--max-tokens`, `--render-only`; `python scripts/rag_judge.py --help` shows `--rubric`.
- Greps: no diff to `CALIBRATED_THRESHOLDS`; `JUDGE_SYSTEM_PROMPT` / `OUT_OF_CORPUS_RULE` untouched.
- Not verified: a live LM Studio / DeepSeek run (out of scope, plan 16-07).

## Commits

- 631bbd4 feat(16-05): add cite run and raw record layer to rag_eval
- 463f8a2 feat(16-05): add Day 24 cite metrics, renderers and cite CLI
- 438ed5b feat(16-05): add faithfulness rubric to rag_judge

## Deviations from Plan

- [Rule 3 - Blocking] Worktree base was not the expected commit; reset to e0b8a79 as the startup check prescribes.
- `rag_judge.py --answers` / `--meta` parser defaults became `None` and are resolved in `judge_command` (Day 23 paths for relevance, Day 24 for faithfulness); relevance behaviour is the same.
- A strict question whose retrieval raised RagFailure is recorded as kind `error` (no model call) instead of a refusal.

## Known Stubs

None.

## Self-Check: PASSED
