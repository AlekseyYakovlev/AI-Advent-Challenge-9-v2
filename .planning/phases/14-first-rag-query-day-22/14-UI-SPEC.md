---
phase: 14
slug: first-rag-query-day-22
status: draft
shadcn_initialized: false
preset: none
created: 2026-10-03
---

# Phase 14 — UI Design Contract

> Visual and interaction contract for the chat-header RAG controls, per-answer mode label, collapsed "Источники" block and RAG warning line. Reuses the existing Tailwind-CDN dark theme (`ui/static/index.html`, `app.js`) and Phase 13 source-card conventions. Decisions source: 14-CONTEXT.md D-01..D-08, 13-UI-SPEC.md. No frontend dependencies added.

---

## Design System

| Property | Value |
|----------|-------|
| Tool | none (vanilla JS + Tailwind CDN; shadcn not applicable per CLAUDE.md) |
| Preset | not applicable |
| Component library | none (hand-built DOM; mirrors `buildToolCallCard` `<details>` and Phase 13 `kb-search-modal` cards) |
| Icon library | none (Unicode glyphs only) |
| Font | Tailwind default system sans; `font-mono` only for chunk_id |

---

## Spacing Scale

| Token | Value | Usage |
|-------|-------|-------|
| xs | 4px | `mt-1`, gaps inside badges |
| sm | 8px | `gap-2` between header controls, `space-y-2` between source rows |
| md | 16px | header `px-4`, message-block gaps |
| lg | 24px | reserved |
| xl | 32px | reserved |
| 2xl | 48px | reserved |
| 3xl | 64px | reserved |

Exceptions (exhaustive; every off-scale class used in this spec is listed here, all inherited from existing UI for visual consistency):
- `py-1.5` (6px vertical) on `#rag-toggle`, `#rag-kb-select`, `#rag-k-input`: identical to existing `#model-select`, so header controls align at the same height.
- `px-3` / `p-3` / `py-2` mix (12px) on the toggle, selects, source cards and sources `<details>` (`px-3 py-2`): identical to existing `.tool-call-card` and Phase 13 result cards (`p-3`).
- `px-2 py-0.5` (8px / 2px) on badges: inherited from existing chips.
- `px-2 py-1.5` on the K input (8px / 6px): compact numeric field, vertical matches `#model-select`.
- `w-16` (64px) K input width; `max-w-[12rem]` (192px) on badge and KB select.
- `gap-x-2`, `mt-2`, `space-y-2` (8px) and `mt-1` (4px) are on-scale.

---

## Typography

| Role | Size | Weight | Line Height |
|------|------|--------|-------------|
| Body (source snippet, header selects/inputs) | 14px (`text-sm`) | 400 | 1.5 |
| Label / meta (badge, per-answer mode label, `<summary>`, source header line, warning line, toggle text) | 12px (`text-xs`) | 400 (badge active state and summary 600) | 1.5 |
| Heading | not added (existing 18px/600 chat title unchanged) | 600 | 1.2 |
| Display | not used | - | - |

Weights: exactly 2 (400, 600).

---

## Color

