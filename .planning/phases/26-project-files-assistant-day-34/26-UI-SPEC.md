---
phase: 26
slug: project-files-assistant-day-34
status: approved
reviewed_at: 2026-10-17
shadcn_initialized: false
preset: none
created: 2026-10-17
---

# Phase 26 - UI Design Contract

> Visual and interaction contract. The UI surface is one change: a coloured unified-diff view inside the existing tool-call card (26-CONTEXT D-11). There is no new command, Settings section, page, modal, button or token. Everything reuses `buildToolCallCard` / `appendToolCallCard` in `ui/static/app.js` and the dark slate theme.

Sources: 26-CONTEXT.md (D-01, D-09, D-11, Claude's Discretion on diff detection), existing `buildToolCallCard` (app.js ~L1477), 25-UI-SPEC.md (visual language).

---

## Design System

| Property | Value |
|----------|-------|
| Tool | none (vanilla JS, Tailwind CDN, no shadcn) |
| Preset | not applicable |
| Component library | none |
| Icon library | none (the existing card uses the emoji prefix in the summary; keep it) |
| Font | Tailwind default sans for card text; Tailwind `font-mono` stack for diff lines only |

---

## UI Surface (what this phase adds)

1. **Diff view in the tool-call card.** When a card's result is a unified diff, the result `<pre>` is replaced by a diff block. Everything else in the card stays unchanged: `<details>` wrapper, summary `🔧 <server> · <tool> — ok`, «Аргументы» block, «Результат» label, red error styling.
2. **Detection (decision, from Claude's Discretion).** Content sniffing on the client, no server marker required. `isUnifiedDiff(text)` is true when `evt.ok` is true AND the text contains a line starting with `--- ` followed (next line) by `+++ ` AND at least one line starting with `@@ `. The new-file all-added diff must satisfy this too (server emits `--- /dev/null`, `+++ b/<path>`, `@@ -0,0 +1,N @@`). Error results never render as diff. If the check fails, the card renders as today (plain `<pre>`). This keeps `git_diff` results from the existing git server also coloured, which is acceptable and consistent.
3. **Line classification** (per line, top to bottom):
   - `+++ ` / `--- ` file headers: header style.
   - `@@ ... @@` hunk header: hunk style.
   - `+...` : added.
   - `-...` : removed.
   - `diff --git`, `index`, `new file mode`, `\ No newline at end of file`: header style.
   - everything else (context lines starting with a space, blank): context style.
4. **Construction rule (security).** Build the block with `document.createElement` and `textContent` only: one `<div>` per line inside a `<pre>`-like container. No `innerHTML` with diff text. If an HTML string is ever used it must pass through `DOMPurify.sanitize()`. Diff text comes from file contents (untrusted), so this is mandatory.
5. **Card behaviour with diffs.** The card for a write tool (`write_file`, `edit_file`) is rendered collapsed like every other card (existing `<details>` default); the summary line is unchanged, so the user sees the call, expands to see the diff. Optional summary suffix for diff results: ` · +A −R` (counts of added and removed lines, excluding headers) appended to the summary text to make a change visible while collapsed. Added in `textContent`, same summary classes.
6. **Truncation.** The existing label suffix «(обрезано)» applies unchanged (`evt.truncated`). A truncated diff keeps the server's explicit truncation marker line, rendered in context style.
7. **Final answer.** The model ends its answer by listing created or changed files (D-11); this is plain assistant markdown rendered by the existing renderer. No new component.
8. **Reload.** Cards restored from stored history go through the same `buildToolCallCard`, so a reloaded chat shows the same coloured diffs.

No new settings, no «Применить» button, no «Изменения» aggregate block (all deferred).

---

## Diff Block Specification

| Part | Classes / values |
|------|------------------|
| Container | `mt-0 rounded border border-slate-700 bg-slate-950 overflow-x-auto max-h-96 overflow-y-auto font-mono text-xs leading-relaxed` with `role="region"` and `aria-label="Diff изменения"`, `tabindex="0"` (keyboard scroll) |
| Line row | `div`, `whitespace-pre`, `px-2` (8px), `min-w-full w-max` so long lines scroll horizontally instead of wrapping |
| Added line | `bg-green-950/60 text-green-300` (text starts with `+`) |
| Removed line | `bg-red-950/60 text-red-300` (text starts with `-`) |
| Hunk header | `bg-slate-800 text-indigo-300` |
| File / meta header | `text-slate-400 font-semibold` (weight 600) |
| Context line | `text-slate-300` |

Colour is never the only signal: the leading `+` / `-` characters stay visible in the text. Contrast: green-300 on green-950/60 and red-300 on red-950/60 both exceed 4.5:1 against the dark background.

---

## Spacing Scale

Reused Tailwind utilities; no new values.

| Token | Value | Usage |
|-------|-------|-------|
| xs | 4px | `py-1`-level gaps, `mt-1` |
| sm | 8px | `px-2` diff line padding, `mt-2` label gaps (existing) |
| md | 12px (existing card only) | card `px-3 py-2` (existing, kept for parity); any new element uses 16px |

Exceptions: 12px (`px-3`) and 2px-class vertical rhythm come from the existing tool-call card; the max height `max-h-96` (384px) is a layout cap, not a spacing token.

---

## Typography

| Role | Size | Weight | Line Height |
|------|------|--------|-------------|
| Card text (summary, labels) | 12px (`text-xs`) | 400 | 1.5 |
| Diff lines (monospace) | 12px (`text-xs`, `font-mono`) | 400 | 1.625 (`leading-relaxed`) |
| Diff file headers | 12px | 600 (`font-semibold`) | 1.625 |

Sizes: one (12px) inside cards. Weights: 400 and 600 only.

---

## Color

Existing dark slate theme; the only new colours are the semantic diff colours, scoped to the diff block.

| Role | Value | Usage |
|------|-------|-------|
| Dominant (60%) | slate-900 / slate-950 | Page, card background (`bg-slate-900`), diff container (`bg-slate-950`) |
| Secondary (30%) | slate-800, border slate-700 | Card border, hunk header band |
| Accent (10%) | indigo | Hunk header text (`text-indigo-300`) only; existing focus ring `indigo-500` on the diff container |
| Added | green-300 on green-950/60 | Added diff lines only |
| Removed / destructive | red-300 on red-950/60 | Removed diff lines only; existing `text-red-400` for error cards unchanged |

Accent reserved for: hunk header text and the keyboard focus ring on the diff container.

---

## Copywriting Contract

All user-facing text in Russian. Almost no new copy: the card already has its labels.

| Element | Copy |
|---------|------|
| Primary CTA | none (the user types a goal in the ordinary chat input; example: `Напиши ADR о разделении UI/Agent на два процесса`) |
| Diff region label (`aria-label`) | Diff изменения |
| Summary suffix for a diff result | ` · +12 −3` (added / removed counts) |
| Result label | Результат (existing); «Результат (обрезано)» when truncated (existing) |
| Empty state | not applicable (no new list or panel). A write call that changes nothing returns the server's non-empty text, rendered in the plain `<pre>` path |
| Error state | Existing error card: summary `— ошибка`, label «Ошибка», red text with the server's English reason and fix (Phase 22 D-15). No new copy |
| Destructive confirmation | not applicable: writes save immediately by decision D-09, undo is through git; no delete or rename tools. No confirmation UI is added |

Documentation copy (Russian, `docs/USER_GUIDE.md`) for the `project-files` form entry is a docs task, not a UI contract item.

---

## Interaction and Accessibility

- The diff container is focusable (`tabindex="0"`) with visible focus ring `focus:outline-none focus:ring-2 focus:ring-indigo-500`; arrow keys scroll it.
- The card stays a native `<details>`/`<summary>`, so keyboard and screen reader toggling is unchanged.
- Loading and streaming: the card appears as soon as the tool result event arrives, before the final tokens, as today (`appendToolCallCard` inserts before the streaming bubble).
- Large diffs: container caps at `max-h-96` and scrolls; wide lines scroll horizontally; no layout shift of the chat column (`max-w-[75%]` kept).
- A tool result that is not a diff renders exactly as today; `/help` and `/support` UI are unchanged.

---

## Registry Safety

| Registry | Blocks Used | Safety Gate |
|----------|-------------|-------------|
| shadcn official | none | not applicable (shadcn not used) |
| third-party | none | not applicable |

CDN libraries stay as-is (Tailwind, Marked.js, DOMPurify 3.1.6). No new library, no highlighter. Diff text is inserted via `textContent` only (see Construction rule).

---

## Checker Sign-Off

- [x] Dimension 1 Copywriting: PASS
- [x] Dimension 2 Visuals: PASS
- [x] Dimension 3 Color: PASS
- [x] Dimension 4 Typography: PASS
- [x] Dimension 5 Spacing: FLAG (12px inherited from existing card only; new diff rows use px-2)
- [x] Dimension 6 Registry Safety: PASS

**Approval:** approved 2026-10-17
