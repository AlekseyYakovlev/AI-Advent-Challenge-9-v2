### Найденные дефекты

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

### Итог по PR

| PR | Вид | deepseek: найдено | deepseek: ложных | deepseek: модель | local: найдено | local: ложных | local: модель |
|---|---|---|---|---|---|---|---|
| bug | obvious_bug | найдено 3 из 3 | ложных: 1 | DeepSeek/deepseek-flash | найдено 2 из 3 | ложных: 0 | LM Studio/qwen/qwen3.5-9b |
| rules | documented_rule | найдено 4 из 4 | ложных: 2 | DeepSeek/deepseek-flash | найдено 3 из 4 | ложных: 1 | LM Studio/qwen/qwen3.5-9b |
| arch | architecture | найдено 1 из 2 | ложных: 2 | DeepSeek/deepseek-flash | найдено 1 из 2 | ложных: 1 | LM Studio/qwen/qwen3.5-9b |
| large | large_pr | найдено 1 из 2 | ложных: 5 | DeepSeek/deepseek-flash | найдено 0 из 2 | ложных: 7 | LM Studio/qwen/qwen3.5-9b |
| clean | clean | найдено 0 из 0 | ложных: 5 | DeepSeek/deepseek-flash | найдено 0 из 0 | ложных: 3 | LM Studio/qwen/qwen3.5-9b |

### Вклад поиска

| Нужно знание | deepseek | local |
|---|---|---|
| только diff | 3 из 4 | 2 из 4 |
| документация | 5 из 6 | 4 из 6 |
| код | 1 из 1 | 0 из 1 |

### Ложные замечания

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
