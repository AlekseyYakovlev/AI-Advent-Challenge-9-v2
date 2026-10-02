---
phase: 09-auto-rename-chats-with-llm-day-21
fixed_at: 2026-10-02T10:44:54Z
review_path: .planning/phases/09-auto-rename-chats-with-llm-day-21/09-REVIEW.md
iteration: 1
findings_in_scope: 4
fixed: 4
skipped: 0
status: all_fixed
---

# Phase 09: Code Review Fix Report

**Fixed at:** 2026-10-02T10:44:54Z
**Source review:** .planning/phases/09-auto-rename-chats-with-llm-day-21/09-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 4 (WR-01 to WR-04; there are no Critical findings)
- Fixed: 4
- Skipped: 0
- Out of scope and untouched: IN-01 to IN-12, CV-01 to CV-03

**Verification:** after each fix `pytest tests/test_titles.py tests/test_titles_ws.py
tests/test_llm_complete_chat.py` was run (80 passed before the fixes, 101 passed after the last
one). The full suite `pytest tests/` was run once after the last fix: 1030 passed, 0 failed. The app
was not started and ports 8000/8001 were not touched.

## Fixed Issues

### WR-01: `chat_title_failed` writes the user's message text into the log on a DB error

**Status:** fixed
**Files modified:** `agent/titles.py`, `tests/test_titles.py`
**Commit:** d50e6da
**Applied fix:** `generate_and_apply_title` now logs `error_type` and the string of `exc.orig`
(the driver error, which has no statement and no bound parameters), falling back to the exception
itself when there is no `orig`. New test `test_db_failure_log_has_no_title_text` makes `apply_title`
raise a `sqlalchemy.exc.OperationalError` built with the title in its parameters and asserts the
logged fields are exactly `chat_id`, `error_type`, `error="database is locked"` and that the user's
text is in no recorded log value.

### WR-02: Sanitizer deletes legitimate characters and whole spans, producing wrong titles

**Status:** fixed: requires human verification (behaviour change in the sanitizer)
**Files modified:** `agent/titles.py`, `tests/test_titles.py`, `docs/ARCHITECTURE.md`
**Commit:** 4d32663
**Applied fix:** `_strip_markup` no longer deletes every `*`, `_`, `#`, `` ` ``, `~`.
- `_TAG_LIKE_RE` is now `</?[A-Za-z][^<>]*>`: only real tags are removed, and the regex is linear.
- `_MD_WRAP_RE` removes paired `**`, `*`, `` ` ``, `~~` markers and keeps the wrapped text; up to
  three passes handle nesting such as `***x***`.
- `_MD_HEADING_RE` removes a leading `#` heading marker only.
- The removal of bare `<` and `>` is kept, because `test_clean_title_never_contains_angle_brackets`
  pins that property.

Differences from the reviewer's suggested patch, to confirm:
- Underscores are never treated as markup (`__` is not in the wrap pattern). With the suggested
  pattern `__init__` would become `init`; now `user_id` and `__init__` pass through unchanged. The
  cost is that a model answer written as `__Title__` keeps its underscores.
- The wrap pattern requires the markers to hug their content and not touch a word character on the
  outside, so `2*3*4` and `5 * 6 * 7` are left alone.
- `Сравнение a < b и c > d в Python` becomes `Сравнение a b и c d в Python`: the words survive, the
  two brackets are still removed.

New rows in `test_clean_title_accepts_and_normalizes` (`C#`/`F#`, `user_id`/`__init__`, `a < b`,
arithmetic with `*`, heading, nested emphasis, bold label) and a new
`test_fallback_title_keeps_code_characters`. The sanitizing sentence in `docs/ARCHITECTURE.md` was
updated to match.

### WR-03: A closing `</think>` without an opening tag stores the model's reasoning as the title

**Status:** fixed: requires human verification (behaviour change in the sanitizer)
**Files modified:** `agent/titles.py`, `tests/test_titles.py`
**Commit:** 9a11001
**Applied fix:** after removing complete think-blocks, `clean_title` drops everything up to and
including the last remaining closing tag (`_THINK_CLOSE_RE = </think\s*>`, case-insensitive), then
applies the existing dangling-`<think` rejection. A regex match is used instead of the suggested
`lower()` + `rindex`, because lowercasing can change the string length for some characters and shift
the index. New accept rows: `thinking</think>План поездки`, a multi-line reasoning with `</THINK>`,
and a complete block followed by a stray closing tag. New reject rows: reasoning that ends with the
closing tag and nothing after it, and a closing tag followed by a new unclosed `<think>`.

### WR-04: `clean_title` still runs the quadratic regex on unbounded model output

**Status:** fixed
**Files modified:** `agent/titles.py`, `tests/test_titles.py`, `docs/ARCHITECTURE.md`,
`docs/TESTING_GUIDE.md`
**Commit:** 9b33a91
**Applied fix:** new constant `CLEAN_INPUT_CHARS = 1000`; `clean_title` cuts the model output to
that length before any think-block or markup regex runs. The `TOOL_TRACE_HEADER` check stays before
the cut, on the full text. The tag regex was already made linear in the WR-02 commit.

One addition beyond the suggested patch, to confirm: when the output is longer than the limit and a
closing `</think>` ends past the cut, `clean_title` returns `None` (the fallback title is used). A
plain cut would remove that closing tag and bring back the WR-03 failure, storing the first line of
the reasoning as the title. The check is a single linear scan.

New tests: `test_clean_title_bounds_input_before_regex` (five hostile 100 000-character inputs;
asserts the length passed to `_strip_markup` and a 0.5 s limit),
`test_clean_title_overlong_output_keeps_first_line`,
`test_clean_title_rejects_reasoning_closed_past_the_cut`. The parametrized test uses explicit `ids`
because pytest copies the id into an environment variable and Windows rejects values over 32 767
characters. Both docs were updated to mention the new bound.

---

_Fixed: 2026-10-02T10:44:54Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
