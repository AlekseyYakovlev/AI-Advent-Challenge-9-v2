---
phase: 21
slug: project-docs-in-rag-day-31
status: approved
shadcn_initialized: false
preset: none
created: 2026-10-13
---

# Phase 21 — UI Design Contract

> Visual and interaction contract for the knowledge base form and card changes in Phase 21. Reuses the shipped v3.0 knowledge base UI (`ui/static/index.html`, `ui/static/app.js`); nothing here introduces a new visual language.

---

## Design System

| Property | Value |
|----------|-------|
| Tool | none (vanilla JS + Tailwind CDN; no bundler, no component library; shadcn gate not applicable) |
| Preset | not applicable |
| Component library | none |
| Icon library | none (text glyphs only: `×`, `+`, `…`) |
| Font | Tailwind default sans stack (system UI) |

Source: CLAUDE.md hard constraints; existing markup in `#kb-create-modal` and `buildKbCard`.

---

## Spacing Scale

Declared values (multiples of 4), expressed as the Tailwind utilities already used by the knowledge base UI:

| Token | Value | Usage |
|-------|-------|-------|
| xs | 4px | `space-y-1`, `py-1`, `mb-1` — pending-list rows, label gaps |
| sm | 8px | `gap-2`, `p-2`, `px-2` — card padding, button rows, summary line gap |
| md | 16px | `space-y-4` — gaps between form groups |
| lg | 24px | not used in this phase |
| xl | 32px | not used in this phase |
| 2xl | 48px | not used in this phase |
| 3xl | 64px | not used in this phase |

Exceptions: existing 12px (`px-3`, `py-3`) in the dashed drop/select controls is kept. Existing 20px modal padding (`p-5`, `px-5`) is kept for consistency with `#kb-create-modal`; `#kb-add-modal` reuses the same shell. Pending-list scroll area is capped at `max-h-48` (192px) so a folder with ~200 files does not push the form off-screen.

---

## Typography

Sizes and weights already in use; no new sizes or weights are added.

| Role | Size | Weight | Line Height |
|------|------|--------|-------------|
| Body (form labels, buttons, card title) | 14px (`text-sm`) | 400 regular; card title and primary button 600 | 1.5 |
| Label / meta (pending rows, card meta, chip, summary, errors) | 12px (`text-xs`) | 400 | 1.5 |
| Heading (modal title) | 18px (`text-lg`) | 600 | 1.2 |
| Display | not used in this phase | — | — |

Weights: exactly two — 400 and 600 (`font-semibold`).

---

## Color

Existing slate dark theme; no new colors.

| Role | Value | Usage |
|------|-------|-------|
| Dominant (60%) | slate-900 `#0f172a` | Modal surface, page background |
| Secondary (30%) | slate-800 `#1e293b` (+ slate-700 borders `#334155`) | Inputs, kb cards, pending-list rows |
| Accent (10%) | indigo-600 `#4f46e5` / indigo-500 `#6366f1` | see list below |
| Destructive | red-400 `#f87171` | Validation/failure messages and delete only |

Status colors (existing, reused): sky-400 progress/indexing, emerald-400 ready, slate-400 neutral/queued.

Accent reserved for: the primary submit button (`Индексировать`), the focus ring on inputs, and the hover border of the dashed "choose" controls. The new "add to base" button on a card uses the neutral secondary style (`bg-slate-700`), NOT accent.

---

## Component Contract

### 1. Create modal — file source controls (replaces single `#kb-files` label)

- Section label (text-sm slate-400): `Файлы (PDF, TXT, MD)`.
- Two dashed controls side by side (`grid grid-cols-2 gap-2`), each styled exactly like the current dashed label: `cursor-pointer rounded-lg border border-dashed border-slate-700 px-3 py-3 text-center text-sm text-slate-300 hover:border-indigo-500 transition`.
  - `Выбрать файлы` — `<input type="file" id="kb-files" multiple accept=".pdf,.txt,.md" class="sr-only">`.
  - `Выбрать папку` — `<input type="file" id="kb-folder" webkitdirectory class="sr-only">` (no `accept`; filtering is done in JS by extension, case-insensitive).
- Focal point: the pending list plus the primary `Индексировать` / `Добавить в базу` button in the modals; on the card, the `Добавление: {done} из {total}` line with its progress bar.
- Both inputs are reset (`value = ''`) after each choice so the same file or folder can be chosen again.
- Keyboard: inputs remain focusable though `sr-only`; the label shows `focus-within:ring-2 focus-within:ring-indigo-500`.
- Helper line under the controls (text-xs slate-500): `Можно выбирать несколько раз — выбранное добавляется в общий список. До {MAX_FILES} файлов, до {MAX_FILE_MB} МБ на файл, до {MAX_TOTAL_MB} МБ всего.` Limits come from the server constant values exposed to the page (single source: `agent/kb_limits.py`); never hardcode a second copy of the number in copy that can drift. First-render state: until the limit values have been loaded from the server, submit stays disabled (or the helper line shows no numbers); the front end never falls back to hardcoded numbers. The mechanism for delivering the limits is a planner decision.

