<!-- ai-pr-review -->
## Ревью Pull Request

Коммит: `045bcc1`
Модель: `DeepSeek/deepseek-flash`

Ревью выполнено по частям: 2.

### Потенциальные баги

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

### Архитектурные проблемы

- `agent/review_demo_big/__init__.py:1` — пакет содержит полные копии модулей `agent/*` (`context_engine.py`, `kb_api.py`, `kb_indexer.py`, `llm_client.py`, `mcp_client.py`, `providers.py`, `rag_pipeline.py`), что создаёт дублирование кода и расхождение при дальнейших правках. Если цель — проверка размера ревью, не коммитьте копии в основную ветку; используйте отдельную ветку/артефакт.
- `agent/review_demo_big/context_engine.py:1` — модуль импортирует `from agent import invariants, memory, profile, task_memory, tasks` и `from agent.tool_guard import TOOL_TRACE_HEADER`, то есть копия зависит от оригинального пакета; при этом внутри пакета есть собственные копии `llm_client` и `providers`, но импорты идут на `agent.*`. Это делает копию несамостоятельной и вводит в заблуждение; либо импортируйте локальные копии, либо не дублируйте модули.
- `agent/review_demo_big/rag_turn.py:1`, `agent/review_demo_big/scheduler.py:1`, `agent/review_demo_big/scheduler_ops.py:1`, `agent/review_demo_big/tools.py:1` — модули импортируют из `agent.*` (например, `from agent import task_memory`, `from agent.scheduler import ...`, `from agent import memory, tasks`), то есть являются копиями, а не самостоятельным пакетом; при расхождении с оригиналами поведение разойдётся. Уточните в описании PR, что это демонстрационные копии, и добавьте тест или проверку, что копии синхронны с оригиналами, либо вынесите общий код в один модуль.
- `agent/review_demo_big/scheduler_ops.py:1` — модуль импортирует `scheduler` (синглтон) из `agent.scheduler`, а не из локального пакета; если цель — изолированный пакет, импорт должен указывать на локальную копию, иначе пакет не самодостаточен.
- `agent/review_demo_big/kb_indexer.py:1` — модуль использует глобальный `_semaphore` и `reset_state`, привязанные к event loop; при копировании в отдельный пакет это состояние не изолировано от оригинала, что может привести к неожиданному поведению при одновременной загрузке обоих модулей. Вынесите состояние в явный объект/класс.

### Рекомендации

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
