---
phase: 23
slug: help-command-verification-and-report-day-31
status: draft
shadcn_initialized: false
preset: none
created: 2026-10-13
---

# Phase 23 — UI Design Contract

> Visual and interaction contract for the `/help` command UI: the Settings section, the knowledge base card badge, the `/help` answer label and the temporary service notice. Reuses the shipped UI in `ui/static/index.html` and `ui/static/app.js`; no new visual language. Decisions D-01..D-22 in 23-CONTEXT.md are locked and not re-asked.

---

## Design System

| Property | Value |
|----------|-------|
| Tool | none (vanilla JS + Tailwind CDN; no bundler, no component library; shadcn gate not applicable) |
| Preset | not applicable |
| Component library | none |
| Icon library | none (text glyphs only: `·`, `×`, `…`) |
| Font | Tailwind default sans stack (system UI) |

Source: CLAUDE.md hard constraints; Phase 21 UI-SPEC; existing `#mcp-section`, `buildKbCard`, `buildRagSourcesBlock`, `buildRagQuotesBlock`, `RAG_CHIP_*`.

---

## Spacing Scale

Expressed as the Tailwind utilities already used:

| Token | Value | Usage |
|-------|-------|-------|
| xs | 4px | `space-y-1`, `mb-1`, `py-1` — checkbox rows, label gaps |
| sm | 8px | `gap-2`, `p-2`, `px-2` — chip padding, badge gap, button rows |
| md | 16px | `space-y-4` — gaps between form groups |
| lg | 24px | not used |
| xl | 32px | not used |
| 2xl | 48px | not used |
| 3xl | 64px | not used |

Exceptions: the new Settings section reuses the existing `#mcp-section` shell (`border-t border-slate-700 p-5 space-y-3`) and the 12px (`px-3 py-2`) input padding; the label chip uses the existing `px-2 py-0.5` chip padding. Server checkbox list is capped at `max-h-40` (160px) with `overflow-y-auto`.

---

## Typography

No new sizes or weights.

| Role | Size | Weight | Line Height |
|------|------|--------|-------------|
| Body (field labels, checkboxes, buttons, KB select) | 14px (`text-sm`) | 400 | 1.5 |
| Label / meta (hints, badge, answer label, notice, warning) | 12px (`text-xs`) | 400; chip/badge 600 | 1.5 |
| Heading (section title `h3`) | 18px (`text-lg`) | 600 | 1.2 |
| Display | not used | — | — |

Weights: exactly two — 400 and 600 (`font-semibold`).

---

## Color

Existing slate dark theme; no new colors.

| Role | Value | Usage |
|------|-------|-------|
| Dominant (60%) | slate-900 `#0f172a` | Settings modal surface, page background |
| Secondary (30%) | slate-800 `#1e293b` (+ slate-700 borders `#334155`) | Inputs, chips, notice, KB cards |
| Accent (10%) | indigo-600 `#4f46e5` / indigo-500 `#6366f1` | see list below |
| Destructive | red-400 `#f87171` | Save-failure messages only |

Warning (D-08 under-answer line, validation hints that are not failures): yellow-500 `#eab308` (already used by `#stats-local-indicator`).

Accent reserved for: the focus ring on the new inputs/select/checkboxes, and the `Сохранить` button of the section if one is used. The `Документация проекта` badge, the `Справка по проекту` label and the service notice use the NEUTRAL chip style (`RAG_CHIP_NEUTRAL`: `bg-slate-800 text-slate-300 border-slate-700`), NOT accent. `Сбросить выбор` and `Сбросить` (threshold) use neutral text-button style.

---

## Component Contract

### 1. Settings section «Справка по проекту (/help)»

Placement: a new `<section id="help-section" class="border-t border-slate-700 p-5 space-y-4">` directly after `#mcp-section` and before `#llm-providers-section` (D-12: next to «MCP серверы»). Heading `h3.text-lg.font-semibold`: `Справка по проекту (/help)`. Under it a hint (`text-xs text-slate-500`): `Команда /help <вопрос> отвечает по выбранной базе знаний и инструментам ниже, в любом чате. Настройки поиска чата она не меняет.`