### 2. Folder choice report (shown under the helper line right after a folder is chosen)

- text-xs, slate-300, `role="status"`: `Из папки «{folder}» взято {taken} {файл|файла|файлов}, пропущено {skipped} {файл|файла|файлов} неподдерживаемого типа.`
- When skipped is 0: `Из папки «{folder}» взято {taken} {…}.` (no skipped clause).
- When taken is 0: text-xs red-400: `В папке «{folder}» нет файлов PDF, TXT или MD.`
- Report is replaced on each new choice (shows the latest choice only); the cumulative skipped total is NOT shown. Use existing `ruPlural` for plurals.
- Shown before the upload starts (KBUP-01); counts are computed client-side.

### 3. Pending list (`#kb-file-list`, accumulating)

- Container: `ul` with `mt-2 space-y-1 text-xs text-slate-300 max-h-48 overflow-y-auto`.
- Above the list, a header row (text-xs slate-400, flex justify-between): `В списке: {n} {файл|файла|файлов} · {total size}` on the left; `Очистить список` text button (`text-slate-400 hover:text-white`) on the right, shown only when n >= 2.
- Row (unchanged style): `flex items-center justify-between gap-2 rounded bg-slate-800 px-2 py-1`; label `truncate`, text `{sourceName} · {size}` where `sourceName` is the relative path (`docs/API_SPEC.md`) for folder files and the plain file name for singly chosen files (D-05); `title` attribute carries the full source name.
- Remove button: `×`, `aria-label="Убрать файл {sourceName}"`, min hit area enlarged with `px-1`; removes only that entry.
- Duplicates (same source name already pending) are not added (D-07); a text-xs slate-400 note replaces the folder report line: `Уже в списке: {k} {файл|файла|файлов} — повторно не добавлено.`
- Over-limit state (pending count > MAX_FILES or any file > per-file limit or total > total limit): rows stay, header turns red-400 with the message below, and the submit button is disabled. Server still validates (defence in depth).
- Empty list: no header, no rows (list is simply empty, as today).
- Text is always set via `textContent`; no HTML insertion, so no DOMPurify call is needed for these nodes.

### 4. Knowledge base card — add files (ready bases only, D-08, D-11)

- Existing `actions` row (`flex items-center justify-between gap-2 pt-1`) gets a second neutral button between `Тест поиска` and the delete controls, same class string as `Тест поиска`: label `Добавить файлы`.
- Visible and enabled only when `kb.status === 'ready'` and no addition is running; otherwise omitted for `queued`/`indexing`/`failed`. While an addition is running (which only happens on a `ready` base) it is rendered disabled with `title="Идёт добавление файлов"`.
- Click opens a compact modal `#kb-add-modal` using the same shell as `#kb-create-modal` (`max-w-lg`, `bg-slate-900 border border-slate-700 rounded-xl`), title (text-lg 600): `Добавить файлы в «{kb.name}»`. Contents: the same two dashed controls, folder report, helper line, pending list (sections 1–3, separate state from the create form; `#kb-add-modal` uses its own distinct IDs for the file input, folder input and pending list, not the `#kb-file-list`, `#kb-files`, `#kb-folder` IDs of the create modal), and a primary submit button `Добавить в базу` (accent). No strategy/size/overlap/model fields; a text-xs slate-500 line states: `Файлы будут проиндексированы с параметрами этой базы: {strategy}, {embedding model short name}.` (D-10).
- Closing the modal returns focus to the `Добавить файлы` button (reuse `state.kbModalOpener` / `closeKbModal`).
- Submit disabled while the pending list is empty or over limit, and while the request is in flight (label `Добавляю…`).

### 5. Card state during and after an addition (D-08, D-09, D-14)

- The chip stays `готово` (emerald) throughout; the card is NOT moved to the `indexing` look, and `Тест поиска` stays enabled.
- While an addition runs, a second line is added under the meta row: text-xs sky-400 `Добавление: {done} из {total}` plus the existing `kbProgressBar` (sky-400 fill, `role="progressbar"` with aria-valuenow/max). Both are driven by the same throttled `/ws/events` progress events used for indexing.
- On success the progress line is replaced for the lifetime of the card state by a summary line (text-xs, slate-300, `role="status"`): `Добавлено: {N}, заменено: {M}, без изменений: {K}`. Clears on the next addition, on page reload, or when the user clicks it away (`×` button, `aria-label="Скрыть сводку"`). Zero counts are still shown.
- On failure the chip stays `готово`; below the meta row a red-400 text-xs error line (same `line-clamp-2` + click-to-expand pattern as the existing failed-card error): `Не удалось добавить файлы: {server message}. База осталась без изменений.`
- Add-in-progress on page load: an addition already running when the page loads must still show the progress line and bar and keep the `Добавить файлы` button disabled. Whether the server reports the "addition running" state is a planner decision.
- File count and chunk count in the meta row update only after a successful addition.
- Success toast: `showToast('Файлы добавлены в базу «{name}»', 'success')`. Rejection toast uses `'error'`.