| Role | Value | Usage |
|------|-------|-------|
| Dominant (60%) | `bg-slate-950` | app background |
| Secondary (30%) | `bg-slate-900` header/message surfaces; `bg-slate-800` selects, inputs, source cards; borders `slate-700` | |
| Accent (10%) | `indigo-600` (#4f46e5) | see list below |
| Destructive | `red-400` | error toast / error text only |
| Warning (semantic) | `yellow-400` text on `bg-yellow-900/30`, border `yellow-700` | RAG-04 warning line only (matches existing `warning` toast `bg-yellow-900/90`) |

Accent reserved for: RAG toggle in the "с RAG" (on) state, focus rings (`focus:ring-2 focus:ring-indigo-500`) on the new header controls, and the focus-visible ring (`ring-indigo-500`) on the sources `<summary>`. Nothing else (badge, labels, source scores stay slate; scores use `text-slate-200`).

RAG-off state: toggle track `bg-slate-700`, text `без RAG`.

---

## Interaction & Layout Contract

### Focal points
- Header: the accent-filled "с RAG" toggle is the focal point; badge, KB select and K input are subordinate (neutral slate).
- Message area: the answer text is the focal point; the warning line, mode label and sources block are visually subordinate (12px, muted slate, collapsed by default). The warning line is the only exception when present, by design.

### Header controls (D-01, D-02, D-03)
Placed in `<header>` between `#chat-title` block and `#model-select`, in this order, `flex items-center gap-2`:
1. `#rag-badge` — status chip `rounded-full bg-slate-800 border border-slate-700 px-2 py-0.5 text-xs text-slate-300`, `max-w-[12rem] truncate`, `title` = full text. Text: `RAG: {имя БЗ}` when on, `без RAG` when off. Hidden-until-chat-selected is not required; with no chat selected all RAG controls are `disabled`.
2. `#rag-toggle` — `<button role="switch" aria-checked>` pill, `rounded-lg px-3 py-1.5 text-sm border`. On: `bg-indigo-600 border-indigo-500 text-white`, label `с RAG`. Off: `bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700`, label `без RAG`. Label text always present (never color-only). One click toggles and PUTs `/api/v1/chats/{id}/rag`. Disabled with `disabled:opacity-50 disabled:cursor-not-allowed` and `title="Выберите базу знаний"` when no KB attached.
3. `#rag-kb-select` — `<select>` styled like `#model-select` (`rounded-lg bg-slate-800 border border-slate-700 px-3 py-1.5 text-sm max-w-[12rem] truncate`). First option `— без базы знаний —` (value empty, detaches KB, also forces mode off). Lists only `ready` KBs of the user. Changing it PUTs immediately. KB stays attached while RAG is off.
4. `#rag-k-wrap` — shown only when RAG is on: `<label for="rag-k-input" class="text-xs text-slate-500">K</label>` + `<input id="rag-k-input" type="number" min="1" max="20" step="1" value="5" class="w-16 rounded-lg bg-slate-800 border border-slate-700 px-2 py-1.5 text-sm">`. Commits on `change`/blur (not each keystroke); out-of-range values clamp to 1..20 and the field shows the clamped value. This wrapper is the anchor for Phase 15 candidate-K/threshold.

Behaviors: controls reflect `GET /rag` on chat switch; on PUT failure revert control to previous value and show error toast. If the attached KB is deleted/not ready, badge reads `RAG: база недоступна` in `text-yellow-400`, toggle stays on until user changes it (the turn degrades per D-04). Do not disable controls during streaming of a turn, but changes apply from the next turn (no mid-stream effect).
Narrow widths: header may `flex-wrap`; `#rag-badge` is dropped (`hidden md:inline-flex`) because toggle label and select already show state.

### Per-answer mode label (D-03)
Under each assistant message content, a single meta line (`mt-1 text-xs text-slate-500`): `с RAG` or `без RAG` (plus `· K={N}` when RAG). Rendered for every assistant message produced after this phase ships (mode stored in `rag_sources`); messages with no stored mode render no label. `textContent` only.

### Sources block (D-05, D-06, D-08)
- Rendered after the done frame (`done.rag`) and from stored `rag_sources` on history load, directly below the mode label, only when `sources.length > 0`.
- `<details class="rag-sources rounded-lg border border-slate-700 bg-slate-900 text-xs text-slate-300 px-3 py-2 mt-2 max-w-[75%]">`, collapsed by default, same visual family as `.tool-call-card`. `<summary class="cursor-pointer select-none font-semibold">Источники ({N})</summary>`; summary has visible focus ring via `focus-visible:ring-2 ring-indigo-500`.
- Body `mt-2 space-y-2`; each row is a card `bg-slate-800 border border-slate-700 rounded-lg p-3 space-y-1` (Phase 13 test-search card):
  - Header line (12px, `flex flex-wrap gap-x-2`): rank `#1`, score `0.742` (3 decimals, `text-slate-200`), file name (`truncate`, `title` full), section breadcrumb (`truncate`, slate-400), chunk_id `font-mono text-slate-500`. Rank number corresponds to the `[N]` citations in the answer.
  - Snippet (14px, `line-clamp-4`, `whitespace-pre-wrap`) fetched lazily by chunk_id when the `<details>` is first opened; toggle text-button `показать полностью` / `свернуть` (`aria-expanded`). Loading line: `Загрузка фрагмента…`.
  - If KB deleted or chunk not found: snippet area replaced by `text-slate-500` line `Текст фрагмента недоступен: база знаний удалена`; metadata row still shown.
- All dynamic text via `textContent`; never `innerHTML` for KB data.
- Streaming: no placeholder block before `done` (D-08). Block appears when `done` arrives; no layout jump beyond appending below the message.

### Warning line (D-07)
- Persistent line under the answer (above the mode label, below message content): `mt-2 rounded-lg border border-yellow-700 bg-yellow-900/30 px-3 py-2 text-xs text-yellow-400`, `role="status"`, prefix `⚠ ` then reason text from `rag_sources.warning`. Rendered from `done.rag.warning` and again from stored data after reload.
- In addition, `showToast(<same short text>, 'warning')` once when the done frame carries a warning (not on history reload).
- When a warning exists the mode label reads `без RAG (сбой поиска)` and no sources block is rendered.

### States
| State | Behavior |
|-------|----------|
| RAG off, no KB | toggle disabled, badge `без RAG`, select shows placeholder, K hidden |
| RAG off, KB attached | toggle enabled (one click turns on), badge `без RAG`, K hidden |
| RAG on | toggle accent, badge `RAG: {имя}`, K visible |
| No ready KBs | select has only placeholder; helper `title` on select: `Нет готовых баз знаний. Создайте базу в боковой панели.` |
| Retrieval returned 0 chunks | mode label `с RAG`, no sources block, muted line `Подходящих фрагментов не найдено` (`text-xs text-slate-500`) |
| Retrieval failure | warning line + toast (above) |

### Accessibility
Labels for all inputs (`aria-label` on selects, `<label for>` on K); toggle uses `role="switch"` + `aria-checked`; state never color-only; keyboard operable (Enter/Space on toggle); warning line `role="status"`; `<details>` natively keyboard accessible.

---

## Copywriting Contract

| Element | Copy |
|---------|------|
| Primary CTA | Toggle `с RAG` / `без RAG` (no new submit button; the existing Send button is unchanged) |
| Header badge | `RAG: {имя БЗ}` · `без RAG` · `RAG: база недоступна` |
| KB select placeholder | `— без базы знаний —` |
| K label | `K` (`title`: `Сколько фрагментов передавать в модель (1–20)`) |
| Sources summary | `Источники ({N})` |
| Snippet toggles | `показать полностью` · `свернуть` |
| Per-answer label | `с RAG · K={N}` · `без RAG` |
| Empty state heading | `Подходящих фрагментов не найдено` |
| Empty state body | `Ответ дан без фрагментов базы знаний. Переформулируйте вопрос или проверьте, что в базе есть нужный документ.` (title on the muted line) |
| Warning: embedder | `Модель эмбеддинга не загружена. Ответ дан без базы знаний. Загрузите модель в LM Studio и повторите вопрос.` |
| Warning: KB deleted | `База знаний удалена. Ответ дан без базы знаний. Выберите другую базу в заголовке чата.` |
| Warning: KB not ready | `База знаний ещё не готова. Ответ дан без базы знаний. Дождитесь окончания индексации.` |
| Warning: dim mismatch | `Размерность эмбеддинга не совпадает с индексом базы. Ответ дан без базы знаний. Проверьте модель эмбеддинга базы.` |
| Toast on warning | `Поиск по базе знаний не удался — ответ дан без RAG` |
| Error: save RAG settings | `Не удалось сохранить настройки RAG. Проверьте соединение и попробуйте снова.` |
| Error: LM Studio down | `LM Studio не запущен. Запустите его и попробуйте снова.` |
| Snippet unavailable | `Текст фрагмента недоступен: база знаний удалена` |
| Destructive confirmation | none in this phase. Toggling RAG and detaching a KB are non-destructive and reversible; KB deletion stays in the Phase 13 inline two-step flow. |

---

## Registry Safety

| Registry | Blocks Used | Safety Gate |
|----------|-------------|-------------|
| shadcn official | none (not initialized, not applicable) | not required |
| third-party | none | not applicable |

Frontend libraries: existing CDN Tailwind, Marked.js, DOMPurify only.

---

## Checker Sign-Off

- [ ] Dimension 1 Copywriting: PASS
- [ ] Dimension 2 Visuals: PASS
- [ ] Dimension 3 Color: PASS
- [ ] Dimension 4 Typography: PASS
- [ ] Dimension 5 Spacing: PASS
- [ ] Dimension 6 Registry Safety: PASS

**Approval:** pending
