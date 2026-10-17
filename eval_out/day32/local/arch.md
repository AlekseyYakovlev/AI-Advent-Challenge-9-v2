<!-- ai-pr-review -->
## Ревью Pull Request

Коммит: `2b694ed`
Модель: `LM Studio/qwen/qwen3.5-9b`

### Потенциальные баги

`agent/review_demo_worker.py:13`: функция `_rebuild` объявлена как `async def`, но внутри нет ожидания асинхронных операций и не используется ключевое слово `await`; при запуске в отдельном процессе через `multiprocessing.Process` это приведёт к тому, что код завершится мгновенно без выполнения логики перестроения сводки.

### Архитектурные проблемы

`agent/review_demo_worker.py:1`: использование модуля `multiprocessing` для запуска фоновых задач в асинхронном веб-сервере на FastAPI некорректно, так как каждый процесс получает собственный интерпретатор Python и отдельный цикл событий, что делает невозможным корректную работу с глобальными асинхронными объектами (сессии БД, WebSocket соединения) и приводит к утечкам памяти или ошибкам при попытке доступа к общему состоянию приложения.

### Рекомендации

`agent/review_demo_worker.py:1`: заменить `multiprocessing.Process` на использование `asyncio.create_task` для запуска фоновых задач в рамках текущего цикла событий; если задача должна выполняться независимо от основного потока, использовать библиотеку `anyio` или `arq`/`celery`, которые корректно интегрируются с асинхронным стеком.

<details><summary>Источники</summary>

**Документация**

- `CLAUDE.md` — Install dependencies > Layers
- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Что положить в базу документации
- `CLAUDE.md` — Install dependencies > Testing
- `docs/ARCHITECTURE.md` — Architecture Guide > Automated PR review (Day 32) > Modules and tables

**Код**

- `agent/context_engine.py:560-573`
- `agent/ws.py:721-734`
- `tests/test_generation_queue.py:97-104`
- `agent/http_chat.py:1-38`
- `agent/http_chat.py:100-125`
- `tests/test_http_chat.py:1-45`

</details>
