# Phase 27: AI release pipeline (Day 35) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-17
**Phase:** 27-ai-release-pipeline-day-35
**Areas discussed:** Launch and write, Changelog, Version, Verification and report

---

## Launch and write

| Question | Options | Selected |
|----------|---------|----------|
| How is release preparation started? | workflow_dispatch Action / /release in chat / local CLI script / Action + CLI | /release in chat |
| Who creates the tag and draft Release? | MCP / app prepares, Action writes / preparation only | MCP (narrow tag tool in local-git; draft Release via second github-mcp-server entry without --read-only; missing tool = non-working step) |
| Who calls the MCP tools? | code calls, AI writes / model calls / mixed | Code calls, AI writes |
| Human confirmation before writing? | two steps / one step / dry-run + --publish | Two steps, version overridable |
| Tag pushed but Release failed? | keep tag + step report / preflight then write / roll back tag | Keep tag + step report; re-confirm skips done steps |

## Changelog

| Question | Options | Selected |
|----------|---------|----------|
| Range and sources? | from last tag / commits only / GitHub PRs only | From last tag to HEAD; commits, DayNN merges, merged PRs if any; overridable range |
| Noise handling? | code filters, AI summarises / all to AI / filter + files | Code filters, AI summarises by phase |
| Where it goes and language? | CHANGELOG.md + Release, human commits / CHANGELOG.md + app commit before tag / Release text only | Russian Keep a Changelog section in CHANGELOG.md, app commits, tag on that commit |
| Release branch and limits? | current branch, strict checks / main only / branch in command | Current branch, strict checks, never force |
| Model and RAG? | chat model, no RAG / own Settings section / + RAG over docs | Chat model, no RAG |

## Version

| Question | Options | Selected |
|----------|---------|----------|
| How is the next version chosen? | code computes, AI explains / AI decides freely / milestone only | Code computes, AI explains |
| Number format and candidates? | vX.Y as now / full semver / format of last tag | vX.Y as now |
| Tag taken or not greater? | plan flags, write blocked / warning only | Plan flags, write blocked |
| Tag type and Release title? | annotated, AI title / annotated, title = number / lightweight | Annotated, AI title |

## Verification and report

The area was restarted at the user's request; the final answers below replace the first pass (which had "DeepSeek only" for models).

| Question | Options | Selected |
|----------|---------|----------|
| Where to test writes? | sandbox then real / real repo directly / local bare repo only | Sandbox then real |
| Models for control runs? | DeepSeek only / local + DeepSeek / local only | Local only |
| Changelog quality evaluation? | code check only / code check + comment / none | Code check + comment |
| Scenarios? (multi) | full path / no GitHub write access / blocks / manual version and range | All four |

**Notes:** An earlier quality question that offered a DeepSeek judge was rejected by the user before answering.

## Claude's Discretion

- Narrow git tool names and whether they sit behind Phase 26's `--allow-write`
- Filter rules, phase mapping, PR dedup
- Plan storage between /release and confirm; confirm button rendering
- Local model context budget for ~200 commits
- Tests, e2e script, docs updates, Day35 branch base

## Deferred Ideas

None.
