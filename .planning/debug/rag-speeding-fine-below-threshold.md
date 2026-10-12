---
status: investigating
trigger: "[screenshot only, no text] RAG chat (KB 'КОАП', K=15, rewrite on): question 'Штраф за превышение скорости на 26 км в час' gets 'в базе знаний нет информации' / 'fragments below threshold'"
created: 2026-10-04
updated: 2026-10-04
---

## Current Focus

hypothesis: unknown — candidate causes: (a) threshold 0.67 too strict for the embedding model on Russian legal text (best cos 0.61-0.64), (b) the relevant KoAP article (ст. 12.9) is missing or poorly chunked in the KB, (c) rewrite stage (20→0 in the details) is not improving the query, (d) the second answer states general-knowledge facts despite the "no KB" verdict
test: gather initial evidence (agent/rag_pipeline.py _apply_threshold/_rewrite_stage, agent/rag.py resolve_threshold, KB chunks for 12.9)
expecting: find whether the right chunk exists and what cos it gets
next_action: gather initial evidence

## Symptoms

<!-- DATA_START -->
expected: Question about the fine for exceeding speed by 26 km/h (КоАП ст. 12.9, 21-40 km/h range) retrieves the matching КоАП chunk above the threshold; the answer is grounded in the KB and cites sources.
actual: Both queries ("...на 26 км/ч", "...на 21-40 км/ч") return "Фрагменты не прошли порог (лучший 0.61 < 0.67)" / "(лучший 0.64 < 0.67)". Answer 1: "В базе знаний нет информации...". Answer 2: "Ответ не основан на базе знаний. По общим данным РФ: штраф 2500 руб., лишение прав от 4 до 6 месяцев или арест до 15 суток" (general knowledge, not from the KB). Details table: top-10 candidates all Kodex_ot_30_12_2001_N_19... chunks (1-736, 1-757, 1-758 ...), cos 0.64..0.59, status "ниже порога", stage chips: порог 0.67 · rewrite · 20→0 · 1449 мс.
errors: none (not an exception, an empty retrieval result)
reproduction: KB "КОАП", RAG on, K=15, model LM Studio qwen/qwen3.5-9b; ask "Штраф за превышение скорости на 21-40 км/ч"
started: unknown (Week 5 RAG phases; threshold calibration phase)
<!-- DATA_END -->

## Eliminated

- hypothesis: (b) ст. 12.9 missing/poorly chunked in KB
  evidence: kbchunk id 737 (chunk_index 736, "Статья 12.9") exists, contains parts 2-5 with the exact 21-40 km/h fine; it is the #1 candidate in the details table.
- hypothesis: filename-heavy chunk header dilutes the embedding
  evidence: re-embedded chunk with/without the filename header via LM Studio bge-m3: 0.629 vs 0.633 vs 0.619 (body only) for query 1. No meaningful change.

## Evidence

- checked: live bge-m3 cosine of the correct chunk vs the user's queries
  found: 0.629 (26 км/ч), 0.637 (21-40 км/ч); a keyword-style query ("более 20 но не более 40 километров в час") gives 0.737, a single-part chunk 0.82
  implication: retrieval ranks the right chunk first; the colloquial-question vs legal-text cosine is simply below the 0.67 cut
- checked: eval_out/day23/calibration.md, bge section
  found: threshold 0.67 = midpoint between gold min 0.674 and out-of-corpus max 0.656 on the frozen calibration set; control set already has answerable Q01 (0.639), Q07 (0.653), Q08 (0.634) cut to 0 survivors
  implication: 0.67 is known to reject answerable colloquial questions; KOAP/legal prose shifts cosines down further
- checked: chatragconfig for chat 1
  found: threshold NULL (-> calibrated 0.67), hybrid=0, lexical=0; rewrite=1 (rewrite only adds another search, cannot raise raw cosine of this chunk above ~0.64)
  implication: FTS exemption (hybrid) is off, so nothing rescues the below-threshold chunk

## Resolution

root_cause: Not a retrieval/indexing bug. Correct chunk (12.9) is retrieved at rank 1 but its raw bge-m3 cosine (0.61-0.64) is below the calibrated global cut-off 0.67 (agent/rag.py CALIBRATED_THRESHOLDS), which was set on a small calibration set and is known to cut answerable questions; hybrid FTS exemption is off. Second answer's general-knowledge text is the NO_FRAGMENTS_INSTRUCTION allowing a labelled general-knowledge answer (also factually wrong vs the KB: 750 rub).
root_cause_status: confirmed; fix not applied (design decision pending)
fix:
verification:
files_changed:
