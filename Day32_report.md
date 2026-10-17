# Day 32 — Ревью Pull Request: отчёт

## Задание

Задание дня 32: собрать пайплайн, который запускается на Pull Request, передаёт ассистенту diff и изменённые файлы, использует RAG (документация проекта и код) и возвращает потенциальные баги, архитектурные проблемы и рекомендации.

Что построено в этой фазе:

- GitHub Action `.github/workflows/pr-review.yml`, который по событию Pull Request вызывает приложение и публикует одно ревью комментарием в PR.
- Маршрут приложения `POST /api/v1/reviews` и сервис ревью (`agent/review_api.py`, `agent/review_service.py`, `agent/review_pipeline.py`, `agent/review_github.py`, `agent/review_prompts.py`).
- Раздел «Ревью Pull Request» в настройках приложения: путь к репозиторию, провайдер и модель ревью, обновление индекса кода.
- Скрипты `scripts/pr_review_ci.py` (шаг Action), `scripts/review_control.py` (контрольный набор), `scripts/review_judge.py` (судья DeepSeek).
- Контрольный набор из пяти замороженных PR и два прогона на двух моделях: локальной и облачной.

## Как устроен пайплайн

1. PR открыт или обновлён: Action запускается на self-hosted раннере с меткой `pr-review` (D-01).
2. Шаг запускает `scripts/pr_review_ci.py`, который вызывает `POST /api/v1/reviews` приложения с ключом API `github-actions-review` (D-03).
3. Сервис читает номер PR, diff и список изменённых файлов через MCP сервер `github-mcp-server` кодом приложения, а не моделью (D-02).
4. Индекс кода базовой ветки строится через git и хранится в приложении (D-07, D-09, D-10, D-11); первое построение для новой базовой ветки медленное.
5. Поиск идёт по двум базам с раздельными квотами результатов: база документации и индекс кода (D-08).
6. Небольшой PR обрабатывается одним вызовом модели; большой идёт по файлам с последующим вызовом, который сливает частичные ревью (D-06). В комментарии при этом стоит строка «Ревью выполнено по частям: N».
7. Результат — один русскоязычный комментарий с тремя разделами «Потенциальные баги», «Архитектурные проблемы», «Рекомендации» и блоком «Источники» (D-12, D-13). Комментарий с маркером `<!-- ai-pr-review -->` заменяется при новых коммитах, а не дублируется (D-14, D-15).
8. Если ревью получить нельзя, задача Action красная, а в сводке одна строка с названной причиной (D-04). Список строк в `docs/USER_GUIDE.md`, раздел «Причины отказа».
9. Провайдер и модель берутся из настроек ревью, по умолчанию локальная модель (D-05); настройки читает `GET /api/v1/reviews/config`, меняет `PUT /api/v1/reviews/config`.

Остальные маршруты: `POST /api/v1/reviews/pick-folder`, `GET /api/v1/reviews/code-index`, `POST /api/v1/reviews/code-index/refresh`.

## Безопасность раннера

Репозиторий публичный, а self-hosted раннер выполняет код на компьютере владельца. Решение, принятое в плане 24-12 (`24-CHECKS.md`, раздел 2): вариант `public-safe-config` (репозиторий остаётся публичным, раннер защищён настройками). Остаточный риск был показан пользователю до ответа.

Применённые настройки:

- Settings → Actions → General: одобрение запуска для всех внешних участников и токен только на чтение (сделано пользователем).
- Условие задачи `github.event.pull_request.head.repo.full_name == github.repository` (PR из форков не запускают ревью), `permissions` только `contents: read` и `pull-requests: write`, `actions/checkout` закреплён по коммиту, данные события передаются только через переменные окружения.
- Отдельный ключ API `github-actions-review`, MCP сервер GitHub с флагом `--read-only`, раннер с уникальной меткой `pr-review`.

Остаточный риск простыми словами: одобрение запусков не даёт полной защиты (автор, чья правка уже принята, может больше не требовать одобрения), а условие про тот же репозиторий записано в самом файле задачи, который PR может изменить. Единственная полная защита — приватный репозиторий. Поэтому раннер нужно держать выключенным, когда он не нужен, а ключ после демонстрации отозвать.

## Контрольный набор

Пять PR заморожены в манифесте `tests/fixtures/review/control_set.json` (D-16); последний коммит манифеста до прогонов: `fd72730da1e4954ac4c9fe787a41d217d48ca1a6`. Все PR открыты в ветку `Day32`. Ссылки (D-17):

