# Retrieval probe: /support threshold and ticket-aware query

- Date: 2026-10-17T13:21:48+00:00
- Status: ran
- Embedding model: text-embedding-bge-m3
- Chunking: structural (size 2000, overlap 0)
- Chunk count: 351
- Corpus: README.md, docs/API_SPEC.md, docs/ARCHITECTURE.md, docs/FAQ.md, docs/TESTING_GUIDE.md, docs/USER_GUIDE.md

## Results

| Kind | Query | Best score | Best source | FAQ rank |
|------|-------|-----------|-------------|----------|
| support | Почему не работает авторизация? | 0.680 | docs/FAQ.md | 1 |
| support | Пишет слишком много неудачных попыток входа, что делать? | 0.773 | docs/FAQ.md | 1 |
| support | После входа снова просит войти | 0.621 | docs/FAQ.md | 1 |
| support | Как передать API-ключ в запросе? | 0.659 | docs/FAQ.md | 1 |
| support | Модель не отвечает, LM Studio is not running | 0.764 | docs/FAQ.md | 1 |
| support | MCP сервер не подключается | 0.780 | docs/FAQ.md | 1 |
| ticket | [T-1001] Почему не работает авторизация? Не могу войти, пишет "слишком много попыток" 429 Слишком много неудачных попыток входа. Повторите позже | 0.759 | docs/FAQ.md | 1 |
| ticket | [T-1002] Почему не работает авторизация? Вход по адресу сервера в локальной сети не работает 403 Cross-origin request rejected | 0.730 | docs/FAQ.md | 1 |
| outside | Какая погода завтра в Москве? | 0.426 | docs/API_SPEC.md | - |
| outside | Рецепт борща | 0.379 | docs/API_SPEC.md | - |

## Decisions

- Threshold: current 0.45, decision 0.45 (kept: every documentation score is at or above it, outside is below)
- Ticket-aware query: keep enabled (kept: both ticket-aware queries rank docs/FAQ.md at least as high as the bare question)
- FAQ is the best source for 6 of 6 support questions

## Applied changes

No change: DEFAULT_SUPPORT_THRESHOLD and SupportConfig.threshold stay 0.45, RETRIEVAL_HINT_ENABLED stays True, DEFAULT_HELP_THRESHOLD untouched.
