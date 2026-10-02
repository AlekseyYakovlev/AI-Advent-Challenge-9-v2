---
phase: 13
slug: knowledge-base-indexing-day-21
status: approved
reviewed_at: 2026-10-03
shadcn_initialized: false
preset: none
created: 2026-10-03
---

# Phase 13 — UI Design Contract

> Visual and interaction contract for the "База знаний" sidebar block, the KB create modal and the test-search modal. Reuses the existing Tailwind-CDN dark theme in `ui/static/index.html` / `app.js`. Source of decisions: 13-CONTEXT.md (D-06, D-07, D-10, D-15..D-17) and the existing Scheduler panel/modal patterns.

---

## Design System

| Property | Value |
|----------|-------|
| Tool | none (vanilla JS + Tailwind CDN; shadcn not applicable per CLAUDE.md) |
| Preset | not applicable |
| Component library | none (hand-built DOM, mirrors Scheduler panel + `scheduler-create-modal`) |
| Icon library | none (Unicode glyphs only: `▸`, `&times;`) |
| Font | Tailwind default sans stack (system UI); `font-mono` only for chunk_id |

---

## Spacing Scale

Tailwind 4px grid, as used by existing blocks:

| Token | Value | Usage |
|-------|-------|-------|
| xs | 4px | gaps inside chips, `mt-1` helper text |
| sm | 8px | `space-y-2` between KB rows/cards, `gap-2` button rows |
| md | 16px | modal field spacing `space-y-4`, `px-4 py-2` buttons |
| lg | 24px | reserved |
| xl | 32px | reserved |
| 2xl | 48px | reserved |
| 3xl | 64px | reserved |

Layout facts: sidebar block uses `p-3`, `border-t border-slate-800`, `max-h-72 overflow-y-auto`; modal body `p-5`, header `px-5 py-4`; modal width `max-w-lg` (create) and `max-w-2xl` (test search), `mx-4`, `max-h-[90vh] overflow-y-auto`.

Exceptions: chip padding `px-2 py-0.5` (2px vertical) and the `w-2 h-2` status dot (8px) are inherited from existing chips; close button `×` keeps existing `text-xl` hit area.

---

## Typography

Inherited from existing UI; only these sizes are used in this phase.

| Role | Size | Weight | Line Height |
|------|------|--------|-------------|
| Body (inputs, buttons, KB name, snippet) | 14px (`text-sm`) | 400 (buttons/name 600) | 1.5 |
| Label / meta (sidebar text, chips, helper, hints, scores) | 12px (`text-xs`) | 400 | 1.5 |
| Heading (modal titles) | 18px (`text-lg`) | 600 | 1.2 |
| Display | not used | - | - |

Weights: exactly 2 (400 regular, 600 semibold; map `font-medium`/`font-semibold` to 600). Sidebar block title "База знаний" uses 12px/600 like "Память"/"Расписание".

---

## Color

