---
phase: 15
slug: reranking-and-filtering-day-23
status: approved
reviewed_at: 2026-10-03
shadcn_initialized: false
preset: none
created: 2026-10-03
---

# Phase 15 — UI Design Contract

> Visual and interaction contract for the "Поиск ⚙" popover, the "Детали поиска" block and the below-threshold grey line. Extends 14-UI-SPEC.md (same Tailwind-CDN dark theme, same tokens); nothing from Phase 14 is re-specified here. Decisions source: 15-CONTEXT.md D-01..D-14. No frontend dependencies added. Phase 14 is planned but not executed, so `#rag-k-wrap` and the "Источники" block are taken from 14-UI-SPEC.md as the anchors.

---

## Design System

| Property | Value |
|----------|-------|
| Tool | none (vanilla JS + Tailwind CDN; shadcn not applicable per CLAUDE.md) |
| Preset | not applicable |
| Component library | none (hand-built DOM; popover mirrors header controls, details block mirrors `.tool-call-card` and 14 "Источники") |
| Icon library | none (Unicode glyphs only: ⚙ ▸ ✓ ≈ ↷ →) |
| Font | Tailwind default system sans; `font-mono` only for chunk_id; `tabular-nums` on score columns |

---

## Spacing Scale

Inherited unchanged from 14-UI-SPEC.md.

| Token | Value | Usage |
|-------|-------|-------|
| xs | 4px | `mt-1`, `gap-1`, chip inner gaps |
| sm | 8px | `gap-2`, `space-y-2` between popover rows, table cell `px-2` |
| md | 16px | popover width steps, block gaps |
| lg | 24px | reserved |
| xl | 32px | reserved |
| 2xl | 48px | reserved |
| 3xl | 64px | reserved |

Exceptions (all inherited from existing UI / Phase 14, exhaustive for this phase):
- `py-1.5` (6px) on `#rag-search-btn` and popover inputs: matches `#rag-k-input` and `#model-select` height.
- `px-3` / `p-3` / `py-2` (12px): popover panel `p-3`, details block `px-3 py-2`, same as 14 sources block.
- `px-2 py-0.5` (8px/2px) on status chips; `py-1` (4px) on table rows.
- `w-72` (288px) popover width; `w-20` (80px) numeric inputs in popover; `max-w-[75%]` details block (same as 14 sources block); `max-h-80` (320px) scroll area for the candidates table.

---

## Typography

| Role | Size | Weight | Line Height |
|------|------|--------|-------------|
| Body (popover inputs) | 14px (`text-sm`) | 400 | 1.5 |
| Label / meta (popover labels and switch text, `<summary>`, header lines, table cells, chips, grey line, skip lines) | 12px (`text-xs`) | 400 (summary, table header row and active-stage names 600) | 1.5 |
| Heading | not added | 600 | 1.2 |
| Display | not used | - | - |

Weights: exactly 2 (400, 600). Sizes in use: exactly 2 (12, 14).

---

## Color

