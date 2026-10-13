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
