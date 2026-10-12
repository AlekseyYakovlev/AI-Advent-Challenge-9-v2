---
phase: 18-lan-exposure-day-30
plan: 07
subsystem: testing
tags: [lan, playwright, e2e, origin, websocket]
requires: ["18-01", "18-02", "18-03", "18-04", "18-05", "18-06"]
provides:
  - isolated LAN-mode end-to-end script (network checks 1-7, browser scenarios L1-L7)
  - TESTING_GUIDE entry with the manual second-device procedure
key-files:
  created: [scripts/e2e_lan_playwright.py]
  modified: [docs/TESTING_GUIDE.md]
status: awaiting-manual-uat
metrics:
  tasks-completed: 2/3
---

# Phase 18 Plan 07: LAN end-to-end verification Summary

Isolated-copy script that starts the app with `BIND_HOST=0.0.0.0` and proves, through the machine's real LAN address with real sockets and a real Chromium, the bind, banner, Origin rules and foreign-page blocking; the cross-device check is a pending human step.

## Results (observed)

`python scripts/e2e_lan_playwright.py` exited 0 with PASS for: copy patched, scratch DB, app up in LAN mode, LAN address reachable (chosen 10.8.1.1 of candidates 192.168.88.224, 10.8.1.1), checks 1-7, scenarios L1-L7, and "isolated ports are free again". Highlights: both sockets listen on 0.0.0.0; the banner has the warning block and LAN URL; preflight reflects the LAN and machine-name origins but not `http://evil.example:18000`; foreign and Agent-port origins get 403 on mutations; browser L1-L7 pass, including copy over plain HTTP, a streamed stub reply over `ws://10.8.1.1:18001/ws/chat/1`, and a foreign page on :18770 (cookie present) blocked on REST read, REST mutation, `/ws/events` and `/ws/chat` with `ws_origin_rejected` logged. L6 uses a `localhost` page.

After the run nothing listened on 18000/18001/18766/18770; nothing was listening on 8000/8001 before or after. `tests/test_port_literals.py` passes; full suite `pytest tests/`: 2301 passed, 11 skipped.

## Commits

- 9632dcd test(18-07): add isolated LAN-mode network checks script
- 6729aed test(18-07): add LAN browser scenarios and testing guide entry

## Deviations from Plan

**[Rule 3 - Blocking] scratch DB seeding** - importing `shared.network` (for LAN address discovery) imports `shared.config` before the helper sets `DB_PATH`, so `seed_scratch_user` refused with "scratch DB_PATH was not applied". Fix: the script sets `settings.DB_PATH` to the scratch database before the first import of `shared.database`. Commit 9632dcd.

A Windows Security Alert may appear when the copy binds 0.0.0.0; it was not scripted or dismissed.

## Known Stubs

None.

## Manual UAT (second device)

Pending: the user must confirm login and a streamed reply from a second device on the home network (NET-03). Until then cross-machine reachability and the Windows Firewall rule are not confirmed by a human. Steps are in the plan's Task 3 and in `docs/TESTING_GUIDE.md`. Outcome to be recorded on resume.
