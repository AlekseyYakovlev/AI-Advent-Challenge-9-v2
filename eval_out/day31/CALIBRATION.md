# /help calibration (Day 31)

## Environment

- Date: 2026-10-13 (UTC)
- Git branch: Day31, short hash 822acac
- Embedding model: text-embedding-bge-m3 (LM Studio, `bge-m3`)
- Chunking: structural, chunk size 2000, overlap 0
- Chunks indexed: 268, from 5 files (`README.md`, `docs/API_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/TESTING_GUIDE.md`, `docs/USER_GUIDE.md`)
- Isolated copy on ports 18000/18001 with a scratch database; the real repository was read through `README.md`, `docs/` and the read-only `local-git` server

Corpus commit: 822acac

Uploaded files with uncommitted changes at measurement time: none. The corpus is `README.md` and `docs/*.md` as they were before Plan 23-06 adds the `/help` sections (the files were last changed by commit ba40d5b).

## Threshold

Best cosine score of the search (top 5, no threshold applied) per control question, measured by `python scripts/help_eval.py measure --allow-draft`; raw data in `threshold.json`.

| Question id | Category | Best score | Best source |
|-------------|----------|-----------:|-------------|
| doc-run | docs | 0.6378 | docs/USER_GUIDE.md |
| doc-env | docs | 0.5584 | README.md |
| doc-arch | docs | 0.6066 | README.md |
| doc-api | docs | 0.6665 | docs/USER_GUIDE.md |
| doc-tests | docs | 0.5621 | docs/TESTING_GUIDE.md |
| git-branch | git | 0.4697 | docs/API_SPEC.md |
| git-commits | git | 0.4459 | docs/ARCHITECTURE.md |
| mixed-status | mixed | 0.5428 | docs/TESTING_GUIDE.md |
| mixed-docs-files | mixed | 0.6341 | docs/TESTING_GUIDE.md |
| outside | out_of_corpus | 0.3944 | docs/API_SPEC.md |

Rule: keep the default 0.45 when all five documentation scores are at or above it and the outside score is below it; otherwise, when the lowest documentation score is above the outside score, use their midpoint rounded to two decimals; otherwise keep 0.45 and record "not separable".

Decision: the lowest documentation score is 0.5584 and the outside score is 0.3944, so the default stays. Note that the two pure git questions score 0.4697 and 0.4459: `git-branch` lands just above the threshold, so a git-only question can still receive documentation fragments as sources. The threshold separates documentation questions from the outside question; it does not separate git questions from documentation.

Default threshold: 0.45

## Prompt spike

Run by `python scripts/help_eval.py spike` after the control set was frozen, over HTTP on the isolated copy (documentation base indexed, `local-git` connected and marked in `/help` settings, default `/help` settings). Three questions per model: `doc-run` (docs), `git-branch` (git), `mixed-status` (mixed). Raw results are in `spike.json`. Wording revisions of the `/help` prompt: 0 (every expectation was met on the first attempt, so no `spike_attempt_<n>.json` exists).

Model ids: local `qwen/qwen3.5-9b` (LM Studio), DeepSeek `deepseek-flash` (picked at run time from the provider's model list).

| Model | Question | Sources | Verified quote | Expected tool called |
|-------|----------|---------|----------------|----------------------|
| local | doc-run | yes (`docs/USER_GUIDE.md`, `README.md`) | yes (2) | n/a (none expected) |
| local | git-branch | yes (3 documentation files, see below) | yes (1) | yes (`git_current_branch`) |
| local | mixed-status | yes | yes (2) | yes (`git_status`) |
| deepseek | doc-run | yes (`docs/USER_GUIDE.md`, `README.md`) | yes (2) | n/a (none expected) |
| deepseek | git-branch | yes (3 documentation files, see below) | no | yes (`git_current_branch`) |
| deepseek | mixed-status | yes | no | yes (`git_status`, `git_current_branch`) |

Observations, none of which fails an expectation:

- Both models answered the git question from the tool (`Day31`) and the mixed question from `git_status` plus the documentation.
- DeepSeek left out the «Цитаты:» block on the git and mixed questions and said in the answer that the reply came only from git tools; the local model quoted on all three. The expectation for those two questions is a tool call and sources, not a quote, so this is recorded as a difference, not a miss.
- The git question received three documentation files as sources on both models: its best search score (0.4697) is above the default threshold 0.45. The sources are present but unrelated to the answer.
- One question per category and one run per model is a spike, not a measurement; the graded run of Plan 23-08 is the measurement.

Verdict local: works
Verdict deepseek: works

## Consequences for the control run

- Models: local `qwen/qwen3.5-9b`, DeepSeek `deepseek-flash`; both can quote and call the git tool in one `/help` turn with the current prompt. No limitation of either model was found; the report states that this rests on three questions per model.
- Nothing is recorded as `not run`: LM Studio, the local chat model, the embedding model and the DeepSeek key were all available. If the control run finds a model unavailable, that model is recorded as `not run` with the reason, never as passed.
- Quotes: DeepSeek may omit the «Цитаты:» block when the answer comes from git only; the report must not count that as a failed quote check for the git and mixed questions.
- Sources on git questions: a git-only question can carry unrelated documentation sources (score 0.4697 against the threshold 0.45); the report states that sources are present for these questions without being evidence for the answer.
- Mixed questions depend on the working tree at run time (`git status`), so the reference answer is built from the git snapshot taken in the same run (`run_meta.json`).
- Corpus: the threshold was measured on the documentation of the commit named in `Corpus commit: 822acac` (the state before the `/help` sections of Plan 23-06), while the control run indexes the final documentation, so the report must name both commits: the one in `Corpus commit:` for the threshold and the commit recorded in `run_meta.json` for the control run.
- Threshold: the default stays 0.45 (all five documentation scores 0.5584 to 0.6665, outside 0.3944); the control run uses it unchanged.
