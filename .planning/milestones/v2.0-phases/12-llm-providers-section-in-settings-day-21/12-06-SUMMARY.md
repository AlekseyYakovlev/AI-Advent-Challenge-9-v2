---
phase: 12-llm-providers-section-in-settings-day-21
plan: 06
subsystem: verification-docs
tags: [playwright, uat, live-check, docs, llm-providers]
requires: ["12-02", "12-03", "12-04", "12-05"]
provides:
  - "Browser UAT script for the providers feature on an isolated copy (UI :18000 / Agent :18001)"
  - "Opt-in live DeepSeek title test (RUN_LIVE_DEEPSEEK=1)"
  - "Provider documentation (API_SPEC, TESTING_GUIDE, USER_GUIDE, .env.example)"
affects: []
tech-stack:
  added: []
  patterns: ["temp copy of the tree with ports patched only in the copy", "stub OpenAI-compatible provider in-process"]
key-files:
  created:
    - scripts/e2e_llm_providers_playwright.py
    - tests/test_live_deepseek_title.py
  modified:
    - docs/API_SPEC.md
    - docs/TESTING_GUIDE.md
    - docs/USER_GUIDE.md
    - .env.example
    - .planning/ROADMAP.md
    - .planning/phases/09-auto-rename-chats-with-llm-day-21/09-HUMAN-UAT.md
key-decisions:
  - "Live DeepSeek check recorded as blocked (placeholder key in .env), not as passed"
requirements-completed: [PROV-01, PROV-02, PROV-03, PROV-04, PROV-05, PROV-06]
duration: ~35 min
completed: 2026-10-02
---

# Phase 12 Plan 06: Live check, docs and browser UAT Summary

The providers feature is verified end to end in a real browser (S1-S10 pass, exit 0) on an isolated copy, and documented. PROV-07 (the live DeepSeek call that closes backlog 999.11) is NOT verified: the repository `.env` has only the placeholder DEEPSEEK_API_KEY, so the live test skipped.

## Tasks

| Task | Commit | Result |
|------|--------|--------|
| 1 Live DeepSeek test, docs, UAT/backlog records | 030c22b | done; live run SKIPPED (no real key) |
| 2 Playwright UAT on the isolated copy | 728c119 | done; exit 0 |

## Task 1 observed results

- `pytest tests/test_live_deepseek_title.py -q -rs`: 1 skipped.
- `RUN_LIVE_DEEPSEEK=1 pytest tests/test_live_deepseek_title.py -q -rs`: 1 skipped, reason "no real DEEPSEEK_API_KEY in the repository .env". No model id was chosen and no paid call was made.
- `09-HUMAN-UAT.md` test 1 stays `[pending]` with a dated note. ROADMAP 999.11 has `**Status:** code path delivered in Phase 12; live check blocked: no real DEEPSEEK_API_KEY ...`. To close: put a real key in `.env` and run `RUN_LIVE_DEEPSEEK=1 pytest tests/test_live_deepseek_title.py -q -rs`.
- Docs: `/api/v1/llm-providers` appears 7 times in API_SPEC (needs at least 6); `test_llm_providers` appears 4 times in TESTING_GUIDE; `LLM_PROVIDER_CHECK_TIMEOUT` is in `.env.example` and API_SPEC.
- Full suite: `pytest tests/ -q` gave 1148 passed, 1 skipped (the opt-in live test), 0 failed.
- `git grep "stub-secret-123"` outside the script matches only the 12-06-PLAN.md text. No real key exists in the repo, so nothing could leak.

## Task 2 browser UAT (run by Claude with Playwright, headless Chromium, on the isolated copy at UI :18000 / Agent :18001; ports 8000/8001 untouched)

Command: `python scripts/e2e_llm_providers_playwright.py`. Final exit code: 0.

| Check | Result | Evidence |
|-------|--------|----------|
| S1 section and seeded cards | PASS | heading "Провайдеры LLM", LM Studio card present, no DeepSeek card (no key) |
| S2 invalid Base URL | PASS | "Base URL должен начинаться с http:// или https://." |
| S3 save + automatic check | PASS | toast "Провайдер сохранён", badge "доступен · 1 моделей", meta "http://127.0.0.1:18766 · ключ: STUB_KEY" (trailing /v1 normalized away) |
| S4 wrong key variable, then fix | PASS | badge "ошибка", "Неверный или отсутствующий API-ключ. Проверьте переменную STUB_BAD ..."; back to "доступен" after the manual check |
| S5 LM Studio unreachable | PASS | "ошибка", "Сервер недоступен. Проверьте Base URL и что сервис запущен." (see Observation) |
| S6 grouped picker | PASS | optgroup "Stub" with "Stub · stub-model" |
| S7 chat through the stub | PASS | answer "Ответ заглушки"; 2 stub chat requests (turn and title), all with `Bearer stub-secret-123`; title "Заголовок заглушки" |
| S8 disable provider | PASS | Stub optgroup gone, picker shows the single "Нет доступных моделей" placeholder (no other entry exists) |
| S9 delete provider | PASS | confirm text starts "Удалить провайдера «Stub»?", card removed, toast "Провайдер удалён" |
| S10 no key in API bodies | PASS | 23 responses inspected, none contained the stub keys |
| S11 DeepSeek chat | SKIPPED | no real DEEPSEEK_API_KEY (placeholder) |

Screenshots (scratch dir kept by the script): `C:\Users\Aleksey\AppData\Local\Temp\providers_e2e_g1zp3g57\S1.png, S3.png, S4.png, S6.png, S7.png, S8.png, S9.png`; results in `results.json` there.

## Not verified

- PROV-07: live DeepSeek call and S11 (real DeepSeek chat and `chat_title_set` source). Blocked on a real key.
- S8 fallback toast "Провайдер недоступен. Выбрана другая модель." was not exercised: the run had no second usable provider, so the placeholder branch was taken. That toast is covered only by code review.
- The user's app on 8000/8001 was never started or stopped.

## Deviations from Plan

**1. [Rule 3 - Blocking] The script copy excludes `scripts/`.** The plan's leftover-port grep aborted on the literal 8000/8001 inside the two e2e scripts themselves; `scripts` was added to the copy's ignore list (first run exit 1).

**2. [Test adjustment] S5 uses a manual "Проверить" click.** The card list is rendered before the page-load model refresh finishes (closed LM Studio port makes that refresh take about 2 s per call), so the LM Studio card still showed "не проверен". The check clicks "Проверить" to get the error badge.

**3. [Test adjustment] Waits for the new chat and the picker.** S7 waits for the chat count to grow (the app already has an auto-created chat, so sending early used the old chat's socket); S6 waits for the Stub optgroup after the slow refresh. No product bug was found, so no product source changed (0 of 3 fix iterations used).

## Observation (not fixed)

On a cold page load the Settings card for a seeded provider can show "не проверен" until the slow concurrent model refresh finishes or the user clicks "Проверить"; the cached check is not pushed to an already rendered card list. This matches 12-UI-SPEC's "not checked yet" state, so it was left as is.

## Known Stubs

None.

## Threat Flags

None. T-12-27: the real key is never read into output (none exists); fake keys live only in the script and the temp copy's `.env`. T-12-28: only ports 18000, 18001, 18766, 18767 were used.

## Self-Check: PASSED

Created files exist (scripts/e2e_llm_providers_playwright.py, tests/test_live_deepseek_title.py); commits 030c22b and 728c119 exist.