| Role | Value | Usage |
|------|-------|-------|
| Dominant (60%) | `bg-slate-950` | app background |
| Secondary (30%) | `bg-slate-900` popover panel and details block; `bg-slate-800` inputs and table header; borders `slate-700` | |
| Accent (10%) | `indigo-600` (#4f46e5) | see list below |
| Destructive | `red-400` | error toast / error text only (settings save failure) |
| Warning (semantic) | `yellow-400` on `bg-yellow-900/30`, border `yellow-700` | not used by Phase 15: stage skips and below-threshold are deliberately neutral (D-09, D-14) |

Accent reserved for: checked state of the four stage switches in the popover (`bg-indigo-600`), `focus:ring-2 focus:ring-indigo-500` on popover inputs/switches/button, `focus-visible:ring-2 ring-indigo-500` on the "Детали поиска" `<summary>`, and the "Поиск ⚙" button border/text only while any optional stage is on (`border-indigo-500 text-white`; otherwise neutral slate). Nothing else: scores `text-slate-200`, chips and the grey line stay slate.

Neutral text hierarchy for the details block: `text-slate-300` default, `text-slate-200` for scores and «в ответе» rows, `text-slate-500` for cut rows (ниже порога / вне top-K / не вошло в бюджет) and the grey line.

---

## Interaction & Layout Contract

### Focal points
- Header: unchanged, the "с RAG" toggle remains the focal point. "Поиск ⚙" is subordinate (neutral slate button).
- Message area: the answer text remains the focal point; the grey line and the collapsed "Детали поиска" are subordinate (12px, muted, collapsed).

### "Поиск ⚙" button and popover (D-01, D-02, D-03, D-10)
- `#rag-search-btn` sits inside `#rag-k-wrap` directly after the K input, so it is shown and hidden with RAG on/off (14 rule). `type="button"`, `rounded-lg border border-slate-700 bg-slate-800 hover:bg-slate-700 px-3 py-1.5 text-xs text-slate-300`, text `Поиск ⚙`, `aria-haspopup="dialog"`, `aria-expanded`. Active-stage count is not shown as a number; the indigo border state (see Color) signals "something extra is on" together with the switch states inside.
- Popover `#rag-search-popover`: `role="dialog"`, `aria-label="Настройки поиска"`, absolutely positioned under the button, right-aligned to it (`absolute right-0 top-full mt-2 z-30 w-72 rounded-lg border border-slate-700 bg-slate-900 p-3 shadow-lg space-y-2`; wrapper is `relative`). At narrow widths it stays within the viewport (`max-w-[calc(100vw-2rem)]`).
- Rows, in this order, each `flex items-center justify-between gap-2 text-xs text-slate-300`:
  1. `Кандидатов` + number input `#rag-candidate-k` (`w-20`, `min` = current K, `max=50`, `step=1`, default 20). Value below K clamps up to K; K raised above candidate-K raises candidate-K to match (the field shows the clamped value). `title`: `Сколько фрагментов отобрать на первом этапе, до отсечения по порогу и реранка (K–50)`.
  2. `Порог` + number input `#rag-threshold` (`w-20`, `min=0`, `max=1`, `step=0.01`, 2 decimals). When `threshold` is NULL the field is shown as a placeholder-style value with the calibrated number and a muted suffix `(калибр.)` after it (`text-slate-500`); typing a number makes it a user override and reveals a text-button `сбросить` (`text-xs text-slate-400 hover:text-white underline`, own row under the field, right-aligned) that PUTs `threshold: null` and returns to the calibrated display. If the KB's model has no calibrated value (unknown model, D-10) the field shows `0.00 (нет калибровки)` and no cut is applied. `title`: `Минимальная косинусная близость. Фрагменты ниже порога не попадают в ответ. Применяется до реранка.`
  3. Four switches, each a `<label>` row with `<input type="checkbox" role="switch" class="sr-only peer">` + visible track (`h-5 w-9 rounded-full bg-slate-700 peer-checked:bg-indigo-600`, thumb `h-4 w-4 rounded-full bg-white`, 4px inset) + label text. Order and exact labels: `Лексич. реранк`, `LLM-реранк`, `Гибрид (FTS5)`, `Переписывание запроса`. Each has a `title` explaining it (see Copywriting). They are independent; no mutual exclusion (D-02, D-13).
- Behavior: opens on button click; closes on `Escape` (focus returns to the button), on outside click, and on button click again. Focus moves to the first input on open. Each control commits immediately on `change`/blur with a PUT `/api/v1/chats/{id}/rag` (switches on click, numbers on `change`, not per keystroke); on failure the control reverts and `showToast(..., 'error')`. No Save/Apply button. Controls are not disabled while a turn streams; changes apply from the next turn (14 rule). Popover content is rebuilt from `GET /rag` on chat switch and whenever the KB select changes (calibrated threshold follows the KB's model, D-10). Popover is force-closed when RAG is switched off.
- Keyboard: Tab order is inputs then switches then back out; Space toggles a switch; all inputs have `<label for>` or `aria-label`.

### Grey below-threshold line (D-09)
- Rendered under the answer content, above the mode label, only when stored verdict is `below_threshold`: `mt-2 text-xs text-slate-500`, plain text without icon or border, `role="status"`. Copy: `Фрагменты не прошли порог (лучший {best} < {threshold})`, both numbers 2 decimals. Persists after reload from stored data. No toast, no yellow styling.
- The 14 muted line `Подходящих фрагментов не найдено` is not shown in this case (the grey line replaces it). "Источники" block is absent (no chunks reached the model); "Детали поиска" is still rendered.

### "Детали поиска" block (D-04, D-05, D-06, D-14)
- Rendered after `done` (`done.rag`) and from stored `rag_sources` v2 on history load, directly below "Источники" (or below the mode label when there is no sources block). Shown for every RAG answer that has a trace; messages from before this phase (v1 sources without a trace) render no details block.
- Container: `<details class="rag-details rounded-lg border border-slate-700 bg-slate-900 text-xs text-slate-300 px-3 py-2 mt-2 max-w-[75%]">`, collapsed by default. `<summary class="cursor-pointer select-none font-semibold focus-visible:ring-2 ring-indigo-500">Детали поиска</summary>`. Body `mt-2 space-y-2`.
- Header lines (each `flex gap-x-2`, label `text-slate-500 w-24 shrink-0`, value `text-slate-300 break-words`):
  - `Запрос:` original query.
  - `Переписан:` rewritten query; row present only when rewrite ran and its output was accepted.
  - `Этапы:` one line, parts joined with ` · `: `порог {thr}` (always), then in this order only those that ran: `лексич.`, `LLM`, `FTS5`, `rewrite`; then `{N_candidates}→{N_final}`; then `{ms} мс`. Active stage names are `font-semibold text-slate-200`.
  - One `Пропущено:` line per skipped stage, `↷ {stage}: пропущен ({причина})`, `text-slate-400` (D-14). Reasons exact: `некорректный ответ модели`, `таймаут`, `ошибка запроса FTS5`, `пустой или слишком длинный результат` (rewrite).
- Candidates table: one table, wrapper `overflow-auto max-h-80 rounded-lg border border-slate-700`, `<table class="w-full text-left border-collapse">`, sticky header `<thead class="sticky top-0 bg-slate-800 text-slate-300 font-semibold">`, rows `border-t border-slate-800`, cells `px-2 py-1 align-top`, numeric cells `text-right tabular-nums`. Columns, in order:
  1. `#` `было→стало` (e.g. `3→1`, `7→—` for cut rows). `<th>` text `было→стало`; `title` explains: `Место до реранка → место в финальной выдаче`.
  2. `cos` score, 2 decimals, `text-slate-200`.
  3. `lex` shown only if lexical ran, 2 decimals, else column omitted.
  4. `FTS` shown only if hybrid ran: FTS rank number or `—`.
  5. `LLM` shown only if LLM rerank ran: score 2 decimals for the scored top 10, `—` otherwise.
  6. `Запрос` shown only if rewrite ran: `исходный` / `переписан` / `оба`.
  7. `Источник` file name + section (`truncate max-w-[10rem]`, `title` full) + `font-mono text-slate-500` chunk_id; metadata only, no chunk text (D-06).
  8. `Статус` chip.
  Column header texts: `было→стало`, `cos`, `lex`, `FTS`, `LLM`, `Запрос`, `Источник`, `Статус`. Rows keep the order of the candidate list as returned (by original rank); there is no client-side sorting.
- Status chips (`inline-block rounded-full border border-slate-700 px-2 py-0.5`, never color-only: glyph prefix + text):
  | Status | Chip text | Style |
  |--------|-----------|-------|
  | in answer | `✓ в ответе` | `bg-slate-700 text-slate-100 font-semibold`; whole row `text-slate-200` |
  | below threshold | `ниже порога` | `bg-slate-900 text-slate-500`; row `text-slate-500` |
  | outside top-K | `вне top-K` | `bg-slate-900 text-slate-500`; row `text-slate-500` |
  | over budget | `не вошло в бюджет` | `bg-slate-900 text-slate-500`; row `text-slate-500`; `title`: `Не поместилось в долю контекста, отведённую под RAG` |
  | FTS exemption | `≈ прошло по FTS` | `bg-slate-800 text-slate-300`; `title`: `Косинус ниже порога, но фрагмент найден по ключевым словам` |
- Empty candidate list: single muted line `Кандидатов нет: в базе нечего сравнивать` (`text-slate-500`) instead of the table.
- All dynamic text via `textContent`; table built with `createElement`, never `innerHTML`. Query strings can contain user text, so DOMPurify is not a substitute for `textContent` here.
- Streaming: no placeholder before `done` (consistent with 14). If rewrite or LLM rerank adds latency before the first token, the existing typing indicator covers it; no new stage-progress UI.
- Order of elements under an assistant message (top to bottom): content, grey below-threshold line (when verdict `below_threshold`), 14 warning line (on retrieval failure), mode label, "Источники", "Детали поиска".

### States
| State | Behavior |
|-------|----------|
| RAG off | `#rag-search-btn` hidden with `#rag-k-wrap`; popover force-closed |
| RAG on, defaults | Button neutral; popover shows candidate-K 20, threshold calibrated value with `(калибр.)`, all four switches off |
| Any optional stage on | Button gets indigo border + white text |
| Threshold overridden | `сбросить` visible; button state unchanged |
| KB changed | popover threshold display refreshes to the new KB's calibrated value unless overridden |
| Everything cut | grey line + details block with all rows chipped as cut; no sources block |
| Stage failed mid-turn | `↷ … пропущен (причина)` line inside details only; no toast, no yellow |
| PUT failed | control reverts, error toast |
| Old message (v1, no trace) | no details block |

### Accessibility
Popover is a labelled dialog with Escape-to-close and focus return; switches use `role="switch"` and show state by position and label, not color alone; table has `<th scope="col">`; chips carry glyph + text; `<details>` is natively keyboard operable; grey line has `role="status"`.

---

## Copywriting Contract

| Element | Copy |
|---------|------|
| Primary CTA | `Поиск ⚙` (opens the popover; changes save on edit, no submit button) |
| Popover aria-label | `Настройки поиска` |
| Candidate-K label | `Кандидатов` |
| Threshold label | `Порог` · suffix `(калибр.)` · no-calibration `0.00 (нет калибровки)` · reset `сбросить` |
| Switch: lexical | `Лексич. реранк` (`title`: `Переупорядочивает прошедшие порог фрагменты по совпадению слов запроса, включая номера статей`) |
| Switch: LLM | `LLM-реранк` (`title`: `Модель чата оценивает 10 лучших фрагментов. Один дополнительный запрос к модели`) |
| Switch: hybrid | `Гибрид (FTS5)` (`title`: `Добавляет поиск по ключевым словам и объединяет его с векторным. Находки по ключевым словам проходят порог`) |
| Switch: rewrite | `Переписывание запроса` (`title`: `Модель чата переформулирует вопрос; ищем по исходному и переписанному. Один дополнительный запрос к модели`) |
| Details summary | `Детали поиска` |
| Details header labels | `Запрос:` · `Переписан:` · `Этапы:` · `Пропущено:` |
| Table headers | `было→стало` · `cos` · `lex` · `FTS` · `LLM` · `Запрос` · `Источник` · `Статус` |
| Query origin values | `исходный` · `переписан` · `оба` |
| Status chips | `✓ в ответе` · `ниже порога` · `вне top-K` · `не вошло в бюджет` · `≈ прошло по FTS` |
| Grey line (empty state heading equivalent) | `Фрагменты не прошли порог (лучший {best} < {threshold})` |
| Empty state body | The grey line is the whole message; the model's answer already states that nothing relevant was found (D-09). Next step is implicit: rephrase or lower the threshold in "Поиск ⚙". `title` on the grey line: `Снизьте порог или переформулируйте вопрос. Подробности в «Детали поиска»` |
| No candidates | `Кандидатов нет: в базе нечего сравнивать` |
| Skip lines | `↷ LLM-реранк: пропущен (некорректный ответ модели)` · `↷ LLM-реранк: пропущен (таймаут)` · `↷ Переписывание: пропущено (пустой или слишком длинный результат)` · `↷ Гибрид (FTS5): пропущен (ошибка запроса FTS5)` |
| Error: save search settings | `Не удалось сохранить настройки поиска. Проверьте соединение и попробуйте снова.` |
| Destructive confirmation | none in this phase. All controls are reversible settings; no delete or reset-all action is added (`сбросить` only restores the calibrated threshold). |

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
