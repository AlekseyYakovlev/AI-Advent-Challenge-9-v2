---
phase: 27
slug: ai-release-pipeline-day-35
status: approved
reviewed_at: 2026-10-17
shadcn_initialized: false
preset: none
created: 2026-10-17
---

# Phase 27 - UI Design Contract

> Visual and interaction contract. The UI surface is one new component: a **release plan card** rendered in the chat, with editable version/title, a confirm button, and a per-step result list. MCP calls keep using the existing tool-call cards. No new page, modal, Settings section or token. Everything reuses the dark slate theme in `ui/static/app.js` (vanilla JS, Tailwind CDN, no bundler).

Sources: 27-CONTEXT.md (D-02, D-03, D-05, D-07, D-09, D-11, D-13, D-14, D-19), `buildHelpNotice` (app.js ~L256, the ephemeral command-notice pattern), `buildToolCallCard` (app.js ~L1477), 26-UI-SPEC.md (visual language, construction rule).

---

## Design System

| Property | Value |
|----------|-------|
| Tool | none (vanilla JS, Tailwind CDN, no shadcn) |
| Preset | not applicable |
| Component library | none |
| Icon library | none (emoji prefix only where the tool card already uses it; status glyphs are text characters) |
| Font | Tailwind default sans; `font-mono` for version, tag, range, SHA and changelog text only |

---

## UI Surface (what this phase adds)

