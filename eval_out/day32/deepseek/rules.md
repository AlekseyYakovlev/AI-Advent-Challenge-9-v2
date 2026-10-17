<!-- ai-pr-review -->
## Ревью Pull Request

Коммит: `f399dd4`
Модель: `DeepSeek/deepseek-flash`

### Потенциальные баги

- `agent/review_demo_audit.py:10` — в сигнатуре `record_title_change` использован символ `-›` (U+201A) вместо ASCII `->`; это синтаксическая ошибка, модуль не импортируется. Заменить на `->`.
- `agent/review_demo_audit.py:18` — в сигнатуре `safe_title_length` та же замена `-›` вместо `->`; исправить.
- `agent/review_demo_audit.py:11` — `print("title change requested", chat.id)` вместо структурного логирования; по правилам проекта (`structlog` вместо `print`) использовать `logger` из `structlog`.
- `agent/review_demo_audit.py:12` — `datetime.utcnow()` даёт naive-время и объявлен устаревшим; по правилам проекта использовать `datetime.now(timezone.utc)`.
- `agent/review_demo_audit.py:14-15` — `session.add(chat)` без `await session.commit()` (и без `rollback` при ошибке): изменение названия не сохранится, а возвращаемая строка журнала будет описывать несостоявшуюся запись. Добавить `commit` с обработкой исключения, как в `agent/titles.py:apply_title`.
- `agent/review_demo_audit.py:22-24` — `except:` без указания типа перехватывает в том числе `KeyboardInterrupt`/`SystemExit`; заменить на `except (AttributeError, TypeError):` или явную проверку `chat.title is None`.

### Архитектурные проблемы

- `agent/review_demo_audit.py:10` — функция меняет `chat.title` напрямую, минуя существующий механизм `agent/titles.py:apply_title`, который пишет заголовок условно (`Chat.title == DEFAULT_CHAT_TITLE`) и коммитит. Стоит переиспользовать/расширить `apply_title`, чтобы не появилось второго пути записи заголовка с другой семантикой.

### Рекомендации

- `agent/review_demo_audit.py:1-24` — новый модуль не покрыт тестами, хотя для логики заголовков есть `tests/test_titles.py`; добавить тесты на запись названия и на возвращаемую строку журнала.
- `agent/review_demo_audit.py:10` — имя `record_title_change` подразумевает только запись в журнал, но функция ещё и меняет состояние; уточнить имя или разделить запись в журнал и изменение заголовка.

<details><summary>Источники</summary>

**Документация**

- `docs/ARCHITECTURE.md` — Architecture Guide > Automated PR review (Day 32) > Modules and tables
- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Что положить в базу документации
- `docs/TESTING_GUIDE.md` — Testing Guide > API keys and HTTP chat (Day 30) > test_docs_day30.py (documentation source guard)
- `README.md` — AiAdventAgentV2 > Documentation

**Код**

- `tests/test_titles.py:1-38`
- `agent/titles.py:218-231`
- `tests/test_titles.py:196-199`
- `tests/test_titles.py:187-193`
- `agent/titles.py:192-215`
- `tests/test_titles_ws.py:39-43`

</details>
