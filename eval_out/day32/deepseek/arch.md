<!-- ai-pr-review -->
## Ревью Pull Request

Коммит: `cd3c65f`
Модель: `DeepSeek/deepseek-flash`

### Потенциальные баги

- `agent/review_demo_worker.py:10` — в сигнатуре `_rebuild` и в `start_rebuild` (строка 15) использован символ `-›` (U+203A) вместо `->`; это синтаксическая ошибка, модуль не импортируется. Заменить на ASCII-стрелку `->`.
- `ui/review_demo_status.py:3` — `ui` импортирует `agent.state`, что нарушает правило проекта «`ui` не импортирует `agent`»; при этом `active_streams` и `chat_locks` живут в процессе агента, поэтому в UI-процессе значения будут всегда нулевыми. Нужно получать эти данные через API/события агента, а не прямым импортом.
- `agent/review_demo_worker.py:15` — `start_rebuild` возвращает `multiprocessing.Process`, но нигде не сохраняет ссылку и не вызывает `join`; процесс может быть собран сборщиком мусора до завершения. Стоит хранить ссылки на запущенные процессы (например, в модульном множестве) и предусмотреть ожидание/очистку.

### Архитектурные проблемы

- `agent/review_demo_worker.py:10` — `_rebuild` только логирует и ничего не перестраивает, а `start_rebuild` запускает процесс без передачи сессии/БД и без обработки ошибок; фоновое перестроение сводки фактически не реализовано. Нужно либо реализовать логику перестроения, либо не добавлять воркер в этом PR.
- `ui/review_demo_status.py:6` — `collect_status` возвращает счётчики in-process структур агента, что не даёт корректного состояния при разделении процессов UI и агента; требуется источник данных, общий для обоих процессов.

### Рекомендации

- `agent/review_demo_worker.py:1` — добавить тесты на запуск воркера (по аналогии с `tests/test_generation_queue.py`), иначе поведение фонового перестроения не покрыто.
- `ui/review_demo_status.py:6` — зафиксировать контракт ответа (типы/ключи) и покрыть его тестом, чтобы страница состояния не ломалась при изменениях.

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
