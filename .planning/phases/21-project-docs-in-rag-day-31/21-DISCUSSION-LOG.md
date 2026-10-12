# Phase 21: Project docs in RAG (Day 31) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in 21-CONTEXT.md — this log preserves how they were reached.

**Date:** 2026-10-13
**Phase:** 21-project-docs-in-rag-day-31
**Mode:** discuss (four questions asked in one turn)
**Areas discussed:** README language, folder contents, base state during addition, repeated files

## README language

| Option | Selected |
|--------|----------|
| English, like `docs/` (recommended) | ✓ |
| Russian | |
| Russian plus `README.en.md` | |

## Folder contents

| Option | Selected |
|--------|----------|
| Skip hidden and service directories (recommended) | |
| Take everything supported, from every nested directory | ✓ |
| Top level only | |

**Note:** the user chose against the recommendation; no directory is filtered by name.

## Base state during addition

| Option | Selected |
|--------|----------|
| Stays ready and searchable, progress on the card, unchanged on failure (recommended) | ✓ |
| Blocked in the indexing status until done | |

## Repeated files

| Option | Selected |
|--------|----------|
| Replace silently, show a summary (recommended) | ✓ |
| Ask for confirmation before replacing | |
| Reject repeated names | |

## Decided earlier in the session (milestone definition)

- Folder upload and several selections in one list are required; addition to an existing base is required; code files are not needed yet.
- Project documents enter RAG through the existing UI, not through a script or automatic indexing.

## Claude's Discretion

- New file-count limit value, document identity by source name, control layout, index update strategy, UI wording.

## Deferred Ideas

- Code and schema files in a knowledge base (KBUP-07), removal of a single file (KBUP-08), a skip list for service directories, automatic re-indexing at start-up.