| Role | Value | Usage |
|------|-------|-------|
| Dominant (60%) | `bg-slate-950` (#020617) | app background; modal overlay `bg-black/60` |
| Secondary (30%) | `bg-slate-900` (#0f172a) sidebar/modal surface; `bg-slate-800` inputs, KB row cards, result cards; borders `slate-700/800` | |
| Accent (10%) | `indigo-600` (#4f46e5), hover `indigo-500` | see list below |
| Destructive | `red-400` text / `red-600` confirm button | Delete + inline errors only |

Status chip colors (semantic, text on `bg-slate-800`): queued `text-slate-400`; indexing `text-sky-400` with `animate-pulse motion-reduce:animate-none` dot (matches scheduler running badge); ready `text-emerald-400`; failed `text-red-400`.

Accent reserved for: "+ Новая база знаний" button, "Индексировать" submit button, "Найти" button in test-search, focus rings (`focus:ring-indigo-500`), progress bar fill during indexing. Nothing else.

---

## Interaction & Layout Contract

### Sidebar block "База знаний"
- Placed after `scheduler-panel`, before `agent-status`. Same markup as Scheduler: `id="kb-panel"`, `border-t border-slate-800 p-3 text-xs text-slate-400 overflow-y-auto max-h-72`, h3 with title + count (`kb-count`) + fold toggle `▸` (`data-fold-toggle="kb-panel-body"`, default collapsed).
- Body: full-width accent button "+ Новая база знаний", then `kb-list` (`space-y-2 mt-2`).
- KB row (card `rounded-lg bg-slate-800 border border-slate-700 p-2`):
  1. Line 1: name (14px/600, `truncate`, `title` = full name) + status chip right-aligned.
  2. Line 2 (12px, slate-500): "N файлов · M чанков" and short embedding model name (last path segment, truncated, `title` = full id).
  3. While `queued`/`indexing`: 4px-high progress bar (`bg-slate-700` track, `bg-indigo-500` fill, `transition-all`) with chip text "индексация x из y".
  4. Failed: error text in `text-red-400` 12px, clamped to 2 lines, click row to expand full text (`aria-expanded`), also in `title`.
  5. Actions row: "Тест поиска" (secondary `bg-slate-700`, `disabled:opacity-50 disabled:cursor-not-allowed`, disabled unless `ready`) and "Удалить" (text `red-400`).
- Delete: inline two-step confirm in the row (button turns to "Точно удалить?" + "Отмена" for 4 s; no native `confirm()`). Allowed in every status (D-02).
- Live updates: `kb_progress` events on `/ws/events` update chip/bar in place (throttled by server); REST list refetch is the fallback on WS reconnect. Progress updates must not steal focus or reset a row's expanded/confirm state.
- Status text map: `queued` -> "в очереди", `indexing` -> "индексация x из y", `ready` -> "готово", `failed` -> "ошибка". Chip also carries text (never color-only).

### Create modal (`kb-create-modal`)
- Structure copies `scheduler-create-modal`: overlay `fixed inset-0 z-50 bg-black/60`, `role="dialog" aria-modal="true" aria-labelledby`, header with title + `×`.
- Closes ONLY via `×` (Phase 10): no overlay click, no Escape, no "Отмена" button. After successful submit (202) it closes programmatically.
- Fields in order: Название (text, maxlength 200, required); Файлы (`<input type="file" multiple accept=".pdf,.txt,.md">` styled as dashed-border drop-zone `border-dashed border-slate-700`, selected files listed below with name + size, per-item remove `×`); Стратегия разбиения (select: "Фиксированная длина" / "Структурная"); Размер чанка (number, default 1000, min 100) and Перекрытие (number, default 150) in a 2-column grid, both shown only for fixed, structural shows helper "Делит по главам, статьям и заголовкам; размер — верхний предел"; Модель эмбеддингов (select, default `giga-embeddings-instruct-480m-0826`, filtered per D-07) with checkbox "показать все модели" and button "Проверить эмбеддинг" (secondary) with result line `text-xs` ("Размерность: 1024" emerald / error red).
- Error line `<p id="kb-create-error" class="text-xs text-red-400">` directly above the submit row, `role="alert"`, shows server `detail`; modal stays open and file selection is preserved (D-06).
- Submit "Индексировать" (accent); while request is in flight it is disabled and reads "Загрузка…".
- Footer is a single right-aligned button (no Cancel).

### Test-search modal (`kb-search-modal`)
- Same shell, `max-w-2xl`, closes only via `×`. Title "Тест поиска: {имя}" (set via `textContent`).
- Query row: text input (placeholder "Введите вопрос…", Enter submits) + accent button "Найти". Fixed K=5 (no control). Button disabled and reads "Поиск…" in flight.
- Results: 5 cards (`bg-slate-800 border border-slate-700 rounded-lg p-3 space-y-1`). Header line 12px: rank "#1", score "0.742" (3 decimals, `text-indigo-300` is NOT used; use `text-slate-200`), source filename, section (truncate), chunk_id in `font-mono text-slate-500`. Body 14px snippet clamped to ~4 lines (`line-clamp-4`) with text-button "показать полностью" / "свернуть" toggling clamp (`aria-expanded`).
- All dynamic text assigned via `textContent`; no `innerHTML` for KB data.
- States: initial (hint line), loading, empty result, error (inline `text-red-400`, modal stays open).

### Chat model picker
- Hides models with `type == "embeddings"` (D-07); no visual change otherwise.

### Accessibility
- All inputs have `<label for>`; focus ring `focus:outline-none focus:ring-2 focus:ring-indigo-500`; status never color-only; progress bar has `role="progressbar"` with `aria-valuenow/max`; reduced motion respected on pulse.

---

## Copywriting Contract

| Element | Copy |
|---------|------|
| Sidebar title | База знаний |
| Primary CTA (sidebar) | + Новая база знаний |
| Primary CTA (modal) | Индексировать |
| Secondary actions | Тест поиска · Проверить эмбеддинг · Найти · показать полностью · свернуть |
| Modal titles | Новая база знаний · Тест поиска: {имя} |
| Field labels | Название · Файлы (PDF, TXT, MD) · Стратегия разбиения · Размер чанка (символов) · Перекрытие (символов) · Модель эмбеддингов · показать все модели |
| Empty state heading | Баз знаний пока нет |
| Empty state body | Нажмите «+ Новая база знаний», загрузите PDF, TXT или MD и запустите индексацию. |
| Empty search results | Ничего не найдено. Попробуйте переформулировать вопрос. |
| Test-search disabled hint (`title`) | Поиск доступен после завершения индексации |
| Embedding check success | Размерность: {N} |
| Error: files missing | Выберите хотя бы один файл. |
| Error: empty name | Введите название базы знаний. |
| Error: duplicate (server) | Файл {имя} уже добавлен. |
| Error: size/overlap (server) | e.g. «Размер чанка должен быть не меньше 100», «Перекрытие должно быть меньше размера чанка и не больше его половины» |
| Error: indexing failed (chip hover/expand) | Server text, e.g. «Не удалось извлечь текст из файла {имя}. Возможно, это скан без текстового слоя.» / «Модель {X} не найдена или не загружается в LM Studio.» |
| Error: LM Studio down | LM Studio не запущен. Запустите его и попробуйте снова. |
| Error: generic network | Не удалось выполнить запрос. Проверьте соединение и попробуйте снова. |
| Destructive confirmation | Удалить: «Точно удалить?» + «Отмена» (inline). Toast after success: «База знаний удалена». Delete of an indexing KB additionally warns via `title`: «Индексация будет прервана». |

Destructive actions in this phase: only KB delete (all statuses, inline two-step confirm, no modal).

---

## Registry Safety

| Registry | Blocks Used | Safety Gate |
|----------|-------------|-------------|
| shadcn official | none (not initialized, not applicable) | not required |
| third-party | none | not applicable |

Frontend libraries: only existing CDN Tailwind, Marked.js, DOMPurify; no new frontend dependencies.

---

## Checker Sign-Off

- [ ] Dimension 1 Copywriting: PASS
- [ ] Dimension 2 Visuals: PASS
- [ ] Dimension 3 Color: PASS
- [ ] Dimension 4 Typography: PASS
- [ ] Dimension 5 Spacing: PASS
- [ ] Dimension 6 Registry Safety: PASS

**Approval:** pending