| id | Вид | PR | Ветка | Название |
|----|-----|----|-------|----------|
| bug | obvious_bug | [#2](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/2) | `review-test/bug` | Добавить постраничный вывод записей базы знаний |
| rules | documented_rule | [#3](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/3) | `review-test/rules` | Добавить журнал изменений названия чата |
| arch | architecture | [#4](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/4) | `review-test/arch` | Добавить страницу состояния и фоновое перестроение сводки |
| large | large_pr | [#5](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/5) | `review-test/large` | Вынести основные модули агента в отдельный пакет |
| clean | clean | [#6](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/6) | `review-test/clean` | Добавить форматирование размеров и длительностей |

Дымовой PR: [#1](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/1), ветка `review-test/smoke`, в оценке не участвует.

Ожидаемые находки (дефекты внесены намеренно; «нужно» — какое знание требуется для находки):

| PR | Ключ | Файл | Нужно | Что внесено |
|----|------|------|-------|-------------|
| bug | B1 | `agent/review_demo_pagination.py` | только diff | Срез страницы в slice_page отбрасывает последний элемент каждой страницы (ошибка на единицу в конце среза). |
| bug | B2 | `agent/review_demo_pagination.py` | только diff | average_page_fill делит на ноль для пустого списка элементов. |
| bug | B3 | `agent/review_demo_pagination.py` | код | Вызов _chunk_rows передаёт аргументы в неверном порядке и не соответствует реальной сигнатуре (kb_id, doc, drafts). |
| rules | R1 | `agent/review_demo_audit.py` | документация | Вместо логгера structlog используется print(), что запрещено правилами проекта. |
| rules | R2 | `agent/review_demo_audit.py` | документация | Используется datetime.utcnow() вместо datetime.now(timezone.utc). |
| rules | R3 | `agent/review_demo_audit.py` | документация | Запись в базу через session.add без await session.commit() и без отката при ошибке. |
| rules | R4 | `agent/review_demo_audit.py` | документация | Голый except: без указания типа исключения. |
| arch | A1 | `ui/review_demo_status.py` | документация | Процесс интерфейса импортирует agent.state и читает память агента напрямую, хотя процессы общаются только по HTTP и WebSocket. |
| arch | A2 | `agent/review_demo_worker.py` | документация | Фоновая работа запускается через multiprocessing.Process, что запрещено ограничениями проекта. |
| large | L1 | `agent/review_demo_big/providers.py` | только diff | В копии get_provider_row убрана проверка, отказывающая отключённому провайдеру. |
| large | L2 | `agent/review_demo_big/kb_indexer.py` | только diff | В копии _commit_addition подмена индекса promote_index выполняется до await session.commit(). |

PR `clean` не содержит дефектов: любое утверждение о проблеме в нём считается ложным замечанием. В PR `large` скопированы основные модули агента, два дефекта внесены в копии.

Все шесть тестовых PR закрыты без слияния (`python scripts/review_control.py close`, вывод: «закрыт без слияния» для каждого); ветки `review-test/*` остаются как доказательство (проверено `git ls-remote origin "refs/heads/review-test/*"`: шесть веток).

## Условия запуска

| Параметр | Значение |
|----------|----------|
| Дата | 16-17 октября 2026; запись прогонов `local` и `deepseek` по `run.json`: 2026-10-16 22:47 UTC и 22:53 UTC |
| Прогон `local` | провайдер LM Studio, модель `qwen/qwen3.5-9b`, длина контекста 65536 |
| Прогон `deepseek` | провайдер DeepSeek, модель `deepseek-flash` |
| Модель эмбеддингов | по умолчанию `text-embedding-bge-m3` (в `run.json` отдельно не записана; значение берётся из базы документации, иначе из `DEFAULT_EMBEDDING_MODEL`) |
| База документации | 7 файлов, ниже; `CLAUDE.md` в базе был |
| База кода | ветка `Day32`, коммит `44680c7` на момент прогонов (запись в `24-CHECKS.md`, 4.1) |
| Раннер | self-hosted, Windows, метка `pr-review`, на машине владельца |
| Судья | DeepSeek `deepseek-flash` (см. раздел «Результаты») |

Файлы базы документации (`eval_out/day32/docs_kb_files.json`):

| Файл | Размер, байт |
|------|--------------|
| `CLAUDE.md` | 29979 |
| `README.md` | 6655 |
| `docs/API_SPEC.md` | 82949 |
| `docs/ARCHITECTURE.md` | 71328 |
| `docs/TESTING_GUIDE.md` | 72476 |
| `docs/USER_GUIDE.md` | 68316 |
| `requirements.txt` | 3675 |

Второй прогон сделан повторным запуском задачи (`rerun`) на тех же PR: в каждую тестовую ветку добавлен один новый коммит с тем же деревом файлов. Поэтому у PR новый `head_sha`, а id комментария тот же, что в первом прогоне.

## Проверка пайплайна на тестовом PR

Из `24-CHECKS.md`, раздел 3:

- Дымовой PR [#1](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/1): результат `ok`. Комментарий с маркером `<!-- ai-pr-review -->`, тремя заголовками, строкой коммита, равной head PR (`6f670f0`), и строкой модели `LM Studio/qwen/qwen3.5-9b`. В источниках 4 раздела документации и 6 диапазонов кода.
- Замена комментария (D-14): после повторного запуска head PR стал `bb09d53`, id комментария тот же (`6225967607`), `Коммит: bb09d53`, маркер встречается один раз. Комментарий заменён, не продублирован.
- Права токена задачи (предположение A3): подтверждено, достаточно `contents: read` и `pull-requests: write`, комментарий опубликован.
- Размер комментария (A4): 1756 символов, далеко от любого предела.
- Запасные варианты не потребовались: отказов 403, отказов по размеру и красных задач не было.
- `workflow_conclusion` и длительность задач скрипт `review_control.py` не сохраняет (`null` в записях): не записано.
- Замечание задним числом: «опечатка» `›` вместо `->`, найденная ревью дымового PR, на самом деле артефакт подготовки запроса, см. «Ограничения».

## Результаты

Судья — DeepSeek, `python scripts/review_judge.py --run local --run deepseek --model deepseek-flash`. Модель судьи по умолчанию (`deepseek-chat`) на момент прогона недоступна (preflight: `model deepseek-chat is not available`), поэтому id передан явно. Два первых прохода (`deepseek-v4-pro`, затем `deepseek-flash`) дали ответы судьи, которые не разобрались: бюджет ответа в 900 токенов целиком уходил на рассуждение модели и ответ был пустым. Бюджет поднят до 8000, весь проход выполнен заново для всех десяти записей, после чего все записи имеют `judge_status: ok`. Ни одна запись не пересуживалась ради лучшего вердикта.

#### Найденные дефекты

| PR | Дефект | Файл | Нужно знание | deepseek | local |
|---|---|---|---|---|---|
| bug | B1 | agent/review_demo_pagination.py | только diff | найден | найден |
| bug | B2 | agent/review_demo_pagination.py | только diff | найден | найден |
| bug | B3 | agent/review_demo_pagination.py | код | найден | не найден |
| rules | R1 | agent/review_demo_audit.py | документация | найден | найден |
| rules | R2 | agent/review_demo_audit.py | документация | найден | найден |
| rules | R3 | agent/review_demo_audit.py | документация | найден | не найден |
| rules | R4 | agent/review_demo_audit.py | документация | найден | найден |
| arch | A1 | ui/review_demo_status.py | документация | найден | не найден |
| arch | A2 | agent/review_demo_worker.py | документация | не найден | найден |
| large | L1 | agent/review_demo_big/providers.py | только diff | не найден | не найден |
| large | L2 | agent/review_demo_big/kb_indexer.py | только diff | найден | не найден |

#### Итог по PR

| PR | Вид | deepseek: найдено | deepseek: ложных | deepseek: модель | local: найдено | local: ложных | local: модель |
|---|---|---|---|---|---|---|---|
| bug | obvious_bug | найдено 3 из 3 | ложных: 1 | DeepSeek/deepseek-flash | найдено 2 из 3 | ложных: 0 | LM Studio/qwen/qwen3.5-9b |
| rules | documented_rule | найдено 4 из 4 | ложных: 2 | DeepSeek/deepseek-flash | найдено 3 из 4 | ложных: 1 | LM Studio/qwen/qwen3.5-9b |
| arch | architecture | найдено 1 из 2 | ложных: 2 | DeepSeek/deepseek-flash | найдено 1 из 2 | ложных: 1 | LM Studio/qwen/qwen3.5-9b |
| large | large_pr | найдено 1 из 2 | ложных: 5 | DeepSeek/deepseek-flash | найдено 0 из 2 | ложных: 7 | LM Studio/qwen/qwen3.5-9b |
| clean | clean | найдено 0 из 0 | ложных: 5 | DeepSeek/deepseek-flash | найдено 0 из 0 | ложных: 3 | LM Studio/qwen/qwen3.5-9b |

#### Вклад поиска

| Нужно знание | deepseek | local |
|---|---|---|
| только diff | 3 из 4 | 2 из 4 |
| документация | 5 из 6 | 4 из 6 |
| код | 1 из 1 | 0 из 1 |

#### Ложные замечания

| Прогон | PR | Замечание |
|---|---|---|
| deepseek | bug | agent/review_demo_pagination.py:12 — утверждение о делении на ноль в page_count при page_size <= 0 не соответствует внесённым дефектам: это спекулятивный граничный случай про невалидный аргумент, а не проблема, реально присутствующая в изменённом коде. |
| deepseek | rules | agent/review_demo_audit.py:10 — утверждение о символе -› (U+201A) вместо ASCII -› с «исправлением» на тот же символ: замечание о несуществующей проблеме, формулировка самопротиворечива. |
| deepseek | rules | agent/review_demo_audit.py:18 — то же самое дублирующее замечание про -› в сигнатуре safe_title_length, также не подтверждаемое содержательно. |
| deepseek | arch | agent/review_demo_worker.py:10 — утверждение о синтаксической ошибке из-за символа U+203A вместо ASCII-стрелки внутренне противоречиво (в тексте оба символа указаны как одинаковые -›), такой дефект в изменённом коде не заявлен и не подтверждается. |
| deepseek | arch | agent/review_demo_worker.py:15 — проблема «процесс может быть собран сборщиком мусора до завершения» не существует: Process удерживается внутренним реестром multiprocessing до завершения, а отсутствие join для фоновой задачи само по себе не дефект; проверяемой проблемы в коде нет. |
| deepseek | large | agent/review_demo_big/context_engine.py:1 — утверждение о невалидном Python и символах -›, ‹/› вместо ASCII-операторов. |
| deepseek | large | agent/review_demo_big/rag_turn.py:1 — утверждение о синтаксических ошибках из-за символов -›, ‹. |
| deepseek | large | agent/review_demo_big/scheduler.py:1 — утверждение о несобираемом коде из-за -›, ‹, ›. |
| deepseek | large | agent/review_demo_big/scheduler_ops.py:1 — утверждение о недопустимых символах и неимпортируемом модуле. |
| deepseek | large | agent/review_demo_big/tools.py:1 — утверждение о некомпилируемом модуле из-за -›, ‹, ›. |
| deepseek | clean | agent/review_demo_format.py:7 — утверждение о подмене ASCII-стрелки на U+203A не подтверждается: в самом тексте замечания оба символа выглядят одинаково, то есть это артефакт кодировки/рендеринга, а не реальная синтаксическая ошибка в файле. |
| deepseek | clean | agent/review_demo_format.py:9 — то же самое: заявленные символы ‹/›= в тексте замечания идентичны «правильным», реальной подмены символов сравнения в коде нет. |
| deepseek | clean | tests/test_review_demo_format.py:8 — та же мнимая проблема с -› в сигнатурах тестов; файл не содержит этой ошибки. |
| deepseek | clean | agent/review_demo_format.py:20 — приписываемое format_duration недокументированное усечение через int(seconds) и отсутствие тестов на дробные значения в коде не подтверждается (поведение описано/покрыто), проблема не существует. |
| deepseek | clean | agent/review_demo_format.py:14 — цикл масштабирования не «застревает» на ГБ: вывод 1024.0 ГБ для размеров ≥ 10244 и отсутствие упоминания порога — несуществующая в коде проблема. |
| local | rules | agent/review_demo_audit.py:16: утверждение, что вызов session.add(chat) избыточен и его следует удалить, — проблемы избыточности в изменённом коде нет (напротив, запись в базу как раз требует session.add с последующим коммитом), это замечание не соответствует ни одному из внесённых дефектов. |
| local | arch | agent/review_demo_worker.py:13 — утверждение о том, что async def _rebuild без await «завершится мгновенно» при запуске через multiprocessing.Process, не соответствует ни одному из заведомо внесённых дефектов и описывает несуществующую в изменениях проблему. |
| local | large | context_engine.py:150 — обращение к chat.user_id без проверки на None в изменённом коде не подтверждается и к внесённым дефектам не относится. |
| local | large | context_engine.py:150 — рассуждение про owner_id/Settings с chat_id=None описывает несуществующую проблему в изменённом коде. |
| local | large | context_engine.py:385 — утверждение о «синтаксической ошибке» из-за оператора сравнения в _apply_compression_strategy не соответствует коду. |
| local | large | rag_turn.py:102 — заявленная логическая ошибка из-за оператора сравнения в load_history_pairs в коде отсутствует. |
| local | large | rag_turn.py:291 — предполагаемый UnboundLocalError для top_k в except RagFailure не подтверждается кодом. |
| local | large | scheduler.py:105 — мнимый дефект оператора сравнения в claim_slot не существует в изменённом коде. |
| local | large | scheduler.py:124 — мнимый дефект оператора сравнения в claim_slot не существует в изменённом коде. |
| local | clean | agent/review_demo_format.py:13 — утверждение об ошибке сравнения ›/›= не подтверждается ожидаемыми дефектами. |
| local | clean | agent/review_demo_format.py:13 — утверждение о неверном символе ‹ в условии цикла не подтверждается. |
| local | clean | agent/review_demo_format.py:20 — утверждение об аналогичной ошибке в проверке длительности не подтверждается. |

В прогоне DeepSeek судья и автор ревью — один и тот же провайдер; оценка этого прогона может быть завышена.

В прогоне DeepSeek судья и автор ревью — один и тот же провайдер; оценка этого прогона может быть завышена.

В прогоне DeepSeek судья и автор ревью — один и тот же провайдер и даже одна и та же модель `deepseek-flash`; оценка этого прогона может быть завышена.

### Сравнение моделей

**DeepSeek `deepseek-flash`:** найдено 9 из 11 дефектов (bug 3 из 3, rules 4 из 4, arch 1 из 2, large 1 из 2). Пропущены A2 (упомянут `multiprocessing.Process`, но только как проблема сборки мусора и `join`, а не как запрещённый проектом механизм) и L1 (убрана проверка отключённого провайдера). Ложных замечаний 15. По моему подсчёту по тексту таблицы, около 11 из них опираются на подменённые символы `-›`, `‹`, `›` (см. «Ограничения»), остальные — спекулятивные граничные случаи и придуманная проблема сборки мусора.

**Локальная `qwen/qwen3.5-9b`:** найдено 6 из 11 дефектов (bug 2 из 3, rules 3 из 4, arch 1 из 2, large 0 из 2). Пропущены B3, R3, A1, L1, L2. Ложных замечаний 12; около 7 из них — те же подменённые символы, остальные — выдуманные проблемы в копиях модулей (`chat.user_id`, `UnboundLocalError`) и в коде `session.add`.

Общее: оба пропустили L1; ни одно ревью PR `clean` не оказалось чистым (DeepSeek 5 ложных замечаний, локальная модель 3). PR `large` в обоих прогонах прошёл по файлам: в комментарии «Ревью выполнено по частям: 2» (путь по файлам подтверждён). Локальная модель на нём не нашла ни одного дефекта и выдала 7 ложных замечаний.

## Что дал поиск

Данные позволяют только осторожные выводы, потому что судья не видит источники, а в комментарии источники не привязаны к отдельным находкам. Набор источников для одного PR одинаков в обоих прогонах (поиск не зависит от модели), различается только то, что модель из них использовала.

- Дефекты, для которых нужна документация (R1-R4, A1, A2): DeepSeek нашёл 5 из 6, локальная модель 4 из 6 (таблица «Вклад поиска», строка «документация»).
- Дефект, для которого нужен код (B3, перепутанный порядок аргументов `_chunk_rows`): DeepSeek нашёл, локальная модель нет. В источниках PR `bug` у обоих прогонов есть `agent/kb_indexer.py:151-168` и соседние диапазоны; DeepSeek сослался на сигнатуру `_chunk_rows(kb_id, doc, drafts)` из `agent/kb_indexer.py`.
- Дефекты, видимые по одному diff (B1, B2, L1, L2): DeepSeek 3 из 4, локальная модель 2 из 4.
- Для PR `rules` в источниках нет `CLAUDE.md` (там записаны правила про `structlog`, `datetime.now(timezone.utc)`, откат сессии), а только `docs/ARCHITECTURE.md`, `docs/USER_GUIDE.md`, `docs/TESTING_GUIDE.md`, `README.md`. Правила обе модели всё равно назвали. Откуда они это взяли — из поиска или из общих знаний, по данным определить нельзя, поэтому утверждать вклад поиска здесь нельзя.
- Для PR `arch` в источниках есть разделы `CLAUDE.md` «Layers» и «Testing»; DeepSeek сослался на правило «`ui` не импортирует `agent`», что согласуется с найденным A1.
- Часть документационных источников — собственные разделы Day 32 (`Автоматическое ревью Pull Request`), они мало связаны с содержимым PR; это шум в выдаче.

## Тексты ревью

Ниже тексты ревью в том виде, в каком они сохранены в `eval_out/day32/<прогон>/<id>.md`. Единственное изменение: убраны HTML-маркер и заголовок «Ревью Pull Request», а заголовки разделов понижены на два уровня, чтобы они не нарушали структуру отчёта. Ничего не пропущено.

<details><summary>bug ([#2](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/2)), прогон `local`, статус `ok`</summary>

Коммит: `64a4ac4`
Модель: `LM Studio/qwen/qwen3.5-9b`

##### Потенциальные баги

`agent/review_demo_pagination.py:20` — в функции `slice_page` используется формула `end = start + page_size - 1`, что приводит к ошибке индекса при попытке получить элементы, если страница является последней и содержит меньше элементов, чем `page_size`. Правильная реализация должна использовать `min(start + page_size, len(items))` для вычисления конца среза.

##### Архитектурные проблемы

Замечаний нет.

##### Рекомендации

`agent/review_demo_pagination.py:28` — в функции `average_page_fill` условие `if len(items)` приводит к делению на ноль при пустом списке, так как выражение `len(items) / len(items)` всегда равно 1.0, но логика должна возвращать 0.0 для пустого списка. Исправьте условие на проверку `if not items: return 0.0`.

<details><summary>Источники</summary>

**Документация**

- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Что положить в базу документации
- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Раздел «Ревью Pull Request» в настройках
- `docs/ARCHITECTURE.md` — Architecture Guide > Automated PR review (Day 32) > Modules and tables
- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Что нужно заранее

**Код**

- `agent/kb_indexer.py:151-168`
- `agent/kb_indexer.py:192-223`
- `agent/kb_indexer.py:122-138`
- `agent/kb_loaders.py:110-120`
- `agent/kb_chunking.py:1-25`
- `agent/kb_chunking.py:308-311`

</details>

</details>

<details><summary>bug ([#2](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/2)), прогон `deepseek`, статус `ok`</summary>

Коммит: `530eef5`
Модель: `DeepSeek/deepseek-flash`

##### Потенциальные баги

- `agent/review_demo_pagination.py:17` — в `slice_page` вычисляется `end = start + page_size - 1`, а затем используется `items[start:end]`; правый конец среза в Python не включается, поэтому последний элемент страницы теряется (страница возвращает `page_size - 1` элементов). Нужно `end = start + page_size` или `items[start:start + page_size]`.
- `agent/review_demo_pagination.py:23` — в `average_page_fill` ветка `else len(items) / len(items)` при пустом списке даёт `0 / 0` и вызывает `ZeroDivisionError`; кроме того, при пустом списке `page_count` возвращает `0`, и деление `len(items) / (pages * page_size)` тоже упало бы. Нужно явно вернуть `0.0` при `not items`.
- `agent/review_demo_pagination.py:12` — `page_count` при `total_items == 0` возвращает `0`, а при отрицательном `page_size` или `page_size == 0` — деление на ноль; стоит валидировать `page_size > 0`.
- `agent/review_demo_pagination.py:28` — `rows_for_pages` вызывает `_chunk_rows(doc, kb_id, drafts)`, но в `agent/kb_indexer.py` сигнатура `_chunk_rows(kb_id, doc, drafts)`; аргументы перепутаны местами, что приведёт к ошибке/неверным данным. Нужно `_chunk_rows(kb_id, doc, drafts)`.

##### Архитектурные проблемы

- `agent/review_demo_pagination.py:3` — импорт приватной функции `_chunk_rows` из `agent/kb_indexer` создаёт связанность с внутренней реализацией другого модуля; стоит вынести построение строк чанков в публичный API или отдельный модуль.
- `agent/review_demo_pagination.py:28` — `rows_for_pages` не относится к постраничному выводу и дублирует логику индексатора; её место в модуле индексации, а не в хелперах пагинации.

##### Рекомендации

- `agent/review_demo_pagination.py:12` — добавить аннотации/докстринги с описанием поведения при `total_items == 0` и неположительном `page_size`.
- `agent/review_demo_pagination.py:17` — покрыть `slice_page` тестами на граничные случаи: последняя неполная страница, `page` за пределами диапазона, пустой список.
- `agent/review_demo_pagination.py:23` — уточнить в докстринге, что возвращается при пустом списке, и согласовать это с реализацией.

<details><summary>Источники</summary>

**Документация**

- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Что положить в базу документации
- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Раздел «Ревью Pull Request» в настройках
- `docs/ARCHITECTURE.md` — Architecture Guide > Automated PR review (Day 32) > Modules and tables
- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Что нужно заранее

**Код**

- `agent/kb_indexer.py:151-168`
- `agent/kb_indexer.py:192-223`
- `agent/kb_indexer.py:122-138`
- `agent/kb_loaders.py:110-120`
- `agent/kb_chunking.py:1-25`
- `agent/kb_chunking.py:308-311`

</details>

</details>

<details><summary>rules ([#3](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/3)), прогон `local`, статус `ok`</summary>

Коммит: `58eaf96`
Модель: `LM Studio/qwen/qwen3.5-9b`

##### Потенциальные баги

`agent/review_demo_audit.py:12`: использование `print()` для логирования изменений противоречит правилам проекта, указанным в документации (`structlog` вместо `print`); замените вызов `print` на запись через `structlog` с соответствующим контекстом.

`agent/review_demo_audit.py:13`: использование `datetime.utcnow().isoformat()` без указания часового пояса может привести к несоответствию времени в зависимости от окружения; используйте `datetime.now(timezone.utc).isoformat()` для гарантии UTC-времени, как рекомендовано в документации.

`agent/review_demo_audit.py:16`: вызов `session.add(chat)` избыточен, так как объект `chat` уже был изменён и находится в сессии; удалите эту строку, чтобы избежать лишних операций отслеживания изменений.

`agent/review_demo_audit.py:20`: функция `safe_title_length` использует голый блок `except:` без указания типа исключения, что скрывает потенциальные ошибки при чтении данных; замените на `except Exception:` для корректного перехвата ошибок.

##### Архитектурные проблемы

Замечаний нет.

##### Рекомендации

`agent/review_demo_audit.py`: добавьте проверку на пустую строку или `None` перед присвоением `chat.title = new_title`, чтобы избежать сохранения некорректных значений в базу данных.

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

</details>

<details><summary>rules ([#3](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/3)), прогон `deepseek`, статус `ok`</summary>

Коммит: `f399dd4`
Модель: `DeepSeek/deepseek-flash`

##### Потенциальные баги

- `agent/review_demo_audit.py:10` — в сигнатуре `record_title_change` использован символ `-›` (U+201A) вместо ASCII `->`; это синтаксическая ошибка, модуль не импортируется. Заменить на `->`.
- `agent/review_demo_audit.py:18` — в сигнатуре `safe_title_length` та же замена `-›` вместо `->`; исправить.
- `agent/review_demo_audit.py:11` — `print("title change requested", chat.id)` вместо структурного логирования; по правилам проекта (`structlog` вместо `print`) использовать `logger` из `structlog`.
- `agent/review_demo_audit.py:12` — `datetime.utcnow()` даёт naive-время и объявлен устаревшим; по правилам проекта использовать `datetime.now(timezone.utc)`.
- `agent/review_demo_audit.py:14-15` — `session.add(chat)` без `await session.commit()` (и без `rollback` при ошибке): изменение названия не сохранится, а возвращаемая строка журнала будет описывать несостоявшуюся запись. Добавить `commit` с обработкой исключения, как в `agent/titles.py:apply_title`.
- `agent/review_demo_audit.py:22-24` — `except:` без указания типа перехватывает в том числе `KeyboardInterrupt`/`SystemExit`; заменить на `except (AttributeError, TypeError):` или явную проверку `chat.title is None`.

##### Архитектурные проблемы

- `agent/review_demo_audit.py:10` — функция меняет `chat.title` напрямую, минуя существующий механизм `agent/titles.py:apply_title`, который пишет заголовок условно (`Chat.title == DEFAULT_CHAT_TITLE`) и коммитит. Стоит переиспользовать/расширить `apply_title`, чтобы не появилось второго пути записи заголовка с другой семантикой.

##### Рекомендации

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

</details>

<details><summary>arch ([#4](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/4)), прогон `local`, статус `ok`</summary>

Коммит: `2b694ed`
Модель: `LM Studio/qwen/qwen3.5-9b`

##### Потенциальные баги

`agent/review_demo_worker.py:13`: функция `_rebuild` объявлена как `async def`, но внутри нет ожидания асинхронных операций и не используется ключевое слово `await`; при запуске в отдельном процессе через `multiprocessing.Process` это приведёт к тому, что код завершится мгновенно без выполнения логики перестроения сводки.

##### Архитектурные проблемы

`agent/review_demo_worker.py:1`: использование модуля `multiprocessing` для запуска фоновых задач в асинхронном веб-сервере на FastAPI некорректно, так как каждый процесс получает собственный интерпретатор Python и отдельный цикл событий, что делает невозможным корректную работу с глобальными асинхронными объектами (сессии БД, WebSocket соединения) и приводит к утечкам памяти или ошибкам при попытке доступа к общему состоянию приложения.

##### Рекомендации

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

</details>

<details><summary>arch ([#4](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/4)), прогон `deepseek`, статус `ok`</summary>

Коммит: `cd3c65f`
Модель: `DeepSeek/deepseek-flash`

##### Потенциальные баги

- `agent/review_demo_worker.py:10` — в сигнатуре `_rebuild` и в `start_rebuild` (строка 15) использован символ `-›` (U+203A) вместо `->`; это синтаксическая ошибка, модуль не импортируется. Заменить на ASCII-стрелку `->`.
- `ui/review_demo_status.py:3` — `ui` импортирует `agent.state`, что нарушает правило проекта «`ui` не импортирует `agent`»; при этом `active_streams` и `chat_locks` живут в процессе агента, поэтому в UI-процессе значения будут всегда нулевыми. Нужно получать эти данные через API/события агента, а не прямым импортом.
- `agent/review_demo_worker.py:15` — `start_rebuild` возвращает `multiprocessing.Process`, но нигде не сохраняет ссылку и не вызывает `join`; процесс может быть собран сборщиком мусора до завершения. Стоит хранить ссылки на запущенные процессы (например, в модульном множестве) и предусмотреть ожидание/очистку.

##### Архитектурные проблемы

- `agent/review_demo_worker.py:10` — `_rebuild` только логирует и ничего не перестраивает, а `start_rebuild` запускает процесс без передачи сессии/БД и без обработки ошибок; фоновое перестроение сводки фактически не реализовано. Нужно либо реализовать логику перестроения, либо не добавлять воркер в этом PR.
- `ui/review_demo_status.py:6` — `collect_status` возвращает счётчики in-process структур агента, что не даёт корректного состояния при разделении процессов UI и агента; требуется источник данных, общий для обоих процессов.

##### Рекомендации

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

</details>

<details><summary>large ([#5](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/5)), прогон `local`, статус `ok`</summary>

Коммит: `6b7c241`
Модель: `LM Studio/qwen/qwen3.5-9b`

Ревью выполнено по частям: 2.

##### Потенциальные баги

`agent/review_demo_big/context_engine.py:150` — в функции `build_system_prompt` при чтении чата используется `chat.user_id`, но если `chat` равен `None`, обращение к атрибуту вызовет ошибку; нужно проверять `if chat is not None:` перед доступом к `user_id`.
`agent/review_demo_big/context_engine.py:150` — в том же месте после проверки на `None` переменная `owner_id` может остаться `None`, что корректно обрабатывается, но логика создания нового объекта `Settings` с `chat_id=None` и последующего добавления его в сессию без явной привязки к существующему чату может привести к несоответствию данных при последующих запросах.
`agent/review_demo_big/context_engine.py:385` — в функции `_apply_compression_strategy` используется оператор `›` вместо `›` для сравнения чисел, что является синтаксической ошибкой Python и приведёт к сбою выполнения кода.
`agent/review_demo_big/rag_turn.py:102` — в теле функции `load_history_pairs` используется оператор сравнения `‹` вместо `‹`, что приведет к логической ошибке; необходимо заменить на `‹`.
`agent/review_demo_big/rag_turn.py:291` — в теле блока `except RagFailure` переменная `top_k` может быть не определена, если функция вернулась ранее с `MODE_OFF`, что вызовет `UnboundLocalError`; необходимо инициализировать переменные до начала try-блока или обрабатывать ветку возврата.
`agent/review_demo_big/scheduler.py:105` — в теле метода `claim_slot` используется оператор сравнения `›` вместо `›`, что приведет к логической ошибке; необходимо заменить на `›`.
`agent/review_demo_big/scheduler.py:124` — в теле метода `claim_slot` используется оператор сравнения `›` вместо `›`, что приведет к логической ошибке; необходимо заменить на `›`.

##### Архитектурные проблемы

Замечаний нет.

##### Рекомендации

`agent/review_demo_big/rag_turn.py:134` — в сигнатуре функции `_history_context` отсутствует явное указание типа возвращаемого значения (используется символ `›`), что затрудняет статический анализ и автодополнение; рекомендуется явно указать тип `-› HistoryContext | None`.
`agent/review_demo_big/scheduler.py:105` — использование оператора сравнения `›` вместо `›` в условиях проверки лимитов (`run_count + 1 ›= task.max_runs`) может привести к тому, что задания не будут корректно ограничиваться по количеству запусков; рекомендуется проверить все подобные операторы на соответствие стандартным символам сравнения.
`agent/review_demo_big/scheduler.py:124` — аналогичная проблема с оператором `›` вместо `›` в условии `task.run_count + 1 ›= task.max_runs` может нарушить логику исчерпания лимитов запусков; рекомендуется исправить на стандартный оператор `›=`.

<details><summary>Источники</summary>

**Документация**

- `docs/ARCHITECTURE.md` — Architecture Guide > Automated PR review (Day 32) > Modules and tables
- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Что нужно заранее
- `CLAUDE.md` — Install dependencies > Component Responsibilities
- `README.md` — AiAdventAgentV2 > Directory structure
- `docs/TESTING_GUIDE.md` — Testing Guide > Citations and gate (Day 24)
- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Что вы получаете
- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Раздел «Ревью Pull Request» в настройках

**Код**

- `agent/context_engine.py:1-32`
- `agent/review_pipeline.py:1-58`
- `agent/review_pipeline.py:609-659`
- `agent/review_service.py:1-47`
- `tests/test_strategies.py:1-23`
- `tests/test_kb_add_api.py:1-50`
- `agent/rag_turn.py:1-49`
- `agent/rag_pipeline.py:1-52`
- `agent/rag_turn.py:291-317`
- `tests/test_rag_turn.py:1-23`
- `agent/rag_turn.py:184-233`
- `agent/task_memory.py:1-40`

</details>

</details>

<details><summary>large ([#5](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/5)), прогон `deepseek`, статус `ok`</summary>

Коммит: `045bcc1`
Модель: `DeepSeek/deepseek-flash`

Ревью выполнено по частям: 2.

##### Потенциальные баги

- `agent/review_demo_big/context_engine.py:1` — файл содержит невалидный Python: в сигнатурах и выражениях использованы символы `-›` и `‹`/`›` вместо корректных ASCII-операторов (например, `def _dict_texts(msg: dict[str, Any]) -› list[str]:`, `if len(text) ‹= limit:`). Модуль не импортируется; замените все вхождения на корректные ASCII-операторы.
- `agent/review_demo_big/rag_turn.py:1` — в файле используются символы `-›` и `‹` вместо корректных ASCII-операторов (например, в сигнатурах `def sources_json(self) -› str`, `if parent_id is None or limit ‹= 0`, `while current_id is not None and len(chain) ‹ steps`). Это синтаксические ошибки, модуль не импортируется; замените на ASCII-операторы.
- `agent/review_demo_big/scheduler.py:1` — аналогично: `def msg_timeout(seconds: float) -› str`, `if counts and task.max_runs is not None and task.run_count + 1 ›= task.max_runs`, `ScheduledTask.next_run_at ‹= now` и другие места используют `-›`, `‹`, `›`; код не соберётся.
- `agent/review_demo_big/scheduler_ops.py:1` — те же недопустимые символы: `def _validate_text_fields(...) -› tuple[...]`, `if len(title) › TITLE_MAX_LENGTH`, `if await _count_live_tasks(...) ›= limit`; файл не импортируется.
- `agent/review_demo_big/tools.py:1` — те же недопустимые символы: `def register_tool(...) -› Callable[[ToolHandler], ToolHandler]`, `def _wrap(fn: ToolHandler) -› ToolHandler`, `async def dispatch_tool_calls(...) -› list[dict[str, Any]]` и далее; модуль не компилируется.
- `agent/review_demo_big/context_engine.py:1` — `summarize_if_needed` объявлена как «Summarize old messages when approaching context limit (LLM calls only)», но фактически возвращает `history` без изменений, из-за чего сжатие по порогу `SUMMARY_TRIGGER_RATIO` никогда не выполняется. Либо реализуйте суммаризацию, либо уберите функцию и её вызов из `build_llm_context`.
- `agent/review_demo_big/context_engine.py:1` — в `_run_debounced_facts` при `ProviderUnavailableError` сообщение из `_pending_messages` уже извлечено через `pop`, но не восстановлено, поэтому факты из этого сообщения теряются безвозвратно. Сохраняйте сообщение обратно в `_pending_messages` или извлекайте его только после успешного `resolve_client`.
- `agent/review_demo_big/context_engine.py:1` — `extract_and_update_facts` создаёт задачу через `asyncio.create_task` и хранит её в `_debounce_tasks`, но при отмене предыдущей задачи её `CancelledError` внутри `_run_debounced_facts` глушится, а `finally` удаляет запись из `_debounce_tasks` по `chat_id`, что может удалить ссылку на уже новую задачу. Проверяйте, что удаляемая задача — текущая.
- `agent/review_demo_big/kb_api.py:1` — в `create_kb` при `UploadRejectedError` вызывается `await session.rollback()`, но `kb_id` уже присвоен и `remove_kb_dir` вызывается до коммита; при этом `session.flush()` мог не создать строку KB, а `remove_kb_dir` удаляет каталог по `user_id/kb_id`. Убедитесь, что каталог действительно создавался, иначе возможна попытка удаления чужого/несуществующего пути.
- `agent/review_demo_big/kb_api.py:1` — в `add_files_route` при `HTTPException` (например, `MSG_ADD_BUSY` после `_claim_kb_for_add`) выполняется `remove_tree(staging)`, но `claimed` уже `True` и маркер `PHASE_ADDING` не снимается, из-за чего база навсегда остаётся в состоянии «идёт добавление». Снимайте claim в этой ветке.
- `agent/review_demo_big/kb_indexer.py:1` — в `_commit_addition` `promote_index(pending, path)` вызывается до `await session.commit()`; если коммит упадёт, индекс уже подменён, а `pending` в блоке `except` удаляется (`pending.unlink`), что может оставить базу с новым индексом и старыми строками. Меняйте порядок: сначала commit, затем promote.
- `agent/review_demo_big/kb_indexer.py:1` — в `run_add_job` при `cancelled` и `_is_post_commit_failure(commit_task)` переменная `moved` обнуляется, но `_fail_add` всё равно вызывается с `MSG_ADD_INTERRUPTED`, помечая базу ошибкой, хотя данные уже закоммичены. Различайте пост-коммитный сбой и обычную отмену.
- `agent/review_demo_big/mcp_client.py:1` — в `open_session` при `TimeoutError` ожидания `handle.ready` задача отменяется, но `handle.errfile` не закрывается (в отличие от ветки `CancelledError`), что приводит к утечке файлового дескриптора. Закрывайте `errfile` и в этой ветке.
- `agent/review_demo_big/providers.py:1` — в `ensure_seeded` ранний выход проверяет `_seeded_users` и `_deepseek_pending`, но при отсутствии ключа DeepSeek повторный вызов всё равно берёт `lock` и заново выполняет запросы к БД; при этом `_seeded_users.add(user_id)` выполняется до фактической проверки, что может привести к пропуску сидирования при гонке. Проверяйте состояние под локом.
- `agent/review_demo_big/rag_pipeline.py:1` — в `run_retrieval_pipeline` есть `assert kb is not None` после `retrieve_vectors(session, kb, ...)`, хотя `kb` объявлен как `KnowledgeBase | None`; при `kb is None` будет `AssertionError` вместо понятной ошибки. Обрабатывайте `None` явно.
- `agent/review_demo_big/rag_pipeline.py:1` — в `_rewrite_stage` возвращается `round(float(np.dot(vectors[0][0], vector)), 4)`, где `vectors[0][0]` — вектор исходного запроса; при этом `vectors` может быть пустым, если `retrieve_vectors` не добавил исходный вектор. Проверьте инвариант или передавайте исходный вектор явно.
- `agent/review_demo_big/scheduler.py:1` — в `claim_slot` при `overlap=True` создаётся `TaskRun` со `status=SKIPPED`, но `run_count` не увеличивается (`+ (1 if counts else 0)`), при этом `next_run_at` уже сдвинут вперёд; это соответствует комментарию, но проверьте, что пропущенный слот не должен учитываться в `max_runs` — иначе задание с `max_runs` может выполняться дольше ожидаемого.
- `agent/review_demo_big/scheduler_ops.py:1` — в `delete_task` после `await session.delete(task)` и `commit` повторно вызывается `scheduler.abort_task_runs(task_id)`, но `task` уже удалён; это осознанно по комментарию, однако `hub.publish(user_id, task_deleted_frame(task_id))` использует `user_id` из аргумента, а не из `task.user_id` — проверьте, что они совпадают (в `get_owned_task` гарантируется).
- `agent/review_demo_big/tools.py:1` — в `dispatch_tool_calls` при `name not in TOOL_REGISTRY` формируется ошибка `unknown tool {name}`, но `name` может быть `None` (если `call.get("function", {}).get("name")` вернул `None`); тогда `f"unknown tool {name}"` даст `unknown tool None`, а `TOOL_SCHEMAS[name]` не вызовется из-за раннего `continue` — но сообщение будет неинформативным; рассмотрите отдельную ветку для отсутствующего имени.

##### Архитектурные проблемы

- `agent/review_demo_big/__init__.py:1` — пакет содержит полные копии модулей `agent/*` (`context_engine.py`, `kb_api.py`, `kb_indexer.py`, `llm_client.py`, `mcp_client.py`, `providers.py`, `rag_pipeline.py`), что создаёт дублирование кода и расхождение при дальнейших правках. Если цель — проверка размера ревью, не коммитьте копии в основную ветку; используйте отдельную ветку/артефакт.
- `agent/review_demo_big/context_engine.py:1` — модуль импортирует `from agent import invariants, memory, profile, task_memory, tasks` и `from agent.tool_guard import TOOL_TRACE_HEADER`, то есть копия зависит от оригинального пакета; при этом внутри пакета есть собственные копии `llm_client` и `providers`, но импорты идут на `agent.*`. Это делает копию несамостоятельной и вводит в заблуждение; либо импортируйте локальные копии, либо не дублируйте модули.
- `agent/review_demo_big/rag_turn.py:1`, `agent/review_demo_big/scheduler.py:1`, `agent/review_demo_big/scheduler_ops.py:1`, `agent/review_demo_big/tools.py:1` — модули импортируют из `agent.*` (например, `from agent import task_memory`, `from agent.scheduler import ...`, `from agent import memory, tasks`), то есть являются копиями, а не самостоятельным пакетом; при расхождении с оригиналами поведение разойдётся. Уточните в описании PR, что это демонстрационные копии, и добавьте тест или проверку, что копии синхронны с оригиналами, либо вынесите общий код в один модуль.
- `agent/review_demo_big/scheduler_ops.py:1` — модуль импортирует `scheduler` (синглтон) из `agent.scheduler`, а не из локального пакета; если цель — изолированный пакет, импорт должен указывать на локальную копию, иначе пакет не самодостаточен.
- `agent/review_demo_big/kb_indexer.py:1` — модуль использует глобальный `_semaphore` и `reset_state`, привязанные к event loop; при копировании в отдельный пакет это состояние не изолировано от оригинала, что может привести к неожиданному поведению при одновременной загрузке обоих модулей. Вынесите состояние в явный объект/класс.

##### Рекомендации

- `agent/review_demo_big/context_engine.py:1` — в `_message_tokens` и `_dict_tokens` используется `count_tokens(text) or 0`, что маскирует возможные ошибки; логируйте причину, если `count_tokens` вернул `None`.
- `agent/review_demo_big/kb_api.py:1` — в `_require_content_length_within_cap` при отсутствии заголовка возвращается 411, но для multipart-загрузок это может ломать клиентов, не отправляющих `Content-Length`; рассмотрите чтение тела с ограничением вместо жёсткого требования заголовка.
- `agent/review_demo_big/kb_indexer.py:1` — в `_embed_all` прогресс публикуется через `_update_kb`, который открывает новую сессию на каждый батч; при большом числе батчей это лишняя нагрузка. Рассмотрите переиспользование сессии или увеличение `EMBED_BATCH_SIZE`.
- `agent/review_demo_big/mcp_client.py:1

<details><summary>Источники</summary>

**Документация**

- `docs/ARCHITECTURE.md` — Architecture Guide > Automated PR review (Day 32) > Modules and tables
- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Что нужно заранее
- `CLAUDE.md` — Install dependencies > Component Responsibilities
- `README.md` — AiAdventAgentV2 > Directory structure
- `docs/TESTING_GUIDE.md` — Testing Guide > Citations and gate (Day 24)
- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Что вы получаете
- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Раздел «Ревью Pull Request» в настройках

**Код**

- `agent/context_engine.py:1-32`
- `agent/review_pipeline.py:1-58`
- `agent/review_pipeline.py:609-659`
- `agent/review_service.py:1-47`
- `tests/test_strategies.py:1-23`
- `tests/test_kb_add_api.py:1-50`
- `agent/rag_turn.py:1-49`
- `agent/rag_pipeline.py:1-52`
- `agent/rag_turn.py:291-317`
- `tests/test_rag_turn.py:1-23`
- `agent/rag_turn.py:184-233`
- `agent/task_memory.py:1-40`

</details>

</details>

<details><summary>clean ([#6](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/6)), прогон `local`, статус `ok`</summary>

Коммит: `4b596e0`
Модель: `LM Studio/qwen/qwen3.5-9b`

##### Потенциальные баги

`agent/review_demo_format.py:13` — в функции `format_size` используется оператор сравнения `›` вместо `>=`, что приводит к ошибке синтаксиса или логической ошибке (в зависимости от интерпретатора), так как для проверки условия перехода на следующую единицу измерения необходимо сравнивать значение на "больше или равно" 1024; исправьте оператор на `>=`.

`agent/review_demo_format.py:13` — в условии цикла используется символ `‹` вместо `<`, что является ошибкой ввода (вероятно, зеркальный символ) и вызовет ошибку синтаксиса при компиляции Python; замените символ на стандартный `<`.

`agent/review_demo_format.py:20` — аналогичная ошибка ввода с использованием символа `‹` вместо `<` в условии проверки отрицательной длительности; исправьте на `<`.

##### Архитектурные проблемы

Замечаний нет.

##### Рекомендации

`agent/review_demo_format.py:19` — при выводе размера файла в единицах выше байт (КБ, МБ и т.д.) используется формат `"{value:.1f}"`, что оставляет одну цифру после запятой; для большей точности и читаемости рекомендуется увеличить количество знаков после запятой до двух (`"{value:.2f}"`), чтобы избежать потери информации при округлении (например, 1024 байт станет ровно "1.0 КБ", а не "1.0 КБ" с возможными артефактами округления для больших чисел).

`agent/review_demo_format.py:26` — в функции `format_duration` остаток секунд от деления на 60 всегда выводится как целое число, хотя входной параметр имеет тип `float`; если ожидается высокая точность (например, миллисекунды), стоит рассмотреть вывод дробной части или использование формата времени ISO 8601 для больших интервалов, но в текущем контексте это допустимо.

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

</details>

<details><summary>clean ([#6](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/6)), прогон `deepseek`, статус `ok`</summary>

Коммит: `3d37e0a`
Модель: `DeepSeek/deepseek-flash`

##### Потенциальные баги

- `agent/review_demo_format.py:7` — в сигнатуре `def format_size(size_bytes: int) -› str:` и далее по файлу используется символ `-›` (U+203A) вместо ASCII `->`; это синтаксическая ошибка, модуль не импортируется. Заменить на `->` во всех сигнатурах и аннотациях.
- `agent/review_demo_format.py:9` — условие `if size_bytes ‹ 0:` использует символ `‹` (U+2039) вместо `<`; аналогично в строках с `while value ›= 1024 and unit ‹ len(_UNITS) - 1` и `if seconds ‹ 0:`. Заменить на `<` и `>=`.
- `tests/test_review_demo_format.py:8` — в сигнатурах тестов `def test_format_size_small() -› None:` и остальных используется `-›` вместо `->`; файл не соберётся. Заменить на ASCII-стрелку.
- `agent/review_demo_format.py:20` — `format_duration` усекает дробные секунды через `int(seconds)`, поэтому `format_duration(59.9)` вернёт `"59 с"`, а `format_duration(60.0)` — `"1 мин 0 с"`; поведение не описано и не покрыто тестами. Либо явно задокументировать усечение в docstring, либо округлять (`round`) и добавить тест на дробное значение.
- `agent/review_demo_format.py:14` — при `size_bytes`, равном `1024**4` и больше, цикл останавливается на `ГБ`, и значение выводится как `1024.0 ГБ`; для больших размеров стоит либо добавить `ТБ`, либо явно указать в docstring, что выше `ГБ` значение не масштабируется.

##### Архитектурные проблемы

Замечаний нет.

##### Рекомендации

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

</details>

<details><summary>smoke ([#1](https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2/pull/1)), прогон `smoke`, статус `ok`</summary>

Коммит: `6f670f0`
Модель: `LM Studio/qwen/qwen3.5-9b`

##### Потенциальные баги

`agent/review_demo_smoke.py:5`: в сигнатуре функции используется символ `›` вместо стандартного символа стрелки `->`, что является опечаткой и может вызвать ошибку синтаксиса при попытке импорта или выполнения файла.

##### Архитектурные проблемы

Замечаний нет.

##### Рекомендации

`agent/review_demo_smoke.py:1`: в строке документации используется символ `›` вместо стандартного символа стрелки `->`, что нарушает единообразие стиля кода проекта (сравните с `agent/rag_cite.py:426` или `scripts/review_judge.py:96`).

<details><summary>Источники</summary>

**Документация**

- `docs/ARCHITECTURE.md` — Architecture Guide > Automated PR review (Day 32) > Modules and tables
- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Что вы получаете
- `docs/USER_GUIDE.md` — AI Agent - User Guide > Автоматическое ревью Pull Request > Что положить в базу документации
- `README.md` — AiAdventAgentV2 > Directory structure

**Код**

- `agent/review_github.py:141-148`
- `agent/review_prompts.py:97-105`
- `agent/rag_rank.py:225-231`
- `agent/rag_cite.py:426-431`
- `scripts/review_judge.py:96-109`
- `scripts/help_eval.py:170-175`

</details>

</details>

## Что не запускалось и ограничения

Не запускалось или не подтверждено (не засчитывается как пройденное):

- Живая проверка MCP сервера GitHub (`24-CHECKS.md`, раздел 1): не запускалось, в сессии исполнителя не было `GITHUB_PERSONAL_ACCESS_TOKEN`. Названия полей ответов GitHub MCP (A1, A2, A7) не подтверждены отдельной проверкой; косвенно их использует сам реальный прогон (PR читались и ревью публиковались).
- Тест с настоящим бинарём в `tests/test_review_github.py`: не запускалось (пропущен без переменной `GITHUB_REVIEW_TEST_PR`).
- Проверка в браузере раздела настроек (`eval_out/day32/e2e_review_settings.md`): шаг 6a (успешное обновление индекса) не запускалось, LM Studio был недоступен на изолированной копии; остальные шаги, кроме 6a, пройдены.
- Длительность каждой задачи и итог `workflow_conclusion`: не записаны скриптом контрольного набора (`null`).
- Судья на несостоявшихся ревью: не потребовалось, все десять ревью получены и оценены.
- Несколько проходов судьи: описаны в разделе «Результаты»; итоговый проход один, все десять записей `ok`.

Ограничения:

- Один общий комментарий, без комментариев к строкам кода.
- Первое построение индекса кода для новой базовой ветки медленное.
- Судья — модель, а не человек; в прогоне DeepSeek судья и автор совпадают.
- Одобрение запусков для внешних участников не даёт полной защиты раннера (см. «Безопасность раннера»).
- Подмена угловых скобок: `agent/review_prompts.py` (строка 65) заменяет `<` и `>` на `‹` и `›` во всех текстах, попадающих в запрос, чтобы код PR не мог закрыть или открыть служебные теги. Модели поэтому видят в diff `-›` вместо `->` и `‹` вместо `<` и заявляют синтаксические ошибки там, где их нет. Это основная причина большей части ложных замечаний в таблице (в PR `clean` — всех замечаний локальной модели и большей части замечаний DeepSeek) и «опечатки» в ревью дымового PR. Исправление не входило в эту фазу.
- Расхождения с судьёй при выборочной проверке (`24-CHECKS.md`, 5.3): по восьми проверенным находкам расхождений нет; единственная оговорка — для A2 у DeepSeek `multiprocessing.Process` упомянут, но не как запрещённый механизм, и вердикт «не найден» принят. У локальной модели B2 засчитан судьёй как найденный, хотя ревью поместило его в раздел рекомендаций с неверным объяснением (`len/len` даёт 1.0).

**Что эта фаза не сделала.** Ветка `Day32` не слита в `main`: `Day31` (фаза 23) ещё не слита, а `Day32` построена на ней. Порядок слияния: сначала `Day31` в `main`, потом `Day32` в `main`; это оставлено пользователю. Пока этого нет, файл задачи `.github/workflows/pr-review.yml` существует только в `Day32` и её потомках, поэтому ревью запускается только для Pull Request в ветку `Day32`, а Pull Request в `main` не ревьюируются. После слияния файл задачи с раннером попадёт в основную ветку, и безопасная конфигурация из руководства пользователя должна остаться в силе.

Ветка `Day32` опубликована обычным `git push origin Day32` (в `24-CHECKS.md` решение `push Day32: yes`); в `main` ничего не отправлялось.

## Тесты

`python -m pytest tests/ -q`, запущено при составлении отчёта: 3549 прошли, 12 пропущены, 0 упали, 0 ошибок (693,9 с). Названия пропущенных тестов этот запуск не выводил (без `-rs`); известно, что среди них тест с настоящим бинарём `github-mcp-server` в `tests/test_review_github.py` (нужна переменная `GITHUB_REVIEW_TEST_PR`). Более ранний прогон перед отправкой ветки (`24-CHECKS.md`, 3.1) дал один сбой `tests/test_kb_api.py::test_create_happy_path`, зависящий от порядка тестов; в этом прогоне он не повторился. Тесты отчёта: `tests/test_review_report_day32.py`, 9 тестов.

## Вывод

На замороженном наборе из пяти PR облачная модель нашла 9 из 11 внесённых дефектов, локальная 6 из 11; ложных замечаний 15 и 12 соответственно, и ни одна модель не промолчала на чистом PR. Пайплайн от PR до комментария работает на реальном раннере и подтверждён дымовым PR, заменой комментария и десятью прогонами. Заметная доля ложных замечаний вызвана подменой угловых скобок в запросе, а не слабостью моделей. Вклад поиска надёжно подтверждён только косвенно (таблица «Вклад поиска»), потому что источники не привязаны к находкам. Судья в прогоне DeepSeek совпадает с автором ревью, поэтому оценка этого прогона может быть завышена.