Groups, top to bottom (all inputs styled like existing fields: `w-full rounded-lg bg-slate-800 border border-slate-700 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500`; labels `block text-sm text-slate-400 mb-1`):

1. **Documentation base** — `<select id="help-kb">` listing the user's bases with `status === 'ready'` (a base being added to counts as ready, assumption A2). First option `— не выбрана —` (value empty). Beside it a neutral text button `Сбросить выбор` (`text-slate-400 hover:text-white text-xs`), shown only when a base is selected; it sets the mark to none. Below the select, `text-xs text-slate-500`: `Показываются только готовые базы. Отметка действует для вашей учётной записи.` If the user has no ready bases: select disabled, option `Нет готовых баз знаний`, hint line `Создайте и проиндексируйте базу в окне баз знаний (README.md и docs/).`
2. **Search settings** (mirror of the chat RAG search popover, D-04), `grid grid-cols-2 gap-2` for the numeric fields:
   - `Top-K` (`type=number`, 1..20 mirroring the chat range), `Кандидатов` (candidate count, min = Top-K, max 50), `Порог` (`type=number step=0.01 min=0 max=1`, empty = calibrated; note span `(калибр.)` / `(нет калибровки)` exactly as `rag-threshold-note`, plus `Сбросить` button as `rag-threshold-reset`).
   - Four checkboxes (rounded, `text-sm` labels, `space-y-1`): `Лексический поиск`, `LLM-реранкинг`, `Гибридный поиск`, `Переписывание запроса` (labels copied from the chat popover wording; the implementer MUST reuse the exact label strings of `#rag-search-popover` rather than inventing new ones).
   - No strict toggle, no history-turns field (D-01, D-10: not applicable to `/help`).
3. **Tools in a /help turn** (D-05..D-07): sub-heading `text-sm text-slate-400`: `Инструменты`.
   - `MCP серверы` — one checkbox row per configured MCP server of the user (`id=help-mcp-{id}`, label = server name, `text-sm`; connection status text in `text-xs` slate-500 to the right: `подключён` / `не подключён`, reusing `MCP_STATUS_LABELS`). Container `max-h-40 overflow-y-auto space-y-1`. Default state on first open: only the server named `local-git` is checked. If `local-git` is not configured, hint `text-xs text-yellow-500`: `Сервер local-git не настроен — /help ответит только по документации.` If no servers at all: `text-xs text-slate-500`: `MCP серверы не настроены.`
   - `Встроенные` — three checkboxes: `Память`, `Задачи`, `Планировщик`, all unchecked by default. Hint `text-xs text-slate-500`: `Включённые встроенные инструменты могут изменять данные: память, задачи, расписание. По умолчанию выключены.`
4. **Save behaviour**: every control saves on `change` (same autosave pattern as the chat RAG popover, `saveRagSearchSetting`), no separate form submit. A small `text-xs` status line (`role="status"`, slate-500) shows `Сохранено` for ~2s after a successful save; failures show a `showToast(..., 'error')` with `Не удалось сохранить настройки /help. Проверьте соединение и попробуйте снова.` and the control reverts to the stored value. Section loads its values when the Settings modal opens.
5. Accessibility: each control has a `<label for>`; the section is `aria-labelledby` its heading; checkbox groups are wrapped in `<fieldset>` with a visually present `<legend>` (`Инструменты`, `Поиск`); status line `role="status"`; error via toast.

### 2. Knowledge base card badge (D-13)

- In `buildKbCard`, when `kb.id` equals the marked base id, the header row gets a badge before the status chip: `Документация проекта`, class `RAG_CHIP_NEUTRAL` plus `text-xs` and `shrink-0` (`inline-block rounded-full border px-2 py-0.5 font-semibold bg-slate-800 text-slate-300 border-slate-700`). Name keeps `truncate` and `title`.
- The card gets NO button to set/clear the mark. The badge is not interactive.
- The badge disappears when the mark is cleared or the marked base is deleted (A3); the knowledge base list re-renders after a Settings change (state shared via the same `state` object; opening the KB window always re-reads the mark).

