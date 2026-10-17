<!-- ai-pr-review -->
## Ревью Pull Request

Коммит: `3d37e0a`
Модель: `DeepSeek/deepseek-flash`

### Потенциальные баги

- `agent/review_demo_format.py:7` — в сигнатуре `def format_size(size_bytes: int) -› str:` и далее по файлу используется символ `-›` (U+203A) вместо ASCII `->`; это синтаксическая ошибка, модуль не импортируется. Заменить на `->` во всех сигнатурах и аннотациях.
- `agent/review_demo_format.py:9` — условие `if size_bytes ‹ 0:` использует символ `‹` (U+2039) вместо `<`; аналогично в строках с `while value ›= 1024 and unit ‹ len(_UNITS) - 1` и `if seconds ‹ 0:`. Заменить на `<` и `>=`.
- `tests/test_review_demo_format.py:8` — в сигнатурах тестов `def test_format_size_small() -› None:` и остальных используется `-›` вместо `->`; файл не соберётся. Заменить на ASCII-стрелку.
- `agent/review_demo_format.py:20` — `format_duration` усекает дробные секунды через `int(seconds)`, поэтому `format_duration(59.9)` вернёт `"59 с"`, а `format_duration(60.0)` — `"1 мин 0 с"`; поведение не описано и не покрыто тестами. Либо явно задокументировать усечение в docstring, либо округлять (`round`) и добавить тест на дробное значение.
- `agent/review_demo_format.py:14` — при `size_bytes`, равном `1024**4` и больше, цикл останавливается на `ГБ`, и значение выводится как `1024.0 ГБ`; для больших размеров стоит либо добавить `ТБ`, либо явно указать в docstring, что выше `ГБ` значение не масштабируется.

### Архитектурные проблемы

Замечаний нет.

### Рекомендации

- `agent/review_demo_format.py:4` — `_UNITS` объявлен как `tuple[str, ...]`, но индексация по нему в `format_size` не проверяется на выход за границы; при добавлении единиц стоит покрыть тестом граничные значения (`1023`, `1024`, `1024**2`, `1024**3`).
- `tests/test_review_demo_format.py:21` — тест `test_format_duration` проверяет два случая в одной функции; разделить на отдельные тесты для минут и секунд, чтобы падение было однозначным.
- `tests/test_review_demo_format.py:1` — нет теста на отрицательную длительность, хотя `format_duration` бросает `ValueError`; добавить тест, аналогичный `test_format_size_negative`.

<details><summary>Источники</summary>

**Документация**

- `docs/API_SPEC.md` — API Specification > Scheduler (Day 18) > Response shapes
- `docs/TESTING_GUIDE.md` — Testing Guide > Automated PR review (Day 32) > test_review_prompts.py
- `docs/TESTING_GUIDE.md` — Testing Guide > Strategy Tests > test_sticky_facts_creates_summary
- `docs/ARCHITECTURE.md` — Architecture Guide > Automated PR review (Day 32) > Modules and tables

**Код**

- `tests/test_review_prompts.py:48-53`
- `tests/test_review_judge.py:130-139`
- `tests/test_review_prompts.py:20-30`
- `tests/test_kb_add_api.py:51-52`
- `agent/mcp_client.py:146-150`
- `tests/test_limits_config.py:92-107`

</details>
