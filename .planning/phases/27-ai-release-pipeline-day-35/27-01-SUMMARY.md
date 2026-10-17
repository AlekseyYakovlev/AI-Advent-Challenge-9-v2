---
phase: 27-ai-release-pipeline-day-35
plan: 01
subsystem: mcp
tags: [mcp, git, release, github-rest, httpx]
requires:
  - phase: 26
    provides: "--allow-write flag and project_git.py file tools"
provides:
  - "--allow-release flag and register_release_tools() in mcp_servers/project_git.py"
  - "release_context, release_range_commits, release_commit_changelog, release_create_tag, release_push, github_create_draft_release"
affects: [27-03, 27-04, 27-06]
tech-stack:
  added: []
  patterns: ["flag-gated tool registration via register_release_tools()", "explicit refspec push, no force"]
key-files:
  created: [tests/test_mcp_release_tools.py, tests/test_mcp_release_github.py]
  modified: [mcp_servers/project_git.py]
key-decisions:
  - "Release tools are plain module functions registered only under --allow-release, so the default entry exposes no writes (D-04)"
  - "Remote tags are read with `ls-remote --refs origin refs/tags/*` so the source never contains the wide-tag-push option string the cleanliness test forbids"
requirements-completed: [D-04, D-06, D-08, D-09, D-20]
duration: 40min
completed: 2026-10-17
---

# Phase 27 Plan 01: Release tools in the git MCP server Summary

Six release tools in `mcp_servers/project_git.py`, registered only under `--allow-release`: context read, paged range read, CHANGELOG.md commit, annotated tag, non-force push with explicit refspecs, and an idempotent draft GitHub Release over REST.

## Tasks

| Task | Commit |
|------|--------|
| 1 RED tests | d12f415 |
| 1 flag, registration, release_context, release_range_commits | 96b1156 |
| 2 RED tests | 78ba9a5 |
| 2 release_commit_changelog, release_create_tag, release_push | 99c9e0b |
| 3 RED tests | 47b60db |
| 3 github_create_draft_release | 3234922 |

## Verification (actually run)

- `pytest tests/test_mcp_release_github.py tests/test_mcp_release_tools.py tests/test_mcp_project_git.py tests/test_mcp_project_git_core.py tests/test_mcp_project_files.py -q`: 280 passed, 3 skipped.
- `python mcp_servers/project_git.py --help` lists `--allow-release`.
- The grep gate for force, wide tag push and `-A` strings (comment lines filtered) prints 0.
- Not verified: a real push to GitHub or a real draft release (only a local bare origin and respx mocks were used).

## Deviations from Plan

**1. [Rule 3 - Blocking] Existing server already has 8 tools and `--allow-write`**
- The plan assumed five always-on tools. Phase 26 had already added `read_file`, `search_text`, `find_usages` (decorated at import), so `grep -cE "^@server.tool"` prints 8, not 5. The no-flag test asserts that none of the six release names are listed and that `--allow-release` adds exactly those six. `configure()` keeps `allow_write` and gains `allow_release` as a following keyword argument.

**2. [Rule 1] `ls-remote` call shape**
- The plan's gate forbids the `"--tags"` string in the source, so remote tags are listed with `ls-remote --refs origin refs/tags/*` instead of `--tags`; same result.

## Assumption Drift (advisory)

- Found during: Task 1. Planned: five existing tools in the default entry. Actual: eight (Phase 26 file tools). Why: Phase 26 landed before this plan. No behavior impact.

## Known Stubs

None.

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| threat_flag: network | mcp_servers/project_git.py | Under --allow-release the server calls api.github.com with a token from its own env (covered by T-27-04) |

## Self-Check: PASSED

Files and commits above exist in the worktree history.
