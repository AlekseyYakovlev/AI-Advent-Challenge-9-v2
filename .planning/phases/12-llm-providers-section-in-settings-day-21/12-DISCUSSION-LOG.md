# Phase 12: LLM providers section in Settings (Day 21) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions captured in 12-CONTEXT.md.

**Date:** 2026-10-02
**Phase:** 12-llm-providers-section-in-settings-day-21
**Areas discussed:** Provider config & secrets, Routing & LM Studio, Connection check & model list, Picker entries & identity

## Provider config & secrets
| Question | Options | Selected |
|---|---|---|
| Fields | Name+URL+key / Type preset+key / You decide | Name + base URL + API key |
| Key storage | Plain in DB masked / Encrypted / Ref to .env var | Reference to .env var |
| Legacy .env key | Fallback only / Auto-seed DeepSeek / Remove | Auto-seed a DeepSeek provider |

## Routing & LM Studio
| Question | Options | Selected |
|---|---|---|
| LM Studio | Built-in non-editable / Regular provider row / Outside providers | Regular provider row |
| Model identity | Composite id / Separate provider_id | Separate provider_id field |
| Routing scope | chat, title, scheduler, summaries/invariants | All four |

## Connection check & model list
| Question | Selected |
|---|---|
| Check | GET /v1/models, auto after save + manual button |
| Model list | Live fetch, cached in memory |
| Picker | "Provider · model", grouped; failed providers contribute nothing |
| Delete rule | Allow; references fail with clear error |

## Claude's Discretion
Table/column names, kind discriminator, REST shapes, cache policy, wording, markup, tests, 999.11 folding.

## Deferred Ideas
Key encryption, usage/cost stats, non-OpenAI-compatible providers.
