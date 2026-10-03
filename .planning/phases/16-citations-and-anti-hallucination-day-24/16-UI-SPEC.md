---
phase: 16
slug: citations-and-anti-hallucination-day-24
status: approved
shadcn_initialized: false
preset: none
created: 2026-10-03
reviewed_at: 2026-10-03
---

# Phase 16 — UI Design Contract

> Visual and interaction contract for the «Цитаты (N)» block, quote status chips, «цитируется» marks, the amber/grey answer lines, the templated «Не знаю» reply and the strict-mode switch in "Поиск ⚙". Extends 14-UI-SPEC.md and 15-UI-SPEC.md (same Tailwind-CDN dark theme, same tokens); nothing from those phases is re-specified. Decisions source: 16-CONTEXT.md D-01..D-18. No frontend dependencies added. Existing anchors in `ui/static/app.js`: `buildRagMeta`, `buildRagSourcesBlock`, `buildRagSourceRow`, `buildRagThresholdLine`, `renderRagSearchPopover`.

---

## Design System

| Property | Value |
|----------|-------|
| Tool | none (vanilla JS + Tailwind CDN; shadcn not applicable per CLAUDE.md) |
| Preset | not applicable |
| Component library | none (hand-built DOM via `mcpEl`; quotes block mirrors the `.rag-sources` `<details>` card, rows mirror `.rag-source-row`) |
| Icon library | none (Unicode glyphs only: ✓ ≈ ✗ ⚙ ↷ →) |
| Font | Tailwind default system sans; `font-mono` only for chunk_id and `[N]` labels; quote text itself is sans, not mono |

---

## Spacing Scale

Inherited unchanged from 14/15-UI-SPEC.

| Token | Value | Usage |
|-------|-------|-------|
| xs | 4px | `mt-1`, `gap-1`, chip inner gaps |
| sm | 8px | `gap-2`, `space-y-2` between quote rows, `gap-x-2` in row heads |
| md | 16px | block gaps |
| lg | 24px | reserved |
| xl | 32px | reserved |
| 2xl | 48px | reserved |
| 3xl | 64px | reserved |

Exceptions (inherited, exhaustive for this phase):
- `px-3 py-2` / `p-3` (12px/8px): quotes block and quote rows, same as the sources block and source rows.
- `px-2 py-0.5` (8px/2px) on chips, same as 15 status chips.
- `max-w-[75%]` on the quotes block, same as sources and details blocks.
- Strict switch row reuses the popover's `h-5 w-9` track and `h-4 w-4` thumb from 15.

---

## Typography

| Role | Size | Weight | Line Height |
|------|------|--------|-------------|
| Body (quote text inside a row) | 14px (`text-sm`) | 400 | 1.5 |
| Label / meta (summary, chips, `[N]` + file → section, notes, amber/grey lines, popover switch text) | 12px (`text-xs`) | 400 (summary and chips 600) | 1.5 |
| Heading | not added | 600 | 1.2 |
| Display | not used | - | - |

Weights: exactly 2 (400, 600). Sizes in use: exactly 2 (12, 14). The templated «Не знаю» reply is a normal assistant message body (14px/400); it gets no special size.

---

## Color

