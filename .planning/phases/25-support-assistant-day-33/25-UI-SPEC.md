---
phase: 25
slug: support-assistant-day-33
status: approved
reviewed_at: 2026-10-17
shadcn_initialized: false
preset: none
created: 2026-10-17
---

# Phase 25 - UI Design Contract

> Visual and interaction contract. Proportionate: the UI surface is small. It is a clone of the existing `/help` UI (Phase 23) with different labels and `support-` element ids. No new tokens, components or libraries are introduced. Everything below reuses what `ui/static/index.html` and `ui/static/app.js` already do.

Sources: 25-CONTEXT.md (D-01, D-03, D-07, D-10), existing `#help-section`, `buildHelpLabel`, `buildHelpNotes`, `buildHelpNotice`.

---

## Design System

| Property | Value |
|----------|-------|
| Tool | none (vanilla JS, Tailwind CDN, no shadcn) |
| Preset | not applicable |
| Component library | none |
| Icon library | none (text and chips only) |
| Font | Tailwind default sans stack (no custom font) |

---

## UI Surface (what this phase adds)

1. **Settings section «Поддержка (/support)»**: placed directly after `#help-section` and before `#llm-providers-section`. Same markup as `#help-section`, ids prefixed `support-` (`support-section`, `support-section-title`, `support-kb-info`, `support-top-k`, `support-candidate-k`, `support-threshold`, `support-threshold-note`, `support-threshold-reset`, `support-stage-lexical|llm|hybrid|rewrite`, `support-mcp-list`, `support-mcp-hint`, `support-tools-memory|tasks|scheduler`, `support-save-status`). Classes copied verbatim from the help section.
   - Differences from `/help`: NO knowledge-base select and no «Сбросить выбор» button. Instead a read-only line `#support-kb-info` (`text-xs text-slate-500`) stating that the base is the one marked for /help (D-04): «База документации: та же, что отмечена для /help (<имя базы> / не выбрана). Выбрать её можно в разделе «Справка по проекту (/help)».»
   - Defaults shown: only the server named `support` checked in the MCP list (if configured); built-in tools unchecked. If no server named `support` exists, `#support-mcp-hint` says «Сервер поддержки не добавлен. Добавьте его в разделе «MCP серверы» (имя support).» in `text-yellow-500`.
   - Saving behaves like `/help`: autosave on change, status text in `#support-save-status` (`text-xs text-slate-500`, `role="status"`).
2. **Label above the answer** (like `buildHelpLabel`): neutral chip `RAG_CHIP_NEUTRAL text-xs inline-block truncate max-w-[16rem]` with text `Поддержка · T-1042` (ticket) or `Поддержка · @login` (user) or `Поддержка` (no identifier). `title` attribute carries the full text.
3. **Direct lookup card** (D-07): rendered by the existing tool-call card component, unchanged, showing server `support`, tool `get_ticket` / `get_user`, arguments and result. No new card variant.
4. **Notes under the answer** (like `buildHelpNotes`): warnings in `text-xs text-yellow-500 max-w-[75%] self-start`, `role="status"` (D-10); «В документации ничего не найдено.» in `text-xs text-slate-500` when no sources. Sources and verified quotes reuse the existing RAG sources renderer.
5. **Service notice** (like `buildHelpNotice`): box `help-notice rounded-lg border border-slate-700 bg-slate-900 text-sm text-slate-300 px-3 py-2 max-w-[75%] self-start`, chip text `Поддержка`, footer «Это сообщение не сохраняется в чате.» Implement by parameterising the existing builders with a command title, not by copying them.

No new pages, modals, panels, or chat input widgets. No autocomplete for `#T-` or `@login`.

---

## Spacing Scale

Reused from the existing UI (Tailwind utilities); no new values.

| Token | Value | Usage |
|-------|-------|-------|
| xs | 4px | `mb-1`, `mt-1` label/hint gaps |
| sm | 8px | `gap-2`, `px-3 py-2` inputs and checkbox rows |
| md | 16px | `space-y-4` between section blocks |
| lg | 20px | `p-5` section padding (existing; matches `#help-section`) |

Exceptions: 20px (`p-5`) and 12px (`px-3`) are inherited from the existing help section for visual parity; no new exceptions.

---

## Typography

Inherited from the existing sections.