1. **Release plan card** (`buildReleasePlanCard(plan)`), shown in the chat after `/release`. Same container language as `buildHelpNotice`: `rounded-lg border border-slate-700 bg-slate-900 text-sm text-slate-300 px-3 py-2 self-start`, `role="region"`, `aria-label="План релиза"`. Width `max-w-[75%]` like all cards. Tool-call cards for the reads (`local-git`, GitHub) appear above it, unchanged.
2. **Plan card content, top to bottom:**
   - Header row: chip «План релиза» (reuse `RAG_CHIP_NEUTRAL + ' text-xs'`) and, right-aligned, status text (see States).
   - Facts block (`text-xs`, label `text-slate-500`, value `text-slate-200 font-mono`): Диапазон `v4.0..HEAD`, Ветка `Day35`, Коммитов в релизе `N`, Отфильтровано `M` (shown always, D-07).
   - Warning lines (`text-xs text-yellow-500`, `role="status"`): branch is not `main` (D-09); candidate excluded because the tag exists (D-13); GitHub write access missing (step will be marked non-working).
   - Version and title form: two inputs. «Версия» (`font-mono`, prefilled with the AI's pick) with a radio-style row of the code-computed candidates as small chips (`v5.0`, `v4.1`) that fill the input when clicked; «Название» (prefilled with the AI title). Inline validation message under the version input (`text-xs text-red-400`) per D-13.
   - Rationale: 2-4 sentences from the AI, plain text, `text-slate-300`, label «Обоснование версии».
   - Changelog draft: collapsed `<details>` «Черновик CHANGELOG (Добавлено / Исправлено / Изменено)»; body rendered through the existing markdown path `DOMPurify.sanitize(marked.parse(text))` inside a container capped at `max-h-96 overflow-y-auto`.
   - Write steps list (ordered, `<ol>`): 1 «Коммит CHANGELOG.md», 2 «Аннотированный тег vX.Y», 3 «Push ветки и тега», 4 «Черновик GitHub Release». Each row has a status glyph (see Step Status).
   - Action row: primary button «Подтвердить релиз», secondary hint `text-xs text-slate-500`: «Или введите /release confirm».
   **Visual hierarchy and scan order.** Focal point: the facts block plus the version input at the top of the card (what is being released and as which version). The accent «Подтвердить релиз» button is the action anchor at the bottom, the only filled accent element in the card. Intended scan order: header status, facts, warnings, version/title form, rationale, changelog draft (collapsed), write steps, action row.
3. **Version/title override.** Edits are local to the card until confirm; confirm sends the current input values. Changing the version re-validates immediately on `input` (format `^v\d+\.\d+$`, greater than last tag, not taken) using the facts delivered with the plan; the server re-validates and is authoritative. While invalid, the confirm button is disabled and the message is shown (`aria-describedby` links input and message).
4. **Result state (same card, updated in place).** After confirm the form fields become read-only text, the confirm button is replaced by the per-step report. The step list rows are filled with outcomes (Step Status). Below the list: link to the draft Release (`<a target="_blank" rel="noopener noreferrer">`, `text-indigo-400 underline`) when step 4 succeeded; the tag name and commit SHA (short, `font-mono`).
5. **Partial failure (D-05).** If steps 1-3 succeeded and step 4 failed: card shows a red-bordered failure block with the English tool reason as returned (`text-red-300`, `font-mono`, `whitespace-pre-wrap break-words`), the ready release text in a collapsed `<details>` «Текст релиза для ручной публикации» with a «Скопировать текст релиза» button, and the manual instruction line. The confirm button returns with label «Повторить оставшиеся шаги»; the already-done rows stay marked done and are skipped.
6. **Tool-call cards for writes.** Each write MCP call renders as an ordinary tool-call card via `appendToolCallCard` (D-03), in call order, between the plan card and the final result update. No change to the card.
7. **`/release confirm` typed in chat** produces the same state transitions on the latest plan card as the button (card updates in place). If there is no pending plan: ephemeral notice «Нет плана релиза. Сначала введите /release.» (help-notice style).
8. **Persistence.** The plan card is a command-turn notice like `/help`: it is rendered live from the WS event; it is not stored as a chat message. A small footnote `text-xs text-slate-500` «План хранится до подтверждения или перезапуска агента.» replaces the help notice's «Это сообщение не сохраняется в чате.» Reloading the chat does not restore the card (the user re-runs `/release`). The tool-call cards reload through the existing history path as today.
9. **Security (construction rule).** Commit subjects, the AI changelog, title and error reasons are untrusted. Build everything with `createElement` + `textContent`; the only HTML path is the changelog markdown, which must go through `DOMPurify.sanitize()`. Input values are read via `.value`, never interpolated into HTML. Secrets are never displayed.

---

## States

| State | Visual |
|-------|--------|
| Planning (after `/release`, before plan arrives) | Existing typing indicator / streaming bubble; tool-call cards appear as reads complete |
| Plan ready | Header status «Ожидает подтверждения» (`text-yellow-400`), button enabled |
| Blocked (dirty tree, HEAD changed, version invalid) | Header status «Заблокировано» (`text-red-400`), reason line (English server reason plus Russian hint), button disabled. HEAD changed adds a «Пересоздать план» secondary button that sends `/release` again |
| Confirming | Button disabled, label «Выполняется…», rows show in-progress glyph as steps run |
| Done | Header «Готово» (`text-green-400`), all rows done, draft Release link |
| Partial | Header «Частично выполнено» (`text-yellow-400`), failure block, retry button (UI Surface 5) |
| Failed at step 1 or 2 | Header «Ошибка» (`text-red-400`), failure block, retry button |
| Stale plan | Older plan cards stay visible but their buttons and inputs are disabled with label «План устарел» when a newer plan exists |

## Step Status

| Glyph (text) | Meaning | Classes |
|--------------|---------|---------|
| `○` | pending | `text-slate-500` |
| `…` | running | `text-slate-300` |
| `✓` | done | `text-green-400` |
| `↷` | skipped (already done earlier) | `text-slate-400` |
| `✗` | failed | `text-red-400` |
| `⊘` | non-working (no key or write access) | `text-yellow-400` |

Glyph is always followed by a Russian status word («выполнено», «пропущено», «ошибка», «недоступно»), so colour is never the only signal.

---

## Spacing Scale

Reused Tailwind utilities; no new values.

| Token | Value | Usage |
|-------|-------|-------|
| xs | 4px | `mt-1`, `gap-1` between facts and rows |
| sm | 8px | `px-2` on inputs/chips, `mt-2` between blocks, `gap-2` |
| md | 16px | `space-y-4` between major card sections (plan card sections), `px-4` on buttons |
| inherited | 12px | card `px-3 py-2` (kept for parity with help notice and tool-call card) |

Exceptions: 12px (`px-3`) inherited from existing cards; `max-h-96` (384px) is a layout cap, not a spacing token. Button and input touch height at least 32px via `py-2` (8px) or an explicit `min-h-8` (32px); `py-1.5` is not used. The only new spacing utilities in play are `space-y-4` (16px) and `gap-2` (8px); everything else is already used in the existing cards.

---

## Typography

| Role | Size | Weight | Line Height |
|------|------|--------|-------------|
| Body (card text, rationale, step rows) | 14px (`text-sm`) | 400 | 1.5 |
| Label / facts / hints / footnote | 12px (`text-xs`) | 400 | 1.5 |
| Header chip, button label, header status | 12px / 14px | 600 (`font-semibold`) | 1.5 |
| Monospace values (version, tag, range, SHA) | 14px, `font-mono` | 400 | 1.5 |

Sizes: 12px and 14px only. Weights: 400 and 600 only.

---

## Color

Existing dark slate theme; semantic colours come from existing usage (`text-yellow-500` warnings, `text-red-400` errors).

| Role | Value | Usage |
|------|-------|-------|
| Dominant (60%) | slate-900 / slate-950 | Page, plan card background (`bg-slate-900`), inputs (`bg-slate-950`) |
| Secondary (30%) | slate-800, border slate-700 | Card border, candidate chips, secondary buttons, changelog container |
| Accent (10%) | indigo-600 / indigo-500 | See reserved list |
| Destructive / error | red-400 / red-300 | Validation errors, failed steps, failure block border `border-red-800` |
| Success | green-400 | Done glyph and «Готово» only |
| Warning | yellow-400 / yellow-500 | Warnings, «Ожидает подтверждения», non-working steps |

Accent reserved for: the primary «Подтвердить релиз» / «Повторить оставшиеся шаги» button (`bg-indigo-600 hover:bg-indigo-500 text-white`), the keyboard focus ring (`focus:outline-none focus:ring-2 focus:ring-indigo-500`) on inputs, chips and buttons, the selected candidate chip border, and the draft Release link text (`text-indigo-400`).

The confirm action is a write to git and GitHub but is not data-destructive (no rollback needed, draft only), so it uses the accent colour, not red.

---

## Copywriting Contract

All user-facing UI text in Russian. Server error reasons stay in English (Phase 22 D-15) and are shown verbatim with a Russian hint line where useful.

| Element | Copy |
|---------|------|
| Primary CTA | Подтвердить релиз |
| Retry CTA (partial/failed) | Повторить оставшиеся шаги |
| Secondary CTA (HEAD changed) | Пересоздать план |
| Copy button | Скопировать текст релиза |
| Command hint | Или введите /release confirm |
| Empty state heading | Нет плана релиза |
| Empty state body | Нет плана релиза. Сначала введите /release. (ephemeral notice for `/release confirm` without plan) |
| Nothing to release | В диапазоне v4.0..HEAD нет значимых изменений (отфильтровано M коммитов). Укажите другой диапазон: /release v3.0..HEAD |
| Error: version taken | Версия vX.Y уже существует как тег. Выберите другую. |
| Error: version not greater | Версия должна быть больше последнего тега (v4.0). |
| Error: version format | Формат версии: vX.Y, например v4.1. |
| Error: dirty tree | Рабочее дерево не чистое. Закоммитьте или отмените изменения и повторите /release. |
| Error: HEAD changed | HEAD изменился после построения плана. Пересоздайте план. |
| Error: write access missing | Шаг недоступен: нет доступа на запись. Тег отправлен, Release не создан. Используйте текст ниже для ручной публикации. |
| Error: LLM/provider failure | Не удалось получить черновик от модели. Повторите /release или смените модель. |
| Warning: not main | Релиз готовится из ветки Day35, а не main. |
| Destructive confirmation | not applicable as a modal. The confirm button itself is the explicit confirmation (D-02): nothing is written before the click or `/release confirm`; the plan lists every write step beforehand. Stale or blocked plans cannot be confirmed. |
| Footnote | План хранится до подтверждения или перезапуска агента. |

Documentation copy (`docs/USER_GUIDE.md`, `Day35_report.md`) is a docs task, not part of this contract.

---

## Interaction and Accessibility

- Card, inputs, chips and buttons are native elements (`<details>`, `<input>`, `<button>`, `<ol>`); keyboard order: version input, candidate chips, title input, changelog toggle, confirm button.
- Every input has a visible `<label>` (`for`/`id`); validation message is linked with `aria-describedby`; invalid input gets `aria-invalid="true"` and `border-red-500`.
- State changes (status header, step rows) are announced: header status element has `role="status"` `aria-live="polite"`.
- Confirm button double-click protection: disabled immediately on click until the server responds; the server also ignores a duplicate confirm while one is running.
- New card inserts before the streaming bubble via the same logic as `appendToolCallCard`; the messages container scrolls to the bottom on insert and on result update; no layout shift of the chat column.
- `/help`, `/support` and other command UIs are unchanged.

---

## Registry Safety

| Registry | Blocks Used | Safety Gate |
|----------|-------------|-------------|
| shadcn official | none | not applicable (shadcn not used) |
| third-party | none | not applicable |

CDN libraries stay as-is (Tailwind, Marked.js, DOMPurify 3.1.6). No new library.

---

## Checker Sign-Off

- [x] Dimension 1 Copywriting: PASS
- [x] Dimension 2 Visuals: PASS
- [x] Dimension 3 Color: PASS
- [x] Dimension 4 Typography: PASS
- [x] Dimension 5 Spacing: PASS
- [x] Dimension 6 Registry Safety: PASS

**Approval:** pending