| Role | Value | Usage |
|------|-------|-------|
| Dominant (60%) | `bg-slate-950` | app background |
| Secondary (30%) | `bg-slate-900` quotes block; `bg-slate-800` quote rows; borders `slate-700` | |
| Accent (10%) | `indigo-600` (#4f46e5) | see list below |
| Destructive | `red-400` on `bg-red-900/30`, border `red-700` | «не подтверждена» chips only |
| Success (semantic) | `emerald-400` on `bg-emerald-900/30`, border `emerald-700` | «подтверждена» chip only |
| Warning (semantic) | `yellow-400` on `bg-yellow-900/30`, border `yellow-700` (existing 14 warning tokens) | amber line «ответ не подтверждён фрагментами» only |

Accent reserved for: checked state of the new strict-mode switch (`bg-indigo-600`), `focus:ring-2 focus:ring-indigo-500` on that switch, `focus-visible:ring-2 ring-indigo-500` on the «Цитаты» `<summary>`, and the existing `показать полностью` text-button (`text-indigo-400`). The «Поиск ⚙» button indigo border state is NOT triggered by the strict switch (it is on by default; the indicator keeps meaning "optional extra stage on").

Neutral chips (never color-only; every chip has glyph + text):
- «почти дословно»: `bg-slate-700 text-slate-100 border-slate-600` (≈ glyph).
- «подобрана автоматически», «источник исправлен», «цитируется»: `bg-slate-800 text-slate-300 border-slate-700`.
Grey «не знаю» styling and the invalid-refs note: `text-slate-500`, no border.

---

## Interaction & Layout Contract

### Focal points
- Message area: the answer text remains the focal point. The «Цитаты» block is the second read (expanded, slate card); «Источники» and «Детали поиска» stay collapsed and subordinate.
- Header: unchanged. Strict mode is a popover control, not a header control.

### Element order under an assistant message (top to bottom)
1. Content (clean answer; «Цитаты:» tail already cut after `done`).
2. Grey line (verdict `below_threshold`, from 15) OR grey «не знаю» styling (D-13, `model_idk`).
3. Amber line `ответ не подтверждён фрагментами` (D-14) when no valid `[N]` and no verified model quote.
4. Grey invalid-refs note (D-07) when count > 0.
5. 14 warning line (retrieval failure), mode label.
6. «Цитаты (N)» block (expanded).
7. «Источники (N)» (collapsed).
8. «Детали поиска» (collapsed).
The block order matches D-08: answer, quotes, sources all legible without a click.

### «Цитаты (N)» block (D-02, D-04, D-05, D-06, D-07, D-08)
- Rendered only from `done.rag` / stored payload v3, never from message text. Absent when strict mode was off for the turn, when the reply is a gated or `model_idk` «не знаю», and for pre-Phase-16 messages. N counts model quotes plus auto quotes shown.
- Container: `<details open class="rag-quotes rounded-lg border border-slate-700 bg-slate-900 text-xs text-slate-300 px-3 py-2 mt-2 max-w-[75%]">`. `<summary class="cursor-pointer select-none font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">Цитаты (N)</summary>`. Body `mt-2 space-y-2`.
- Quote row: `bg-slate-800 border border-slate-700 rounded-lg p-3 space-y-1`. Layout:
  - Head line `text-xs flex flex-wrap items-center gap-x-2 gap-y-1`, in order: status chip, optional secondary chip («источник исправлен» or «подобрана автоматически»), `[N]` (`font-mono text-slate-500`), file (`truncate`, `title` full), section (`truncate`). `[N]`, file and section come from chunk metadata of the attached source, never from model text. For «нет такого источника» the head has the chip and no `[N]`/file/section.
  - Quote text `<p class="text-sm text-slate-200 line-clamp-4 whitespace-pre-wrap">«…»</p>` (guillemets added by the renderer, text via `textContent`). Verified and almost-verbatim quotes in `text-slate-200`; unverified quotes in `text-slate-400` with `line-through` NOT used (the text stays readable).
  - Quotes longer than ~300 chars are clamped by `line-clamp-4` and get the existing `показать полностью` / `свернуть` button (`text-xs text-indigo-400 hover:text-indigo-300`, `aria-expanded`), shown only when the text overflows.
- Order of rows: as the model wrote them; auto quotes (D-04) follow model quotes. No client sorting.
- Quote status chips (`inline-block rounded-full border px-2 py-0.5 font-semibold`):
  | State | Chip text | Style |
  |-------|-----------|-------|
  | exact | `✓ подтверждена` | emerald tokens; `title`: `Цитата дословно найдена в указанном фрагменте` |
  | fuzzy | `≈ почти дословно` | neutral slate-700; `title`: `Цитата совпадает с фрагментом с мелкими отличиями (сходство не ниже 0.9)` |
  | unverified | `✗ не подтверждена` | red tokens; `title`: `Цитата не найдена ни в одном из фрагментов, отправленных модели` |
  | unverified, bad ref | `✗ не подтверждена (нет такого источника)` | red tokens; same `title` |
- Secondary chips (neutral slate-800): `источник исправлен` (`title`: `Цитата не нашлась в указанном фрагменте, но найдена в другом; источник заменён`) and `подобрана автоматически` (`title`: `Модель не дала подтверждённых цитат; предложение выбрано кодом из фрагмента по совпадению слов`).
- Unverified quotes are always displayed, never hidden (D-06).

### «Источники (N)» changes (D-09)
- Same block as 14. Rows for fragments referenced by a valid `[N]` in the answer or by a quote are listed first and get a neutral chip `цитируется` right after `#rank`; remaining fragments follow in rank order. Row content and lazy snippet loading unchanged.

### Amber and grey lines
- Amber (D-14): `mt-2 rounded-lg border border-yellow-700 bg-yellow-900/30 px-3 py-2 text-xs text-yellow-400`, `role="status"`, text `⚠ ответ не подтверждён фрагментами` (same tokens as the 14 warning line). `title`: `В ответе нет ссылки [N] на фрагмент и ни одной подтверждённой цитаты. Проверьте ответ по источникам`.
- Grey «не знаю» (D-10, D-13): the assistant bubble content is the reply; below it, `mt-2 text-xs text-slate-500` `role="status"` line. Gated (`below_threshold`) keeps the 15 line `Фрагменты не прошли порог (лучший {best} < {threshold})`. `model_idk` shows `Модель ответила «не знаю»: фрагменты не содержат ответа` in the same grey style. No amber, no red, no toast: abstaining is a correct outcome.
- Invalid-refs note (D-07): `mt-1 text-xs text-slate-500`, text `Несуществующих ссылок на источники в ответе: {n}`; shown only when n > 0.

### Templated «Не знаю» reply (D-10, D-11)
- Rendered as an ordinary assistant bubble (same markup, same Markdown pipeline with DOMPurify). Content is code-built: fixed sentence, then the clarifying question. Delivery over WebSocket (single frame vs token frames) is the planner's choice; the UI must render it identically after `done` and after reload.
- «Источники» absent (no chunks reached a model); «Цитаты» absent; «Детали поиска» present (trace kept).
- No candidates at all: clarifying question falls back to the rephrase-only sentence (see Copywriting).

### Streaming (D-02)
- While tokens arrive the raw «Цитаты:» tail is visible inside the bubble; on `done` the bubble content is replaced with the clean answer and the quotes block, amber/grey lines and notes are appended. No hide-filter, no placeholder, no layout animation. The block insertion must not steal focus or scroll the viewport away from the user's position unless they were already at the bottom (existing autoscroll rule).

### Strict-mode switch in "Поиск ⚙" popover (D-15)
- New fifth switch row, placed FIRST in the switch list (above «Лексич. реранк»), separated from the four stage switches by `border-b border-slate-800 pb-2` since it governs the answer, not the search. Same row markup as 15 switches (`<label>` + `role="switch"` + `sr-only peer` input + track/thumb). Label `Строгий режим` with muted second line `цитаты + «не знаю»` (`text-slate-500`). On by default for RAG chats.
- Commits immediately via PUT `/api/v1/chats/{id}/rag`; failure reverts the switch and `showToast(..., 'error')`. Applies from the next turn; not disabled while streaming. Popover width stays `w-72`; label wraps if needed.
- Off state: answers behave as Phase 15 (no «Цитаты», no gate); old messages keep what they were stored with.

### States
| State | Behavior |
|-------|----------|
| Strict on, verified quotes | «Цитаты (N)» expanded, emerald/neutral chips |
| Strict on, model quotes failed, auto quotes used | quotes shown with `подобрана автоматически` chip; no amber line (auto quotes do not count for D-14) |
| Strict on, no valid `[N]` and no verified model quote | amber line shown (auto quotes may still appear) |
| Quote re-attached | `источник исправлен` chip, source row marked `цитируется` |
| Gated (`below_threshold`) | templated reply + grey threshold line + details; no sources/quotes |
| `model_idk` | grey «не знаю» line; no quotes, no auto quotes |
| Strict off | Phase 15 behavior; no quote UI |
| KB deleted | quote text and file/section still shown (stored in payload); only the lazy «показать полностью» of source snippets degrades per 14 |
| Old message (v1/v2 payload) | no quotes block, no amber line, no notes |

### Accessibility
`<details>` natively keyboard operable; chips carry glyph + text (state never color-only); amber/grey lines `role="status"`; strict switch `role="switch"` with `aria-checked` kept in sync and visible label; red/emerald chips meet contrast on their dark tinted backgrounds (400-level text on 900/30). All dynamic text (quotes, file, section, notes, clarifying question) goes through `textContent`; the assistant reply continues through Marked + `DOMPurify.sanitize()`. Quote strings are model output and must never reach `innerHTML`.

---

## Copywriting Contract

| Element | Copy |
|---------|------|
| Primary CTA | none new. The phase adds no submit action; the only control is the switch `Строгий режим` (`title`: `Ответ с цитатами из фрагментов; если подходящих фрагментов нет, ассистент отвечает «не знаю» без обращения к модели`) |
| Switch sub-label | `цитаты + «не знаю»` |
| Quotes summary | `Цитаты (N)` |
| Quote chips | `✓ подтверждена` · `≈ почти дословно` · `✗ не подтверждена` · `✗ не подтверждена (нет такого источника)` |
| Secondary chips | `источник исправлен` · `подобрана автоматически` · `цитируется` |
| Expand / collapse | `показать полностью` · `свернуть` |
| Empty state heading (no quotes, strict on, answer given) | none rendered; the amber line below carries the message |
| Empty state body | `⚠ ответ не подтверждён фрагментами` (amber line); next step is implicit: check the answer against «Источники» |
| Templated «не знаю» (gate, fixed sentence) | `Не знаю: в базе знаний нет достаточно подходящих фрагментов для ответа на этот вопрос.` followed by the clarifying question |
| Clarifying question (with candidates) | `Ближайшие темы в базе: {файл} → {раздел}; {файл} → {раздел}. Уточните, к какой из них относится вопрос, или переформулируйте его.` (2-3 distinct nearest sections from trace candidates) |
| Clarifying question (no candidates) | `Уточните вопрос или переформулируйте его: в базе знаний не нашлось ничего близкого.` |
| `model_idk` grey line | `Модель ответила «не знаю»: фрагменты не содержат ответа` |
| Grey threshold line | `Фрагменты не прошли порог (лучший {best} < {threshold})` (from 15, unchanged) |
| Invalid refs note | `Несуществующих ссылок на источники в ответе: {n}` |
| Error state: save strict mode | `Не удалось сохранить настройки поиска. Проверьте соединение и попробуйте снова.` (reuses 15 string; switch reverts) |
| Destructive confirmation | none in this phase. The switch is a reversible setting; no delete or reset action is added. |

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