### 3. `/help` answer in the chat (D-15, D-09, D-02)

- **User message**: rendered unchanged, exactly as typed including `/help ` prefix (existing user bubble).
- **Label** above the answer text, inside the assistant bubble, first child: a chip in `RAG_CHIP_NEUTRAL` style, `text-xs`, `mb-1`: `Справка по проекту · {kb.name}` (name `truncate max-w-[16rem]`, full name in `title`). Rendered from the stored assistant-message payload (carrier: `Message.rag_sources`, A5) so it is identical after reload. When the base was deleted afterwards the stored name is still shown.
- **Order below the label**: answer text (existing markdown rendering via Marked + DOMPurify), then ordinary tool-call cards (`buildToolCallCard`, unchanged; MCP calls show `mcp__local-git__…` names), then `Источники (N)` (`buildRagSourcesBlock`, collapsed), then `Цитаты (N)` (`buildRagQuotesBlock`, open) — the existing blocks, unchanged styling, reused as-is. Relative paths shown verbatim (`docs/API_SPEC.md`, per Phase 21 §6).
- When no fragment passed the threshold (D-01): no sources block, no quotes block; label is still shown with the base name. A one-line note `text-xs text-slate-500` under the answer: `В документации ничего не найдено.` (wording A4).
- «Детали поиска» block: shown under a `/help` answer exactly as for a RAG answer when search ran (assumption A6); no change to its style.
- **MCP warning (D-08)**: a `text-xs text-yellow-500` line directly under the answer, `role="status"`, text `сервер {name} недоступен, его инструменты не использовались` (one line per unavailable server, first letter capitalised: `Сервер local-git недоступен, его инструменты не использовались.`). Stored with the message (same carrier as the label) so it survives reload.
- Streaming: while the turn runs the existing typing/stream indicator is used; the label appears as soon as the first frame identifies the turn as `/help` (or, at the latest, on the done frame). No new loader.

### 4. Temporary service notice (D-14)

Used for the two no-model replies: usage text (HELP-04) and "no base" instruction (HELP-05).

- Rendered in the message list as a non-persistent element: container `rounded-lg border border-slate-700 bg-slate-900 text-sm text-slate-300 px-3 py-2 max-w-[75%] self-start` (same width rule as the RAG blocks), `role="status"`, not a message bubble of the assistant (no avatar/role styling, no copy/regenerate/edit/branch actions).
- First line chip `RAG_CHIP_NEUTRAL`: `Справка по проекту` followed by text lines. Footer `text-xs text-slate-500`: `Это сообщение не сохраняется в чате.`
- Not added to `state` message tree, not counted in the chat stats, gone after reload or chat switch. The user's typed `/help` line is also not shown as a stored message after reload (nothing was stored). In the live view the typed line is shown in the user bubble position until reload (consistent with the notice); it is removed together with the notice on reload.
- The input is re-enabled immediately (no model call, no stream). No error toast is used: these are guidance, not failures.

### 5. Message input

- No change to the input control, no `/` autocomplete or hint (deferred by CONTEXT). `/help` is sent through the normal send path; recognition is server-side.

---

## Copywriting Contract

All user-facing strings in Russian (matches the shipped UI). Final wording of server texts is planner discretion but MUST keep the meaning and the `/help <вопрос>` form below.

