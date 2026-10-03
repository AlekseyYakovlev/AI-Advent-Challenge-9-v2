# Phase 14: First RAG query (Day 22) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-03
**Phase:** 14-first-rag-query-day-22
**Areas discussed:** RAG controls in chat, Sources under answer, Retrieval and prompt, Eval and Day22 report

---

## RAG controls in chat

| Option | Description | Selected |
|--------|-------------|----------|
| Chat header | Next to #model-select, always visible, one click | ✓ |
| Chat settings modal | Next to compression strategy | |
| Above input | Toggle + select above #chat-form | |

| Option | Description | Selected |
|--------|-------------|----------|
| Toggle + KB select | KB stays attached when RAG is off | ✓ |
| Single select | "Без RAG" / "БЗ: …" | |

| Option | Description | Selected |
|--------|-------------|----------|
| Header badge + per-answer label | Comparison visible in history | ✓ |
| Header badge only | | |

| Option | Description | Selected |
|--------|-------------|----------|
| kb_id SET NULL, only ready KBs in select | Warning per RAG-04 | ✓ |
| Keep binding, show red badge | | |

## Sources under answer

| Option | Description | Selected |
|--------|-------------|----------|
| Collapsed "Источники (N)" | Like tool_trace | ✓ |
| Always expanded list | | |

| Option | Description | Selected |
|--------|-------------|----------|
| Meta + snippet via chunk_id | Reuse test-search card, text not copied | ✓ |
| Meta only | | |
| Meta + snippet copied into rag_sources | | |

| Option | Description | Selected |
|--------|-------------|----------|
| Persistent warning line + toast | | ✓ |
| Toast only | | |

| Option | Description | Selected |
|--------|-------------|----------|
| In final done frame | | ✓ |
| Separate pre-stream frame | | |

## Retrieval and prompt

| Option | Description | Selected |
|--------|-------------|----------|
| Default 5, API only | UI in Phase 15 | |
| Default 5 + number field in UI now | | ✓ |
| Fixed 5 | | |

Follow-up: K field placement — header next to toggle, shown only when RAG on (✓) vs chat settings modal.

| Option | Description | Selected |
|--------|-------------|----------|
| Last user message of outbound copy | | ✓ |
| System prompt | | |

| Option | Description | Selected |
|--------|-------------|----------|
| Soft: rely on fragments, cite [N] | | ✓ |
| Minimal: context + question | | |

| Option | Description | Selected |
|--------|-------------|----------|
| 30% context_length, drop lowest-score | | ✓ |
| Fixed tokens (~3000) | | |

## Eval and Day22 report

| Option | Description | Selected |
|--------|-------------|----------|
| Claude drafts (6 direct / 2 synthesis / 2 out-of-corpus), user approves | | ✓ |
| User provides questions | | |

| Option | Description | Selected |
|--------|-------------|----------|
| Local qwen 9B, DeepSeek optional | | ✓ |
| DeepSeek | | |
| Both | | |

| Option | Description | Selected |
|--------|-------------|----------|
| nomic with/without prefix + fixed vs structural chunking | | |
| Load a second embedding model (bge-m3 / e5) | | ✓ |
| nomic only, no A/B | | |

Follow-up: LM Studio check showed `text-embedding-bge-m3` already downloaded (type embeddings). Routing guarantee — mini-spike + dim guard (✓) vs always one embedder at a time.

| Option | Description | Selected |
|--------|-------------|----------|
| Auto hit@k + manual verdict (Claude fills, user reviews) | | ✓ |
| Raw answers + hit@k only | | |

## Claude's Discretion

- rag_sources JSON shape, done.rag fields, migration, module split, token multiplier, snippet length, header styling, fixture/report locations.

## Deferred Ideas

- Phase 15 knobs (candidate-K, threshold, rerank, rewrite, Детали поиска, LLM-judge); Phase 16 quotes and "не знаю"; multi-KB and per-message override (Future).
