# Retrospective

## Milestone: v3.0 — Week 5: RAG

**Shipped:** 2026-10-10
**Phases:** 7 | **Plans:** 53

### What Was Built
Knowledge-base indexing, RAG chat with per-chat settings, two-stage retrieval with calibrated threshold, verified citations with a code-enforced "не знаю" gate, and task memory for a RAG mini-chat; Day22-Day25 reports.

### What Worked
- Frozen control set and eval script made every report comparable.
- Fail-soft stages and outbound-only merging kept the message tree clean.
- Isolated-copy Playwright UAT (ports 18000/18001) never disturbed the user's app.

### What Was Inefficient
- Phase 14 UAT gap (K=15) sat undiagnosed although a fix (c108374) already existed; the UAT file was not re-checked.
- Requirement checkboxes and SUMMARY frontmatter drifted from verified state.
- Several human_needed items accumulated unresolved.

### Key Lessons
- Re-run UAT items after related fix commits and close them in the UAT file.
- Tick requirements at phase verification, not at milestone close.
- Threshold calibration on a tiny control set gives false refusals; record that honestly.

## Cross-Milestone Trends

| Milestone | Phases | Plans | Audit |
|-----------|--------|-------|-------|
| v3.0 | 7 | 53 | tech_debt |
