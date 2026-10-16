---
phase: 24-automated-pr-code-review-day-32
reviewed: 2026-10-17T00:00:00Z
depth: standard
files_reviewed: 17
files_reviewed_list:
  - .github/workflows/pr-review.yml
  - agent/code_chunking.py
  - agent/code_git.py
  - agent/code_index.py
  - agent/main.py
  - agent/providers.py
  - agent/review_api.py
  - agent/review_github.py
  - agent/review_pipeline.py
  - agent/review_prompts.py
  - agent/review_service.py
  - scripts/pr_review_ci.py
  - scripts/review_control.py
  - scripts/review_judge.py
  - shared/models.py
  - ui/static/app.js
  - ui/static/index.html
findings:
  critical: 0
  warning: 8
  info: 7
  total: 15
status: issues_found
---

# Phase 24: Code Review Report

**Reviewed:** 2026-10-17
**Depth:** standard
**Files Reviewed:** 17
**Status:** issues_found

## Summary

The Day 32 PR-review feature is generally careful: git is run without a shell with a fixed argv, refs are validated, the route is lock-protected, secrets are scrubbed in the CI script, and the review text goes through a sanitizer. No outright blocker was found. The significant problems are (1) the CI job executes a script taken from the PR branch on a self-hosted runner that holds the API key, (2) the markdown sanitizer can be defeated by an unterminated code fence, (3) the prompt "neutralise" step silently rewrites the code being reviewed (`===` becomes `==`, `->` becomes `-›`), (4) the code index can send secret-bearing config files to a cloud LLM, and (5) error classification in the GitHub reader matches bare substrings like "401".

No convention-pack run was performed (the `gsd-tools verify conventions` module is not part of this environment); there are no CONVENTION findings.

## Warnings

### WR-01: CI runs `scripts/pr_review_ci.py` from the PR branch on a self-hosted runner with the API key

**File:** `.github/workflows/pr-review.yml:25-36`
**Issue:** `actions/checkout` on a `pull_request` event checks out the PR merge ref, and then `python scripts/pr_review_ci.py` runs the file from that checkout with `API_KEY: ${{ secrets.PR_REVIEW_API_KEY }}` in its environment. Any same-repo branch author (fork PRs are excluded, same-repo are not) can edit the script, or add `sitecustomize.py` or similar next to it, to execute arbitrary code on the owner's Windows machine and read the API key. The fork gate does not help against a compromised collaborator account or a malicious dependency bot.
**Fix:** Check out the trusted base and run the script from there. The script needs nothing from the PR contents:
```yaml
- uses: actions/checkout@<sha>
  with:
    ref: ${{ github.event.pull_request.base.sha }}
    persist-credentials: false
```
Alternatively run the script via `python -I` from a fixed path outside the workspace.

### WR-02: `sanitize_markdown` leaves an unterminated code fence open, so fence parity flips across sections

**File:** `agent/review_pipeline.py:94, 400-416`
**Issue:** `_FENCE_RE` treats an unterminated fence as running to end of text (`|\Z`) and passes it through unsanitised. The only place an open fence is closed is the truncation branch. A section that ends with an odd number of fences is therefore published as-is. When the CI script then concatenates the three sections, the unclosed fence in section N is closed by the first fence of section N+1. From that point GitHub renders the "code" of section N+1 as live markdown, including the parts the sanitizer deliberately skipped because it believed they were inside a fence (raw `<img>`, `<a>`, HTML). A prompt-injected diff can use this to get raw HTML or tracking images into the comment. The CI-side `defuse` only strips markdown images and mentions, not HTML.
**Fix:** After sanitising each section, force even parity:
```python
cleaned = "".join(out)
if cleaned.count("```") % 2 == 1:
    cleaned += "\n```"
```
Do this before the length check, and make the truncation branch reuse the same helper.

### WR-03: `neutralise` rewrites the code under review (`===` to `==`, `<`/`>` to guillemets)

**File:** `agent/review_prompts.py:22, 63-65`
**Issue:** `neutralise` replaces every `<` and `>` with `‹`/`›` and collapses any run of 3 or more `=` to `==`. It is applied to the diff, to the retrieved code fragments and to the documentation. The model therefore sees JavaScript `===` as `==`, Python `->` as `-›`, `a < b` as `a ‹ b`, `>=` as `›=`, HTML templates as garbage, and so on. This invites false findings ("loose equality", "syntax error") and hides real ones, and it directly damages the quality metric this phase measures. It also gives no real protection: the tags in use are only a delimiter convention.
**Fix:** Escape only the specific closing delimiters, not all angle brackets:
```python
_TAG_RE = re.compile(r"</?(pr_title|pr_description|diff|documentation|code|partial_review)\b", re.I)
def neutralise(text: str) -> str:
    return _TAG_RE.sub(lambda m: m.group(0).replace("<", "‹"), text)
