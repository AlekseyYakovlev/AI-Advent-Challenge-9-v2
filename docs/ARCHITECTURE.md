# Architecture Guide

## Process Communication
- UI launches Agent via asyncio.create_subprocess_exec
- Healthcheck: GET /health every 3 seconds
- Orphan cleanup: psutil to find/kill processes on port 8001

## Context Management Strategies
1. Sliding Window: keep last N messages
2. Sticky Facts: extract JSON facts from user messages, inject into prompt
3. Branching: parent_id tree, current_leaf_message_id

## Summarization Algorithm
- Trigger: >75% of max_tokens
- Language: STRICTLY ENGLISH
- Accumulation: [Old Summary] + [New Summary] + [Recent N pairs]
- Re-summarize if summary >1500 tokens

## LM Studio Control API
- List models: GET /v1/models
- Load model: POST /api/v0/models/load
- Unload model: POST /api/v0/models/unload
- Emergency unload on timeout: 5s hard timeout