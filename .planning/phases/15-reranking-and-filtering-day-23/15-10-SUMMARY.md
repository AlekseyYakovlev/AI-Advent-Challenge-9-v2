---
phase: 15-reranking-and-filtering-day-23
plan: 10
subsystem: rag-evaluation
tags: [rag, ablation, bge-m3, rerank, threshold]
requires:
  - phase: 15-09
    provides: calibrated bge-m3 threshold 0.67, keyword-gated FTS exemption
provides:
  - 70 live traces and answers for 7 configurations (eval_out/day23/raw)
  - ablation.md, answers.md/csv with manual verdicts, run_meta.json with winner
requirements-completed: [RANK-08]
completed: 2026-10-03
---

# Phase 15 Plan 10: Live 7-run ablation and manual verdicts Summary

The 7-configuration ablation ran live on bge-m3 (the Day 22 winner) over the 10 frozen control questions, and all 70 answers have a manual verdict. The main finding is that the 0.67 threshold lowers hit@5 from 0.88 to 0.62 on this set.

## Task 1: winner and run (commit c9cc6a4)

- Precondition: `Day22_report.md` and `14-07-SUMMARY.md` both exist.
- Winner (D-16): bge-m3. Day22_report.md quotes mean hit@1/3/5 of 0.75 / 0.88 / 0.88 for bge-m3 and 0.12 / 0.25 / 0.25 for nomic. Rule: higher mean hit@5. Recorded in the `winner` object in run_meta.json. KB: `bge=2`.
- Threshold: `resolve_threshold(None, "text-embedding-bge-m3")` returns 0.67 (source calibrated). No explicit `--threshold` was needed.
- Command: `python scripts/rag_eval.py ablate --kb bge=2 --max-tokens 4096 --context-length 16384`. It ran once, exit 0, about 20 minutes. LM Studio served qwen/qwen3.5-9b and bge-m3.
- Day 22 run_meta has max_tokens 4096 and context_length 16384. Day 23 run_meta has the same values. temperature is 0.0. fixture_sha256 is 8c5b76a0...6ebe2 in both.
- Completeness: 70 raw files. The baseline config is candidate_k 5, threshold 0.0 and no stage flags. The `all` config has lexical, llm, hybrid and rewrite all true.
- Baseline mean hit@5 is 0.88, equal to Day 22 bge-m3 (0.88). Baseline hit@1 0.75 and hit@3 0.88 also match.
- `app.db` untouched (`git status --short app.db` empty).

| run | hit@1 | hit@3 | hit@5 | chunks before | chunks after | retrieval ms | answer ms | below_threshold | empty/truncated |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 0.75 | 0.88 | 0.88 | 5.0 | 5.0 | 442 | 17189 | 0 | 1 |
| threshold | 0.62 | 0.62 | 0.62 | 20.0 | 1.1 | 438 | 15890 | 5 | 0 |
| lexical | 0.62 | 0.62 | 0.62 | 20.0 | 1.1 | 437 | 21719 | 5 | 0 |
| llm_rerank | 0.62 | 0.62 | 0.62 | 20.0 | 1.1 | 791 | 15942 | 5 | 0 |
| hybrid | 0.62 | 0.62 | 0.62 | 32.8 | 1.4 | 446 | 16711 | 5 | 0 |
| rewrite | 0.75 | 0.75 | 0.75 | 24.7 | 1.5 | 1539 | 12645 | 3 | 0 |
| all | 0.75 | 0.75 | 0.75 | 37.1 | 1.8 | 2070 | 13629 | 3 | 0 |

Skip counts: none, the `skipped` column is `-` for every run.

Empty/truncated answers: 1 of 70, `baseline` Q07 (finish_reason length, empty answer). Within the gate (at most 10 of 70, at most 3 of 10 per run). It is kept as is and listed as a limitation. Day 22 had 2 of 30 at the same budget.

## Task 2: manual verdicts (commit fd36563)

Claude filled `verdict` and `comment` for all 70 rows. judge_verdict and judge_comment are empty. Answers are byte-identical to the raw files (checked). answers.md was re-rendered with the script's `render_ablation_answers_md`, so no model calls were made.

Scale follows Day 22. An empty or truncated answer is graded «неверно» with the cause in the comment, as Day 22 did for its Q10 case. Answers that the threshold forced into general-knowledge mode are graded on factual agreement with the expected answer. Invented specifics are «галлюцинация», and correct answers carrying a misleading or unsupported extra are «частично».

| run | верно | частично | неверно | галлюцинация |
|---|---|---|---|---|
| baseline | 9 | 0 | 1 | 0 |
| threshold | 7 | 2 | 0 | 1 |
| lexical | 7 | 1 | 0 | 2 |
| llm_rerank | 8 | 1 | 0 | 1 |
| hybrid | 7 | 2 | 0 | 1 |
| rewrite | 8 | 0 | 1 | 1 |
| all | 8 | 0 | 1 | 1 |

Cases where a run's verdict is worse than the baseline's for the same question (input for the report's "where a stage hurt" section). Q07 baseline is an empty budget failure, so its comparisons are not a like-for-like regression:
- Q01: threshold «частично» (A given as 16 years); rewrite and all «неверно» (refusal; only one article-26 chunk survived the threshold and it lacked the age clause). Baseline «верно».
- Q02: lexical and hybrid «частично» (a misleading remark that the article lost force in 2012).
- Q07 (against the empty baseline): lexical, llm_rerank and hybrid «галлюцинация» (invented sanctions, no KB source).
- Q08: threshold, lexical, rewrite and all «галлюцинация»; llm_rerank and hybrid «частично». Baseline «верно».

Retrieval observations. The threshold run cuts all chunks for Q01, Q07 and Q08 (matching 15-08: cos 0.639, 0.653, 0.634 are below 0.67), so the model falls back to general knowledge. The out-of-corpus questions Q09 and Q10 were handled correctly in every run. On this 10-question set, rewrite and all recover Q07 with the correct sanction but not Q01 or Q08.

## Deviations from Plan

None. The `winner` object was added to run_meta.json by hand because the script does not write one. The run_meta.json edit is in c9cc6a4.

## Limitations

- 10 questions and one LLM, so differences are illustrative.
- The baseline Q07 is empty because the token budget ran out.
- The single run per configuration has no repeats, and the temperature is 0.
- Verdicts are Claude's and are reviewed by the user in 15-11.

## Self-Check: PASSED

Commits c9cc6a4 and fd36563 exist. eval_out/day23/raw has 70 files. answers.csv has 70 filled rows. STATE.md and ROADMAP.md were not modified.