### 6. Sources display (KBUP-03)

- Everywhere a source is displayed — test-search results, source lists under answers, citations — render the stored relative path verbatim (`docs/API_SPEC.md`), `truncate` with the full path in `title`. No basename reduction, no separate "folder" column. Two entries `docs/a/README.md` and `docs/b/README.md` must appear as two distinct sources.

---

## Copywriting Contract

All user-facing strings in Russian (matches the shipped knowledge base UI).

| Element | Copy |
|---------|------|
| Primary CTA (create) | `Индексировать` (unchanged) |
| Primary CTA (add) | `Добавить в базу` |
| Secondary controls | `Выбрать файлы`, `Выбрать папку`, `Очистить список`, card button `Добавить файлы` |
| Empty state heading | `Баз знаний пока нет` (unchanged) |
| Empty state body | `Нажмите «+ Новая база знаний», загрузите PDF, TXT или MD (можно целую папку) и запустите индексацию.` |
| Pending list empty hint | `Список пуст. Выберите файлы или папку.` (text-xs slate-500, shown only after the user cleared or removed everything, not on first open) |
| Folder report | `Из папки «{folder}» взято {taken} {файл\|файла\|файлов}, пропущено {skipped} {файл\|файла\|файлов} неподдерживаемого типа.` |
| Folder with nothing usable | `В папке «{folder}» нет файлов PDF, TXT или MD.` |
| Limit error: count | `Слишком много файлов: {n} из допустимых {MAX_FILES}. Уберите лишние из списка или разделите загрузку на несколько частей.` |
| Limit error: file size | `Файл «{sourceName}» больше {MAX_FILE_MB} МБ. Уберите его из списка.` |
| Limit error: total size | `Общий размер {x} МБ превышает {MAX_TOTAL_MB} МБ. Уберите часть файлов или загрузите их по частям.` |
| Server rejection (any limit) | Show the server `detail` text as-is in `#kb-create-error` / add-modal error line (red-400, `role="alert"`); nothing is stored and the pending list is kept so the user can fix it and retry. |
| Add summary | `Добавлено: {N}, заменено: {M}, без изменений: {K}` |
| Add failure | `Не удалось добавить файлы: {server message}. База осталась без изменений.` |
| Add progress | `Добавление: {done} из {total}` |
| Add unavailable tooltip | `Идёт добавление файлов` (while running); button hidden for non-ready bases |
| Destructive confirmation | No new destructive actions. Replacement of a changed file has no confirmation by decision (D-12); removing a pending entry is immediate and reversible by re-choosing. Existing delete flow `Точно удалить?` / `Отмена` is unchanged. |

---

## Interaction & State Rules

1. Selections accumulate (D-06): every choice from either input appends to the pending list; nothing is replaced.
2. Source name: folder choice uses `file.webkitRelativePath` (already starts with the chosen folder name, forward slashes); single files use `file.name` (D-05). Backslashes, if any, are normalized to `/`.
3. Extension filter is case-insensitive over `.pdf`, `.txt`, `.md`; everything else is counted as skipped. No directory-name filtering (D-03).
4. A file already ready in the base with changed content is replaced silently; unchanged is skipped; absent files are untouched (D-12, D-13, D-15). The UI never asks the user to resolve duplicates.
5. After the create or add request is accepted (202) the modal closes, the pending state is cleared, and progress is shown on the card. A rejected request (4xx) keeps the modal open with the pending list intact.
6. Reduced motion: pulse indicator keeps `motion-reduce:animate-none`.
7. Accessibility: every control has a visible text label or `aria-label`; error line is `role="alert"`, reports and summary are `role="status"`; modal keeps `role="dialog" aria-modal="true" aria-labelledby`; Escape closes the add modal like the existing modals.

---

## Registry Safety

| Registry | Blocks Used | Safety Gate |
|----------|-------------|-------------|
| shadcn official | none | not applicable |
| third-party | none | not applicable |

External assets remain the existing CDN libraries (Tailwind, Marked.js, DOMPurify); no new ones.

---

## Checker Sign-Off

- [x] Dimension 1 Copywriting: PASS
- [x] Dimension 2 Visuals: PASS
- [x] Dimension 3 Color: PASS
- [x] Dimension 4 Typography: PASS
- [x] Dimension 5 Spacing: PASS
- [x] Dimension 6 Registry Safety: PASS

**Approval:** approved 2026-10-13