```
and drop the `=` collapsing, or restrict it to the exact fragment-block fence string that `build_rag_block` uses.

### WR-04: Code index can embed secret-bearing config files and ship them to a cloud LLM

**File:** `agent/code_index.py:78-84, 163-176`
**Issue:** `is_indexable` accepts `.json .yml .yaml .toml .ini .cfg .txt` anywhere in the tree and only excludes names starting with `.env`, `package-lock.json` and a few suffixes. Files such as `secrets.yml`, `credentials.json`, `config/production.ini` or `service-account.json` that are committed to the base branch are chunked, embedded, and later retrieved into the review prompt, which goes to DeepSeek when a cloud provider is selected (the UI only shows a generic note about it).
**Fix:** Add a name-based deny list (`secret`, `credential`, `token`, `password`, `private`, `id_rsa`, `service-account`, `.npmrc`, `.pypirc`, `.netrc`) checked case-insensitively on the basename, and consider excluding `.json`/`.yml` outside of an allow-listed set of directories.

### WR-05: Error classification by bare substring misreads PR numbers and unrelated text

**File:** `agent/review_github.py:42-43, 151-157`
**Issue:** `TOKEN_REJECTED_MARKERS = ("401", ...)` and `NOT_FOUND_MARKERS = ("404", ...)` are matched with `in` against the whole error text, and token rejection is checked first. A "pull request 401 not found" style message, a repository or owner name containing `401`, a request id, or a 403 rate-limit message containing digits will be reported as "token rejected (401)" or fall through to "not understood". The CI failure text then tells the owner to fix a token that is fine.
**Fix:** Match on word boundaries and on the phrases GitHub actually returns, for example `re.search(r"\b401\b|Bad credentials", text)`, check "Not Found" before the numeric 401, and add an explicit branch for rate-limit (403/429) so it does not collapse into `MSG_BAD_ANSWER`.

### WR-06: `head_sha` accepted and validated by the route but never used

**File:** `agent/review_api.py:88`, `agent/review_service.py:293-306`
**Issue:** `ReviewRequest.head_sha` is validated and passed down to `produce_review`, which only logs `head_requested=bool(head_sha)`. The review always uses whatever head the PR has at fetch time. A queued CI run for an older commit therefore posts a review of a newer commit without any indication that it differs from the event commit (the comment header shows the real head, but the caller never learns about the mismatch). This is a dead parameter with an implied contract.
**Fix:** Either compare `pr.head_sha` with the requested value and add a note (or a `head_changed` flag) when they differ, or remove the field from the API and from `pr_review_ci.py`.

### WR-07: Truncation of a section in `truncate_comment` can cut inside a code fence and swallow the rest of the comment

**File:** `scripts/pr_review_ci.py:204-213`
**Issue:** Sections are cut with `v[:int(len(v)*ratio)].rstrip() + " …"` regardless of fence state. If the cut falls inside a fenced block, every later section heading and the `<details>` sources block render as code. In addition, if the fixed part of the comment (header with long `notes` or an `unreviewed_files` list) alone exceeds `COMMENT_MAX_CHARS`, the `while ... ratio > 0` loop re-renders about 6,900 times (until the float underflows) and still returns an oversized text, which GitHub then rejects with HTTP 422, so the review is lost.
**Fix:** After cutting, close an odd number of fences, cap the length of the notes and unreviewed lists in `_header_lines`, and stop the loop at a minimum ratio such as `0.05`, then hard-truncate the final text.

### WR-08: CI-side `defuse` inserts a zero-width space into `@` inside code and inline code

**File:** `scripts/pr_review_ci.py:57, 126-129`
**Issue:** The pipeline sanitizer deliberately leaves `@` untouched inside backticks and fences (`(?<![\w`])@`), but `defuse` in the CI script uses `@(?=[A-Za-z0-9])` on the whole text. Every `@router.post`, `@dataclass`, `@app.get` in a suggested fix is corrupted with an invisible character, so copy-pasted code breaks. The two sanitizers also disagree on image handling (the CI one misses reference-style images).
**Fix:** Reuse one implementation (the pipeline already sanitises), or apply the same fence/inline-code aware logic in the script. Since the app already sanitises, `defuse` could be limited to the header `notes`.

## Info

### IN-01: Duplicate "unreviewed files" information in the comment

**File:** `agent/review_pipeline.py:686-687`, `scripts/pr_review_ci.py:171-174`
**Issue:** `run_review` appends `NOTE_UNREVIEWED` (with the file names) to `notes`, and `_header_lines` also renders `unreviewed_files` as a second line. The same list appears twice. The note also embeds raw file names that bypass `sanitize_markdown`, while the script wraps names in backticks (a file name containing a backtick breaks out of the code span).
**Fix:** Keep only the structured field, or drop one of the two lines, and strip backticks and angle brackets from names.

### IN-02: A PR where nothing is reviewable reports "Замечаний нет." in all three sections

**File:** `agent/review_pipeline.py:674-675`
**Issue:** If every changed file has no patch text (binary files only, or rename-only), `partials` is empty and the result is three "no remarks" sections with `parts=0`. Readers will take that as a clean review. Only the note about unreviewed files hints otherwise.
**Fix:** Raise or return a distinct state (for example a note "ничего не проанализировано") and let the CI script render a clear banner.

### IN-03: Cross-module use of private helpers of `kb_indexer`

**File:** `agent/code_index.py:250, 498`
**Issue:** `kb_indexer._get_semaphore()` and `kb_indexer._chunk_rows(...)` are underscore-private but are called from another module. A change inside `kb_indexer` can break the code index silently.
**Fix:** Expose them as public functions (`get_index_semaphore`, `build_chunk_rows`) in `kb_indexer`.

### IN-04: Review can lose its code base to a concurrent refresh or eviction

**File:** `agent/code_index.py:221-228, 294-310, 588-604`
**Issue:** The per-base lock is released when `refresh_code_index` returns, but the review keeps using `code_kb` for retrieval for minutes. A concurrent manual refresh with a different embedding model (`_load_existing` drops the base), or a refresh of a sixth base ref (`_evict_old`), can delete that KB mid-review and the review fails with `CODE_INDEX_UNAVAILABLE`. Changing the docs KB in settings (which changes the embedding model) also forces a full rebuild of every code base.
**Fix:** Hold a shared read marker for the KB while a review uses it, or skip eviction and drop for bases touched in the last N minutes; document the rebuild-on-model-change behaviour.

### IN-05: Invisible character used as a constant

**File:** `agent/review_pipeline.py:62`, `scripts/pr_review_ci.py:47`
**Issue:** `ZERO_WIDTH_SPACE` is a literal invisible character in the source, which is easy to lose in an editor and unreadable in review.
**Fix:** Use `"​"`.

### IN-06: Minor model and API inconsistencies

**File:** `shared/models.py:873`, `agent/code_git.py:30`, `agent/review_api.py:345-354`
**Issue:** `ReviewCodeIndex.indexed_commit` is `max_length=40`, while `_COMMIT_ID` accepts 64-hex SHA-256 commits. `_read_refresh_body` reports a malformed JSON body with `MSG_NO_BRANCH` ("could not determine branch"), which misleads. Branch names containing characters outside `_REF_CHARS` (for example `#`, `,`) that are legal in git fail with a generic bad-name error.
**Fix:** Raise `max_length` to 64, use a dedicated message for a bad body, and widen the allowed ref character set (keeping the option-injection checks).

### IN-07: Review UI always pins provider and model on save

**File:** `ui/static/app.js:4183-4188`
**Issue:** `saveReviewSettings` always sends the currently selected provider and model, so the documented fallback "NULL means the model currently loaded in LM Studio" is lost after the first save. Also, `evaluate_pr` and `review_control` list only the first 100 PR comments (`per_page=100`, no paging), so an old review comment can be missed on a busy PR.
**Fix:** Send `null` for the model when the user did not change it from the effective value, and page through comments in `scripts/review_control.py:300`.

---

_Reviewed: 2026-10-17_
_Reviewer: Claude (bm-code-reviewer)_
_Depth: standard_