| Element | Copy |
|---------|------|
| Primary CTA | none (autosave; the command itself is the CTA: `/help <вопрос>`) |
| Settings section title | `Справка по проекту (/help)` |
| Base select placeholder | `— не выбрана —` |
| Clear mark | `Сбросить выбор` |
| Base list empty | `Нет готовых баз знаний` |
| Answer label | `Справка по проекту · {имя базы}` |
| KB card badge | `Документация проекта` |
| Usage text (no question) | `Использование: /help <вопрос> — ответ по документации проекта и состоянию git. Пример: /help как запустить приложение?` |
| Empty state heading (no base marked or not ready) | `База документации не выбрана` |
| Empty state body (instruction) | `Откройте «Настройки» → «Справка по проекту (/help)» и выберите готовую базу знаний с документацией проекта (README.md и docs/). Если база ещё индексируется, дождитесь статуса «готово».` |
| Marked base not ready (variant of body) | `Выбранная база «{имя}» ещё не готова. Дождитесь окончания индексации или выберите другую базу в «Настройки» → «Справка по проекту (/help)».` |
| Nothing found in docs | `В документации ничего не найдено.` |
| MCP unavailable warning | `Сервер {имя} недоступен, его инструменты не использовались.` |
| Not-stored footer on notice | `Это сообщение не сохраняется в чате.` |
| Save error (toast) | `Не удалось сохранить настройки /help. Проверьте соединение и попробуйте снова.` |
| Save success status | `Сохранено` |
| local-git missing hint | `Сервер local-git не настроен — /help ответит только по документации.` |
| Destructive confirmation | No new destructive actions. Clearing the mark is immediate and reversible (re-select the base); no confirmation. Deleting a marked base uses the existing `Точно удалить?` / `Отмена` flow and silently clears the mark (A3). |

HTTP service replies (D-14, D-16) are JSON, not UI; field names are planner discretion, but the body must carry the same usage/instruction text and an explicit "not stored" flag.

---

## Interaction & State Rules

1. `/help` is recognised on the server; the client never special-cases the command text, only renders the frames/payload it receives.
2. A real `/help` exchange is two stored messages and renders from stored data after reload identically to live (label, tool cards, sources, quotes, warning).
3. Service notices are client-only and transient; reloading or switching chats removes them.
4. The mark and `/help` settings are per user and independent of the current chat; changing them never touches the chat RAG panel (`#rag-search-popover`, mode, KB select) and the chat RAG panel never reflects them.
5. Disabled/empty rules: base select is disabled when no ready base exists; candidate count `min` follows Top-K; threshold empty means calibrated value of the marked base's embedding model (note `(калибр.)`), and `(нет калибровки)` when no base is marked.
6. All dynamic text (base names, server names, file paths, answers' warnings) is set via `textContent`; the answer body uses the existing Marked + `DOMPurify.sanitize()` path. No new `innerHTML`.
7. Keyboard/a11y: all new controls focusable with the indigo focus ring; `Escape` behaviour of the Settings modal unchanged; notice is `role="status"` (polite), MCP warning `role="status"`.
8. Reduced motion: no new animations introduced.

---

## Assumptions (CONTEXT left open; chosen for consistency with existing UI)

- A1. Settings controls autosave on change (like the chat RAG popover) rather than a Save button; the existing `Сохранить` of the main settings form does not cover this section.
- A2. A ready base with `phase=adding` appears in the selector and counts as ready.
- A3. Deleting the marked base clears the mark; the badge and the select update accordingly.
- A4. Wording of the usage text, instruction, label, warning and "nothing found" note as in the Copywriting table.
- A5. Label and warning persist in the assistant message's existing `rag_sources` payload; if the planner chooses another carrier, the rendered result must be identical.
- A6. «Детали поиска» is shown under a `/help` answer whenever the search ran, with the existing block unchanged.
- A7. Search-settings labels are copied from the existing chat popover; the section has no strict toggle and no history-turns field.
- A8. The service notice also keeps the typed `/help` line out of the stored history; both disappear on reload.

---

## Registry Safety

| Registry | Blocks Used | Safety Gate |
|----------|-------------|-------------|
| shadcn official | none | not applicable |
| third-party | none | not applicable |

External assets remain the existing CDN libraries (Tailwind, Marked.js, DOMPurify); no new ones.

---

## Checker Sign-Off

- [ ] Dimension 1 Copywriting: PASS
- [ ] Dimension 2 Visuals: PASS
- [ ] Dimension 3 Color: PASS
- [ ] Dimension 4 Typography: PASS
- [ ] Dimension 5 Spacing: PASS
- [ ] Dimension 6 Registry Safety: PASS

**Approval:** pending
