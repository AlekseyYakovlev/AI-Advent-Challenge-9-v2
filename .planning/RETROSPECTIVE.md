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

## Milestone: v4.0 — Week 6: Local LLM as a Private Service

**Shipped:** 2026-10-12
**Phases:** 3 | **Plans:** 21

### What Was Built
LAN mode behind one `BIND_HOST` setting with a shared host-match Origin rule, per-user API keys, a plain HTTP chat endpoint on the shared chat turn, message/login/concurrency limits, `scripts/verify_service.py` and `Day30_report.md` with a live run from a second machine.

### What Worked
- Extracting `_run_chat_turn` first let HTTP chat reuse the WebSocket path with no duplicated logic.
- Documentation guard tests keep API_SPEC and the guides tied to real routes and settings.
- A standard-library-only verification script ran on the Mac with nothing to install.

### What Was Inefficient
- ROADMAP kept Phase 18 at 6/7 and all three phases unticked after the work was done; the stale markers were fixed only at milestone close.
- The manual second-device check was approved without recorded steps, so the report can only cite the user's statement.
- No milestone audit was run before close.

### Key Lessons
- Tick the roadmap when a plan's summary is written, including plans that end on a human checkpoint.
- For a human checkpoint, ask for one concrete artifact (screenshot or device name) at approval time.

## Cross-Milestone Trends

| Milestone | Phases | Plans | Audit |
|-----------|--------|-------|-------|
| v3.0 | 7 | 53 | tech_debt |
| v4.0 | 3 | 21 | not run |