| Role | Size | Weight | Line Height |
|------|------|--------|-------------|
| Body (inputs, checkbox labels, notice text) | 14px (`text-sm`) | 400 | 1.5 |
| Label / hint / warning / chip (`text-xs`) | 12px | 400 | 1.5 |
| Heading (section title, `text-lg`) | 18px | 600 (`font-semibold`) | 1.4 |

Display: not used. Weights: 400 and 600 only.

---

## Color

Existing dark slate theme; nothing new.

| Role | Value | Usage |
|------|-------|-------|
| Dominant (60%) | slate-900 / page background | Page, notice box (`bg-slate-900`) |
| Secondary (30%) | slate-800 (`bg-slate-800`), border slate-700 | Inputs, section dividers (`border-slate-700`) |
| Accent (10%) | indigo-500 | Focus ring on settings inputs only (`focus:ring-indigo-500`) |
| Warning | yellow-500 | D-10 warnings and the «server not added» hint |
| Destructive | none in this phase | No destructive actions |

Accent reserved for: input focus rings in the support settings section. The label chip stays neutral (`RAG_CHIP_NEUTRAL`), not accent, so `/help` and `/support` look consistent.

Muted text: slate-500 for hints, slate-400 for field legends, slate-300 for notice body.

---

## Copywriting Contract

All user-facing text in Russian.

| Element | Copy |
|---------|------|
| Section title | Поддержка (/support) |
| Section description | Команда /support [#T-1042 или @login] &lt;вопрос&gt; отвечает по документации и данным тикета или пользователя из сервера поддержки. Вы выступаете как сотрудник поддержки и можете спрашивать о любом тикете. |
| Primary CTA | none in the section (autosave); chat usage: `/support #T-1001 Почему не работает авторизация?` |
| Usage text (`/support` with empty question) | «Использование: /support [#T-1042 или @login] <вопрос>. Пример: /support #T-1001 Почему не работает авторизация?» |
| Empty state: no base marked | «База документации не выбрана. Отметьте её в настройках, в разделе «Справка по проекту (/help)», и повторите команду.» |
| Empty state: base not ready | «База документации ещё не готова. Дождитесь окончания индексации в разделе «Справка по проекту (/help)» и повторите команду.» |
| No fragments found (note under answer) | В документации ничего не найдено. |
| Warning: ticket not found | «Тикет T-9999 не найден, ответ дан без данных тикета.» |
| Warning: user not found | «Пользователь @login не найден, ответ дан без данных пользователя.» |
| Warning: server missing / not connected | «Сервер поддержки недоступен (<причина>), ответ дан без данных тикета. Проверьте сервер support в разделе «MCP серверы».» |
| Error state (model/LLM failure) | Reuse the existing chat error banner copy; no new text. |
| Destructive confirmation | not applicable (no destructive actions; `/support` only reads) |

Notices (empty/usage/not-ready) are not stored and make no model call, same as `/help`.

---

## Interaction and Accessibility

- Warnings and notices use `role="status"`; settings status uses `role="status"`; every input has a `<label for>`; every checkbox is reachable via its label text.
- Keyboard: standard tab order, visible focus ring (`focus:ring-2 focus:ring-indigo-500`) on all fields.
- The label chip truncates at `max-w-[16rem]` with full text in `title`.
- Loading: the existing streaming/loading bubble; the direct-lookup card appears before the first token, as an ordinary tool-call card.
- Reload: label, sources, quotes and warnings are restored from the stored message payload (as `/help`), so reload shows the same view.
- A message that is not `/support` renders exactly as today; `/help` UI is unchanged (existing `help-*` ids and strings stay).

---

## Registry Safety

| Registry | Blocks Used | Safety Gate |
|----------|-------------|-------------|
| shadcn official | none | not applicable (shadcn not used) |
| third-party | none | not applicable |

CDN libraries stay as-is (Tailwind, Marked.js, DOMPurify 3.1.6). All text from the server (ticket subject, error text, warnings, label) is inserted via `textContent` (`mcpEl`) or passed through `DOMPurify.sanitize()`; never raw innerHTML.

---

## Checker Sign-Off

- [x] Dimension 1 Copywriting: PASS
- [x] Dimension 2 Visuals: FLAG (non-blocking; declare answer-area focal point)
- [x] Dimension 3 Color: PASS
- [x] Dimension 4 Typography: PASS
- [x] Dimension 5 Spacing: FLAG (non-blocking; p-5/px-3 kept for parity with #help-section)
- [x] Dimension 6 Registry Safety: PASS

**Approval:** approved 2026-10-17
