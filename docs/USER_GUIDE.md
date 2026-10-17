# AI Agent - User Guide

## Getting Started

1. Start the application: `python run.py`
2. Open browser to `http://127.0.0.1:8000`
3. Create a new chat or select existing one

## Using Chat

### Sending Messages
1. Type message in input field
2. Press Enter or click Send button
3. Wait for streaming response
4. Statistics update automatically

### Managing Chats
- **Create:** Click "Новый чат" button
- **Switch:** Click chat in sidebar
- **Delete:** Right-click chat in sidebar
- **Branch:** Click "↩ отсюда" button on any message
- **Title:** A new chat is named automatically after the first answer (short title in the language of the message); until then it is shown as "New Chat"

### Using Branches
- Messages can have multiple responses (branches)
- Use ◀ ▶ buttons to switch between branches
- Branching preserves full conversation history

## Settings

### Global vs Per-Chat Settings
- **Global:** Apply to all new chats
- **Per-Chat:** Override global settings for specific chat
- Toggle with "Настройки для текущего чата" checkbox

### Compression Strategies

**Sliding Window (Скользящее окно)**
- Keeps only recent messages
- Best for: Fast conversations
- Context: Last 10 messages

**Sticky Facts (Ключевые факты)**
- Summarizes old messages in English
- Extracts key facts from user messages
- Best for: Long consultations
- Context: Summary + recent messages

**Truncate Middle (Обрезка посередине)**
- Preserves first messages (instructions)
- Preserves recent messages
- Summarizes middle part
- Best for: Maintaining initial context

**No Compression (Без сжатия)**
- Sends all messages to LLM
- Blocks if context exceeds window
- Best for: Short, critical conversations
- Requires manual strategy change on overflow

### Context Length
- Maximum tokens sent to LLM
- Range: 512 - 131,072 tokens
- Default: 4,096 tokens
- Adjust based on model capacity

### Temperature
- Controls response randomness
- 0.0: Deterministic, focused
- 2.0: Creative, varied
- Default: 0.7

### Max Tokens
- Maximum response length
- Range: 256 - 128,000 tokens
- Default: 4,096 tokens

## Statistics

Statistics panel shows:
- **Запросы:** Total tokens in user messages
- **Ответы:** Total tokens in assistant messages
- **Текущий контекст:** Tokens sent to LLM
- **Использование:** Percentage of context window

Color indicators:
- 🟢 Green: 0-75% usage
- 🟡 Yellow: 75-90% usage (warning)
- 🔴 Red: 90-100% usage (critical)

## Memory

The memory panel is in the sidebar ("Память"); click the arrow to expand it. It has three layers:
"Кратковременная", "Рабочая (этот чат)" and "Долговременная (все чаты)".

- Long-term entries are created by the assistant and are shared by all your chats.
- Each long-term entry has "Редактировать" (change the key and the text in place, then "Сохранить" or "Отмена") and "Удалить" (asks for confirmation; cannot be undone).
- Two entries cannot have the same key.
- The assistant uses the changed memory starting with your next message.
- The assistant may save a deleted fact again if it comes up in conversation.
- Working memory is read-only.
- Another open browser tab shows the change after its next answer or chat switch.

## Context Overflow

When using "No Compression" and context exceeds window:
1. Red banner appears: "Контекст переполнен"
2. Input field disabled
3. Error message shown
4. Click "Изменить стратегию сжатия"
5. Select different strategy
6. Save settings
7. Input unlocked, conversation continues

## Model Management

### Local Models (LM Studio)
1. Start LM Studio application
2. Models appear in dropdown
3. Select model
4. If not loaded, confirm dialog appears
5. Click "OK" to load model
6. Wait for "Model loaded" notification

### Cloud Models (DeepSeek)
- Always available
- No loading required
- Select from dropdown

### Провайдеры LLM

Модели берутся у провайдеров, которые настраиваются в Settings, раздел «Провайдеры LLM». По умолчанию есть «LM Studio»; «DeepSeek» появляется, если в `.env` задан `DEEPSEEK_API_KEY`.

Как добавить провайдера:
1. Впишите ключ в файл `.env` отдельной строкой, например `OPENROUTER_API_KEY=...`, и перезапустите приложение.
2. В Settings нажмите «+ Добавить провайдера», введите название, Base URL (например `https://openrouter.ai/api`) и **имя** переменной с ключом (не сам ключ).
3. Нажмите «Сохранить провайдера» и прочитайте значок на карточке: после сохранения проверка запускается сама; «доступен» означает, что список моделей получен, «ошибка» показывает причину (неверный ключ, сервер недоступен, переменная не найдена). Кнопка «Проверить» повторяет проверку.

В списке выбора модели записи имеют вид «Провайдер · модель» и сгруппированы по провайдерам. Если провайдер отключён или удалён, его модели исчезают из списка, а выбор переключается на другую модель с уведомлением «Провайдер недоступен. Выбрана другая модель.». Сами чаты не удаляются.

## База знаний

Раздел «База знаний» в левой панели (сворачивается стрелкой) хранит ваши документы для семантического поиска. Базы видны только вам.

Как создать базу:
1. Нажмите «+ Новая база знаний», введите название и соберите список файлов (PDF, TXT, MD). Есть две кнопки: «Выбрать файлы» и «Выбрать папку». Их можно нажимать сколько угодно раз: выбранное добавляется в общий список, а не заменяет его. У каждой записи есть кнопка удаления, а «Очистить список» убирает всё. Над списком написано «В списке: N файлов». Для папки показывается отчёт: сколько файлов взято и сколько пропущено как неподдерживаемые. Файл из папки получает имя с относительным путём, например `docs/API_SPEC.md`, а отдельно выбранный файл — просто имя, например `README.md`. Имя может быть в списке только один раз; повторный выбор тех же файлов ничего не добавляет. Лимиты читаются из формы: до 200 файлов за одну загрузку, до 50 МБ на файл, до 100 МБ всего. Папка берётся целиком, включая скрытые и служебные каталоги, поэтому выбирайте `docs`, а не корень проекта. Если лимит превышен, заголовок списка краснеет, а кнопка отправки отключается.
2. Выберите стратегию разбиения. «Фиксированная длина» режет текст на куски заданного размера (100-2000 символов) с перекрытием (меньше размера и не больше его половины). «Структурная» делит по главам, статьям и заголовкам, а размер служит верхним пределом.
3. Выберите модель эмбеддингов. В списке только модели LM Studio типа embeddings (по умолчанию `text-embedding-nomic-embed-text-v1.5`). Галочка «показать все модели» показывает и остальные, но обычные языковые модели, например `giga-embeddings-instruct-480m-0826`, не поддерживают эмбеддинги в LM Studio, и проверка вернёт ошибку. Кнопка «Проверить эмбеддинг» показывает размерность вектора.
4. Нажмите «Индексировать». Ошибки проверки показываются под формой, окно остаётся открытым.

Прогресс виден на карточке: «в очереди», «загрузка модели…», «разбор файлов…», «индексация N из M», затем «готово» с числом файлов и чанков. Если индексация не удалась, статус «ошибка»; нажмите на текст ошибки, чтобы развернуть его. PDF-скан без текстового слоя не поддерживается. Если приложение перезапущено во время индексации, такая база получает ошибку, её нужно удалить и создать заново.

«Тест поиска» (доступен для готовой базы) принимает вопрос и показывает 5 ближайших фрагментов: оценка сходства, файл, раздел и идентификатор чанка; «показать полностью» раскрывает текст.

### Как добавить файлы в готовую базу

У готовой базы есть кнопка «Добавить файлы». Она открывает окно с теми же кнопками «Выбрать файлы» и «Выбрать папку» и тем же списком; настройки (стратегия, размер чанка, модель) берутся из самой базы. Пока идёт добавление, на карточке видна строка «Добавление: N из M», статус остаётся «готово», а «Тест поиска» продолжает работать по уже загруженным документам. В конце показывается сводка «Добавлено: N, заменено: M, без изменений: K». Файл с тем же именем и другим содержимым заменяет прежний: его старые фрагменты исчезают из поиска. Файл с тем же именем и тем же содержимым пропускается. Файлы, которых нет в новом выборе, остаются в базе. Если добавление не удалось, под карточкой появится строка с причиной, а база останется такой, какой была.

Правило имён: файл, загруженный через папку, называется по относительному пути, поэтому обновить такой документ можно, только выбрав папку заново. Если выбрать один изменившийся файл, он добавится как отдельный источник с простым именем.

Пример для показа: выберите `README.md`, затем папку `docs`, создайте базу. Позже измените любой документ в `docs`, нажмите «Добавить файлы», выберите папку `docs` ещё раз и отправьте: в сводке будет «заменено: 1».

«Удалить» просит подтверждение («Точно удалить?»); база удаляется вместе с файлами, даже если идёт индексация.

## Ответы с базой знаний (RAG)

Готовую базу знаний можно подключить к чату, чтобы модель отвечала по фрагментам ваших документов.

1. В заголовке чата выберите базу в списке «— без базы знаний —» (в списке только готовые базы).
2. Переключатель рядом показывает «без RAG»; нажмите его, он станет «с RAG». Рядом появится поле K: сколько фрагментов передавать модели (1-20, по умолчанию 5). Значок слева показывает «RAG: название базы». Настройка сохраняется для чата и переживает перезагрузку страницы.
3. Задайте вопрос. Под ответом написано «с RAG · K=5» или «без RAG», а ниже есть свёрнутый блок «Источники (N)»: раскройте его, чтобы увидеть оценку, файл, раздел и текст каждого фрагмента («показать полностью» раскрывает длинный текст). Сам вопрос в истории хранится без вставленных фрагментов.
4. Переключайтесь между «с RAG» и «без RAG» в одном чате, чтобы сравнить ответы: режим каждого ответа виден в истории.

Жёлтая строка «⚠ ...» под ответом означает, что поиск по базе не сработал и ответ дан без неё (одновременно появляется уведомление): база удалена или ещё не готова, не загружена модель эмбеддинга, не совпадает размерность индекса, индекс повреждён, контекст заполнен или поиск завершился ошибкой. Ответ при этом всё равно приходит. Если база удалена, выберите другую в заголовке чата. Надпись «Подходящих фрагментов не найдено» значит, что в базе нет близких фрагментов.

### Настройки поиска (Поиск ⚙)

Когда переключатель стоит в положении «с RAG», рядом с полем K появляется кнопка «Поиск ⚙». Она открывает панель «Настройки поиска» (Esc или щелчок вне панели закрывают её). Если включён хотя бы один переключатель, кнопка подсвечена. Изменения действуют со следующего сообщения и сохраняются для чата.

- «Кандидатов» (1-50, по умолчанию 20): сколько фрагментов поиск берёт до отсева и реранка. Не меньше K.
- «Порог» (0-1): минимальное косинусное сходство фрагмента с вопросом. Подпись «(калибр.)» значит, что используется значение, подобранное для модели эмбеддингов базы. «(нет калибровки)» значит, что для этой модели порог не подбирался и отсева нет (0.00). Своё значение можно ввести вручную, тогда появится «сбросить», который возвращает калибровку.
- «Лексич. реранк»: переупорядочивает фрагменты с учётом совпадения слов и номеров статей.
- «LLM-реранк»: модель оценивает 10 лучших фрагментов (один дополнительный вызов модели).
- «Гибрид (FTS5)»: добавляет поиск по ключевым словам и объединяет его с векторным; фрагмент с низким сходством остаётся, только если совпал номер статьи или слова вопроса.
- «Переписывание запроса»: модель переформулирует вопрос перед поиском (один дополнительный вызов модели).
- «Ходов истории» (0-10, по умолчанию 3): сколько последних пар «вопрос-ответ» видит поиск, когда превращает короткое уточнение («а за повторное?») в самостоятельный запрос. 0 означает, что поиск опирается только на память задачи. При включённом RAG в чате такое уточнение запроса делается автоматически (один дополнительный вызов модели), если в чате уже есть история или память задачи.

Если дополнительный этап не сработал (таймаут, некорректный ответ модели), поиск продолжается без него, а причина видна в «Детали поиска».

Под ответом есть свёрнутый блок «Детали поиска». В нём строки «Запрос:» (и «Переписан:», если запрос был переписан; если запрос был уточнён по истории диалога, строка называется «Уточнён:», а в «Этапах» появляется «история ×N»), «Этапы:» (порог, включённые этапы, число кандидатов → в ответе, время) и «Пропущено:» (строки «↷ …» для пропущенных этапов), а ниже таблица кандидатов: «было→стало» (место до и после реранка), «cos», при включённых этапах «lex», «FTS», «LLM», «Запрос», затем «Источник» и «Статус». Статус показывает метка: «✓ в ответе», «ниже порога», «вне top-K», «не вошло в бюджет», а «≈ прошло по FTS» отмечает фрагмент, найденный по ключевым словам. Текста фрагментов в таблице нет, он остаётся в блоке «Источники».

Серая строка «Фрагменты не прошли порог (лучший X < Y)» значит, что ни один фрагмент не достиг порога, поэтому модель ответила без фрагментов и блока «Источники» нет. Снизьте порог или переформулируйте вопрос.

### Строгий режим: цитаты и «не знаю»

Первый переключатель в «Поиск ⚙» называется «Строгий режим». Он включён для нового чата с RAG и для всех существующих RAG-чатов после обновления. Строгий режим требует от модели отвечать только по найденным фрагментам, ссылаться на них и приводить цитаты; код проверяет цитаты по тексту фрагментов и сам отказывается отвечать, если подходящих фрагментов нет. Кнопка «Поиск ⚙» не подсвечивается из-за одного этого переключателя. Если выключить строгий режим, со следующего сообщения чат отвечает как раньше (без проверки цитат и без отказа кодом).

Под ответом в строгом режиме раскрыт блок «Цитаты (N)». У каждой цитаты есть метка:

- «✓ подтверждена»: цитата дословно найдена в указанном фрагменте.
- «≈ почти дословно»: найдена с мелкими отличиями (сходство не ниже 0.9).
- «✗ не подтверждена»: такой цитаты нет ни в одном фрагменте, отправленном модели; «(нет такого источника)» значит, что указан несуществующий номер фрагмента.
- «подобрана автоматически»: модель не дала подтверждённых цитат, и код выбрал предложение из фрагмента по совпадению слов.
- «источник исправлен»: цитата нашлась не в том фрагменте, который назвала модель, а в другом.

В блоке «Источники» метка «цитируется» стоит у фрагментов, на которые ссылается ответ; они показаны первыми. Блоки «Источники» и «Детали поиска» свёрнуты.

Серая строка под ответом может сообщать, что модель ответила «не знаю» (во фрагментах нет ответа) или что в ответе есть ссылки на несуществующие источники. Янтарная строка «⚠ ответ не подтверждён фрагментами» значит, что в ответе нет ссылки [N] и ни одной подтверждённой цитаты: проверьте ответ по источникам.

Есть два исхода «не знаю»:

- Отказ кодом: ни один фрагмент не прошёл порог. Ответ начинается с «Не знаю:» и содержит уточняющий вопрос с ближайшими разделами; модель при этом не вызывается, поэтому ответ приходит мгновенно. Под ответом серая строка «Фрагменты не прошли порог (лучший X < Y)», блоков «Цитаты» и «Источники» нет.
- Ответ модели «Не знаю…»: фрагменты нашлись, но модель решила, что ответа в них нет.

Что делать при отказе: снизить порог в «Поиск ⚙», переформулировать вопрос или выключить строгий режим.

Пока ответ печатается, в конце текста виден раздел «Цитаты:» в виде обычного текста; когда ответ завершён, он заменяется блоком «Цитаты (N)», а в самом сообщении этого хвоста нет.

Измеренное поведение описано в [Day24_report.md](../Day24_report.md). В частности, при калиброванном пороге часть вопросов, на которые в базе есть ответ, всё же получает отказ.

Для отчёта Day 22 есть скрипт сравнения: `python scripts/rag_eval.py build-kbs` индексирует документы отдельной тестовой базой (не `app.db`) для каждой модели эмбеддинга, затем `python scripts/rag_eval.py run --kb LABEL=ID` отвечает на контрольные вопросы без RAG и с RAG и сохраняет таблицы результатов.

## Память задачи

В чате с включённым RAG в панели «Память» (в боковой панели) появляется блок «Память задачи». В чате без RAG его нет. Блок показывает три части: «Цель» (о чём этот диалог), «Уточнено» (что вы уточнили по ходу разговора) и «Ограничения и термины» (что вы потребовали соблюдать и о каких значениях договорились). Пока ничего не сохранено, видно «Пока пусто».

- Память обновляется после каждого ответа: модель выделяет из вашего сообщения цель, уточнения и ограничения, а код оставляет только то, что вы сами сказали. Факты, которые назвала только модель, в память не попадают. Блок обновляется сразу, без перезагрузки страницы.
- Память передаётся модели в каждом следующем запросе и помогает поиску понять короткие уточнения.
- Под каждым ответом есть свёрнутый блок «Память задачи (N)»: это состояние памяти после этого ответа. Метка «новое» показывает, что добавил именно этот ответ. Если обновить память не удалось, в блоке будет метка «память не обновлена», а прежнее состояние сохранится.
- Цель можно изменить: «Редактировать», затем «Сохранить» (Esc или «Отмена» возвращают прежний текст). Пункт можно убрать кнопкой «×» (без подтверждения). «Сбросить» очищает всю память задачи после подтверждения «Сбросить память задачи этого чата?»; отменить это действие нельзя.
- Память следует за активной веткой. Если вы переключаете ветку или нажимаете «↩ отсюда», блок показывает память, сохранённую в последнем ответе этой ветки. Правки, внесённые вручную после последнего ответа, при переключении ветки пропадают.
- Количество пар «вопрос-ответ», которые поиск использует для уточнения запроса, задаётся полем «Ходов истории» в «Поиск ⚙» (0-10, 0 означает только память задачи).

Измеренное поведение описано в [Day25_report.md](../Day25_report.md).

## LAN mode

### What it does
One setting, `BIND_HOST`, opens both the UI (port 8000) and the Agent (port 8001) to every machine on the local network. The default is this computer only. There are no IP lists, no HTTPS and no binding to a single LAN address.

### Enable
1. Set `BIND_HOST=0.0.0.0` in `.env`.
2. Restart with `python run.py`.
3. Read the warning block and the address list printed at startup: a framed block starting with `WARNING: LAN mode is ON (BIND_HOST=0.0.0.0)`, followed by `Starting UI on:` and one URL per reachable address.
4. Open one of the printed addresses in a browser on another machine and log in with a normal account.

The same list is shown in Settings -> «Сеть»; a click copies the link. Both ports must be reachable, because the page served on 8000 talks to the Agent on 8001.

### Accepted values

| `BIND_HOST` | Meaning |
|-------------|---------|
| `127.0.0.1` | This computer only (default) |
| `localhost` | Synonym of `127.0.0.1` |
| empty or unset | Same as `127.0.0.1` |
| `0.0.0.0` | Every machine on the local network |

Any other value, including a specific LAN IP or `::`, stops the app at startup with exit code 2 and an explanation. The value is shared by both processes; health checks always use `127.0.0.1`.

### Windows Firewall
The app never changes the firewall. Create the rule yourself, in PowerShell started as administrator (TCP 8000 and 8001, Private networks only):

```powershell
New-NetFirewallRule -DisplayName "AiAdventAgentV2 LAN" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000,8001 -Profile Private
```

Or in the GUI:
1. Open Windows Defender Firewall with Advanced Security -> Inbound Rules -> New Rule.
2. Choose Port -> TCP -> Specific local ports: `8000,8001`.
3. Choose "Allow the connection".
4. Tick only Private.
5. Name it `AiAdventAgentV2 LAN`.

To remove the rule:

```powershell
Remove-NetFirewallRule -DisplayName "AiAdventAgentV2 LAN"
```

The rule applies only while the network is Private. Check the category with `Get-NetConnectionProfile` (field `NetworkCategory`). If the home Wi-Fi shows Public, change it:

```powershell
Set-NetConnectionProfile -InterfaceAlias "Wi-Fi" -NetworkCategory Private
```

or use Settings -> Network & Internet -> the connection's properties -> Private network.

The first time Python listens on the network, Windows may show a "Windows Security Alert" prompt. Tick only the Private box there.

### Security notes
- Everyone on the network can reach the login page.
- Traffic is plain HTTP and can be read by others on the same network.
- Do not enable LAN mode on public or shared networks (cafe, hotel, office guest Wi-Fi).
- Requests from other web sites are still rejected by the Origin check.
- `/health` is open without login. `/debug/routes` needs a login or an API key.
- Wrong passwords are limited by a lockout, see "Limits".

### Disable
Remove `BIND_HOST` from `.env` or set `BIND_HOST=127.0.0.1`, restart, and remove the firewall rule.

## API keys and HTTP access

An API key lets another program (a script, a notebook, another machine) use the app without the browser login.

### Create a key
1. Open Settings and find the section «API ключи».
2. Press «+ Создать ключ», enter a name (up to 100 characters) and confirm.
3. The window «Ключ создан» shows the key once, with a ready curl example. Copy the key now: it cannot be shown again. A lost key is replaced by a new one.

You can have at most 20 keys. Each row shows the name, the beginning of the key and when it was last used.

### Revoke a key
Press «Отозвать» in the key row and confirm. The key stops working on the next request.

### Send a message
```bash
curl -s -X POST http://127.0.0.1:8001/api/v1/chats/<chat_id>/messages \
  -H "Authorization: Bearer <your key>" -H "Content-Type: application/json" \
  -d '{"content": "Привет", "model": "<model>"}'
```
- `<chat_id>` is the `id` from `GET /api/v1/chats` (create a chat with `POST /api/v1/chats`).
- `<model>` is the model id as shown in the model selector; the model must already be loaded, the endpoint does not load models.
- Use the Agent port (`AGENT_PORT`, 8001 by default), not the UI port. From another machine the app must run in LAN mode.
- The reply is one JSON body with the answer text (`content`), token statistics and any tool calls, memory writes and RAG information of that turn.
- The exchange appears in the same chat in the open browser tab without a reload.
- To use a knowledge base add `"kb_id": <id>` (ids come from `GET /api/v1/kb`); `"kb_id": null` turns RAG off; leaving it out keeps the chat's saved choice.

### Errors
- `401`: the key is wrong or revoked.
- `429`: the chat's message limit is exceeded; wait the number of seconds in the `Retry-After` header and send again.
- `409`: the chat is busy with another message; try again when it finishes.
- `413`: the context does not fit the model window; switch the chat to a compression strategy such as `sliding`.
- `503`: LM Studio or the provider is not available.

The full scenario with all routes is in [API_SPEC.md](API_SPEC.md), section "HTTP chat (Day 30)".

### Security
Anyone holding the key acts as you on the REST API. The connection is plain HTTP, so use keys only on a trusted network. Keys cannot create other keys, add users or log out; those actions work only from the web interface.

## Limits

Four settings in `.env` limit how fast the service is used:

| Setting | Default | Meaning |
|---------|---------|---------|
| `RATE_LIMIT_MESSAGES_PER_MINUTE` | 30 | Messages per chat per minute, counted together for the browser and the HTTP endpoint. `0` = off |
| `LOGIN_MAX_FAILED_ATTEMPTS` | 5 | Wrong passwords per username and client machine before login is blocked. `0` = off |
| `LOGIN_LOCKOUT_SECONDS` | 300 | How long a login block lasts (at least 1) |
| `MAX_CONCURRENT_GENERATIONS` | 2 | Chat turns generating at the same time, all models together; the rest wait in line. `0` = no cap |

To change a value, edit `.env` and restart `python run.py`. A restart also clears every login block, which is how you unblock someone. A negative or non-numeric value stops the start with a message.

Wrong passwords are counted until a successful login, however far apart they are, so mistypes on different days add up. After a block has ended the count starts again from zero. The lockout slows casual guessing but does not stop a determined attacker who is already on your home network.

What you see:
- A message over the per-chat limit: a toast «Слишком много сообщений. Повторите через N с»; the text returns to the input field.
- A message waiting for a free generation slot: the caption «В очереди, ожидание свободного места…», then the reply.
- A blocked login: «Слишком много неудачных попыток. Повторите через N мин».

The values are not shown in the app. They are visible only in `.env` and in the Agent log line `limits_in_force` written at start.

## Checking the service from another machine

`scripts/verify_service.py` checks a running service the way an outside client would: it tests that the service is reachable and the key works, sends several requests at once to a real model and reports their timings and queue waits, shows the message limit answering 429 and the context-overflow answer. It is a single file that needs only Python 3.11 or newer. Nothing has to be installed and the repository does not have to be copied.

Steps:
1. On the host, turn on LAN mode and create the firewall rule (see "LAN mode"), load a model in LM Studio and create an API key in Settings (see "API keys and HTTP access").
2. For the demonstration set a low limit in `.env`, for example `RATE_LIMIT_MESSAGES_PER_MINUTE=5`, and restart. Put the normal value back afterwards.
3. Copy `scripts/verify_service.py` to the other machine.
4. Set the key in the environment variable `AIA_API_KEY` (or pass `--key`) and run:

```bash
export AIA_API_KEY="<your key>"
python3 verify_service.py --url http://<lan-ip>:8001 --n 6
```

Flags:
- `--url`: the Agent address (port 8001), required.
- `--key`: the API key, instead of `AIA_API_KEY`.
- `--n`: number of parallel requests (default 6).
- `--model` and `--provider-id`: choose the model and provider; given together, otherwise the script picks one.
- `--timeout`: seconds allowed per request.
- `--rate-mode chat|fast|both`: how the message limit is shown. `chat` sends real turns until the 429. `fast` uses a tiny chat that overflows its context so the replies come back at once; use it with a slow model that cannot finish enough turns inside one minute. `both` runs both.
- `--rate-max-attempts`: ceiling of requests in one rate sequence.
- `--server-rate-limit` and `--server-max-concurrent`: the limits set on the host, written to the report for information only.
- `--out`: output directory (default `verify_out_<time>`).
- `--keep`: keep the chats the script created (they are deleted by default).

Output: the console, `verify_results.md` (tables) and `verify_results.json` (raw records) in the output directory. The key is never written to them. Exit codes: `0` everything behaved as expected, `1` an expectation was not met, `2` the check could not start (bad arguments, server unreachable, key refused, no usable model).

## MCP серверы

### Форма «MCP серверы»

Форма находится в окне Settings, раздел «MCP серверы» (кнопка «+ Добавить сервер»). Поля: «Название», «Команда», «Аргументы (по одному в строке)», «Переменные окружения (KEY=VALUE, по одной в строке)», «Рабочая директория (необязательно)» и флажок «Включён». Включённые серверы подключаются сами при первом сообщении в чате. Вызов инструмента показывается в чате карточкой с именем `mcp__<название>__<инструмент>`.

### Сервер git проекта (local-git)

Это собственный сервер приложения только для чтения: `mcp_servers/project_git.py`. Он читает ЛОКАЛЬНУЮ рабочую копию на этом компьютере, а не GitHub.

| Поле | Значение |
|------|----------|
| Название | `local-git` |
| Команда | `python` |
| Аргументы | строка 1: `C:\Projects\AiAdventAgentV2\mcp_servers\project_git.py`; строка 2: `--root=C:\Projects\AiAdventAgentV2`; необязательно: `--max-chars=18000` и `--timeout=10` (каждый аргумент в своей строке) |
| Переменные окружения | пусто |
| Рабочая директория | `C:\Projects\AiAdventAgentV2` |

Пути даны как пример для этого компьютера: замените их путём к вашему репозиторию. Путь к скрипту лучше писать абсолютным: относительный отсчитывается от рабочей директории. Без `--root` корнем считается рабочая директория.

| Инструмент | Параметры | Что делает |
|------------|-----------|------------|
| `git_current_branch` | нет | Текущая ветка (или отсоединённый HEAD) |
| `git_status` | нет | Изменённые и неотслеживаемые файлы рабочей копии |
| `git_diff` | `staged`, `ref`, `path` | Изменения; `ref` — `HEAD`, ветка, тег, хеш или диапазон вроде `main...HEAD` |
| `git_tracked_files` | `path` | Список отслеживаемых файлов (для большого проекта — таблица по папкам) |
| `git_recent_commits` | `count` (1-50, по умолчанию 10), `path`, `ref` | Последние коммиты |
| `read_file` | `path`, `start_line`, `end_line` | Текст файла с номерами строк (по умолчанию 300 строк, дальше — с `start_line`) |
| `search_text` | `pattern`, `regex`, `glob`, `ignore_case` | Поиск подстроки или регулярного выражения по файлам проекта, ответ `путь:строка: текст` |
| `find_usages` | `symbol`, `path` | Определения, импорты, вызовы и ссылки на имя Python (через `ast`, без совпадений в комментариях и строках) |

Читаются только файлы, которые видит git: отслеживаемые и новые неигнорируемые; `.env`, `app.db` и другие игнорируемые файлы недоступны.

Лимиты: результат обрезается на `--max-chars` (по умолчанию 18000, меньше лимита приложения 20000) с пометкой, сколько показано, и советом сузить запрос параметром `path`. Каждая команда git ограничена `--timeout` (по умолчанию 10 секунд). Путь вне репозитория или аргумент, начинающийся с `-`, отклоняется ошибкой с подсказкой, как исправить вызов.

Примеры вопросов: «на какой ветке проект?», «что изменено в рабочей копии?», «покажи последние 5 коммитов в `agent/`».

### Сервер GitHub (github)

Официальный `github-mcp-server`, версия `v2.0.2` (с ней проводилась проверка).

**Установка.** Скачайте `github-mcp-server_Windows_x86_64.zip` со страницы `https://github.com/github/github-mcp-server/releases/tag/v2.0.2`. Сравните SHA-256 архива с файлом `github-mcp-server_2.0.2_checksums.txt` из того же релиза (для v2.0.2 ожидается `4ae64087501650752f21c9d5af5378107a5116d94ee1cf37786b22b8c4bd99df`):

```powershell
Get-FileHash .\github-mcp-server_Windows_x86_64.zip -Algorithm SHA256
```

Затем скопируйте `github-mcp-server.exe` в `C:\Users\Aleksey\go\bin` рядом с `filesystem.exe`.

**Токен.** GitHub -> Settings -> Developer settings -> Fine-grained tokens. Repository access: «Only select repositories» = `AlekseyYakovlev/AI-Advent-Challenge-9-v2`. Права: Metadata: Read, Contents: Read and write, Pull requests: Read and write (запись — запас для будущих дней с Pull Request). Токен создаёте вы сами и вводите только в форму; в файлы репозитория его класть нельзя.

| Поле | Значение |
|------|----------|
| Название | `github` |
| Команда | `C:\Users\Aleksey\go\bin\github-mcp-server.exe` |
| Аргументы | три строки: `stdio`, `--read-only`, `--toolsets=repos,pull_requests` |
| Переменные окружения | `GITHUB_PERSONAL_ACCESS_TOKEN=<ваш токен>` |
| Рабочая директория | пусто |

В этой конфигурации сервер доступен только для чтения ТОЛЬКО благодаря флагу `--read-only`: у самого токена есть права на запись, поэтому если убрать флаг, станут доступны инструменты записи.

**Маскирование.** После сохранения форма показывает `GITHUB_PERSONAL_ACCESS_TOKEN=•••`. Значение никогда не возвращается в браузер; повторное сохранение формы с маской оставляет сохранённое значение.

Инструменты (по списку v2.0.2): `get_commit`, `get_file_contents`, `get_latest_release`, `get_release_by_tag`, `get_tag`, `list_branches`, `list_commits`, `list_pull_requests`, `list_releases`, `list_repository_collaborators`, `list_tags`, `pull_request_read`, `search_code`, `search_commits`, `search_pull_requests`, `search_repositories`.

Кого спрашивать: `local-git` отвечает про рабочую копию на этом компьютере, `github` — про удалённый репозиторий на GitHub.

### Если сервер не подключается

- Папка не является репозиторием git: сервер сообщает, что нужно выполнить `git init`; сам репозиторий он никогда не создаёт. Ошибка приходит как `PROCESS_EXITED` с последней строкой stderr.
- `git` не найден в PATH: установите Git for Windows и перезапустите приложение.
- Корневая папка не существует: исправьте `--root`.
- Команда git превысила время: сузьте запрос параметром `path` или увеличьте `--timeout`.
- Неверный токен GitHub: сервер подключается и показывает инструменты, но вызовы возвращают `401 Bad credentials`; создайте токен заново и сохраните его в форме.

## Команда /help

Команда `/help <вопрос>` отвечает на вопрос о проекте по его документации и по живому состоянию git. Примеры: `/help как запустить приложение?`, `/help на какой ветке проект?`.

### Настройка

1. Создайте базу знаний из `README.md` и папки `docs/` (см. раздел «База знаний») и дождитесь статуса «готово».
2. Добавьте MCP сервер `local-git` (см. раздел «MCP серверы»). Он только читает репозиторий и по умолчанию используется для `/help`.
3. Откройте «Настройки» → «Справка по проекту (/help)» и выберите базу в поле «База документации». Выбор сохраняется сам. «Сбросить выбор» убирает отметку.

В том же разделе: «Top-K», «Кандидатов», «Порог» (кнопка «Сбросить» возвращает значение, рассчитанное под модель эмбеддингов), четыре стадии поиска и список инструментов. Список «MCP серверы» выбирает серверы целиком; по умолчанию включён только `local-git`. Встроенные инструменты «Память», «Задачи» и «Планировщик» по умолчанию выключены.

Включённые встроенные инструменты («Память», «Задачи», «Планировщик») и любой MCP сервер с правом записи позволяют ответу `/help` изменять данные. Включайте их только осознанно.

### Что видно в чате

- Над ответом метка «Справка по проекту · <имя базы>»; под ответом «Источники» и «Цитаты» из документации.
- Вызовы инструментов показываются карточками. После перезагрузки страницы карточки не показываются снова, а метка, источники и цитаты остаются.
- Если в документации ничего не найдено, виден текст «В документации ничего не найдено.»; ответ тогда строится по инструментам или модель отвечает, что не знает.
- Если сервер недоступен, под ответом предупреждение: «Сервер local-git недоступен, его инструменты не использовались.» Ответ при этом всё равно приходит.
- В окне базы знаний выбранная база помечена значком «Документация проекта».

### Временные подсказки

`/help` без вопроса показывает подсказку `/help <вопрос>`, а без выбранной готовой базы подсказку выбрать базу. Подсказка временная: она не сохраняется в чате, не вызывает модель и не тратит лимит сообщений.

### Что важно знать

- `/help` не использует прежние сообщения чата и не меняет настройки RAG этого чата. Параметр `kb_id` в HTTP-запросе с `/help` игнорируется.
- На ходе `/help` название чата не создаётся автоматически: новый чат, открытый командой `/help`, сохраняет название по умолчанию «New Chat», пока вы не отправите обычное сообщение.
- Память задачи, факты и инвариантная проверка на ходе `/help` не работают.
- Та же команда работает по HTTP с ключом доступа: см. раздел «API keys and HTTP access», тело запроса `{"content": "/help как запустить приложение?", "model": "..."}`.

## Команда /support

Команда `/support` — ассистент поддержки. Он отвечает на вопрос по документации приложения (включая `docs/FAQ.md`) и по данным тикета или пользователя, которые читает из сервера поддержки. Вы выступаете как сотрудник поддержки: можно спросить о любом тикете и назвать причину проблемы клиента вместе с решением из документации.

### Как пользоваться

- `/support #T-1001 Почему не работает авторизация?` — вопрос по тикету. Принимаются `#T-1001`, `#t-1001`, `#T1001` и `#1001`.
- `/support @olga Что у неё с доступом?` — вопрос по пользователю (логин после `@`).
- `/support вопрос без идентификатора` — ответ только по документации (и по инструментам, если модель их вызовет).

Идентификатор должен стоять сразу после команды. Данные тикета или пользователя читаются кодом приложения до обращения к модели, поэтому карточка вызова `mcp__support__get_ticket` (или `mcp__support__get_user`) появляется раньше ответа. Слово после `/support`, не похожее на идентификатор, остаётся частью вопроса.

### Настройка

1. База документации — та же, что отмечена для `/help` («Настройки» → «Справка по проекту (/help)»). Если база создана раньше, добавьте в неё файлы заново (кнопка добавления файлов у готовой базы, папка `docs`), чтобы в ней появился `docs/FAQ.md`: изменённые файлы заменяют старые фрагменты, неизменённые пропускаются.
2. Добавьте MCP сервер поддержки через форму в разделе «MCP серверы»:

| Поле | Значение |
|------|----------|
| Название | `support` |
| Команда | `python` |
| Аргументы (по одному в строке) | строка 1: `mcp_servers/support_desk.py`; строка 2: `--data=mcp_servers/support_data.json` |
| Переменные окружения | пусто |
| Рабочая директория | корень репозитория (абсолютный путь, например `C:\Projects\AiAdventAgentV2`) |

Пути в аргументах относительные, поэтому рабочая директория обязательно должна быть корнем репозитория. Необязательный аргумент: `--max-chars=18000` (лимит результата одного вызова).

3. Откройте «Настройки» → «Поддержка (/support)». По умолчанию отмечен только сервер `support`. Поля «Top-K» (5), «Кандидатов» (20), «Порог» (по умолчанию 0.45; кнопка «Сбросить» возвращает значение, рассчитанное под модель эмбеддингов), четыре стадии поиска и встроенные инструменты «Память», «Задачи», «Планировщик» (выключены) работают так же, как в разделе `/help`, но хранятся отдельно. Выбор сохраняется сам. Базу документации в этом разделе только показывают: выбрать её можно в разделе `/help`.

Данные для проверки лежат в `mcp_servers/support_data.json` (четыре пользователя, тикеты `T-1001`–`T-1006`). Если отредактировать этот файл, изменения видны уже при следующем вызове: перезапуск сервера не нужен.

### Инструменты сервера поддержки

Все четыре инструмента только читают данные:

| Инструмент | Параметры | Что делает |
|------------|-----------|------------|
| `get_ticket` | `id` | Тикет с клиентом, окружением и перепиской |
| `get_user` | `user` | Клиент с тарифом, окружением и списком тикетов |
| `list_user_tickets` | `user`, `status` | Тикеты клиента, сначала недавно обновлённые |
| `search_tickets` | `text`, `status`, `category` | Поиск по теме, тексту ошибки и сообщениям |

### Что видно в чате

- Над ответом метка «Поддержка», «Поддержка · T-1001» или «Поддержка · @olga»; под ответом «Источники» и «Цитаты» из документации. После перезагрузки страницы метка, источники и цитаты остаются, а карточки вызовов не показываются снова.
- Если ответ получен только из данных тикета, раздела «Цитаты» нет.
- Если ничего не найдено, модель отвечает по данным тикета или говорит, что не знает.

### Предупреждения

Предупреждение выводится под ответом, ответ при этом всё равно приходит:

- «Тикет T-9999 не найден, ответ дан без данных тикета.»
- «Пользователь @login не найден, ответ дан без данных пользователя.»
- «Сервер поддержки недоступен (<причина>), ответ дан без данных тикета. Проверьте сервер support в разделе «MCP серверы».» Причина: «не добавлен», «отключён», «не подключается» или «ошибка инструмента».
- «Сервер support недоступен, его инструменты не использовались.» — выбранный сервер отсутствует или не подключился.

### Уточняющие вопросы

Приложение хранит последние 3 пары `/support`-вопрос и ответ этой ветки чата и отправляет их модели вместе с новым вопросом. Поэтому после `/support #T-1001 ...` можно продолжить `/support а как это исправить?` без идентификатора. Остальная история чата, профиль и инварианты на ходе `/support` не используются.

### Временные подсказки

`/support` без вопроса показывает подсказку `/support [#T-1042 или @login] <вопрос>`, а без отмеченной готовой базы документации — подсказку выбрать базу. Подсказка временная: она не сохраняется в чате, не вызывает модель и не тратит лимит сообщений.

### Что важно знать

- Вошедший пользователь считается сотрудником поддержки и может прочитать любой тикет из файла данных: проверки доступа по тикетам нет.
- Данные тикета и пользователя отправляются модели провайдера этого чата. С облачным провайдером (например, DeepSeek) они покидают компьютер; для локальной работы выберите LM Studio.
- Текст тикета считается данными, а не указаниями: он передаётся модели в блоке `<support_data>`, и просьбы внутри него не выполняются.
- Название чата на ходе `/support` не создаётся автоматически; память задачи, факты и инвариантная проверка не работают.
- Та же команда работает по HTTP с ключом доступа: `{"content": "/support #T-1001 Почему не работает авторизация?", "model": "..."}`; ответ содержит поле `support`.
- Проверка в браузере (`scripts/e2e_support_playwright.py`) на изолированной копии выполнена не полностью: пока в LM Studio не загружена модель чата, проверки с ответом модели записываются как NOT RUN, подробности в `.planning/phases/25-support-assistant-day-33/25-E2E.md`.

## Автоматическое ревью Pull Request

Когда в репозитории открывают или обновляют Pull Request, GitHub запускает задачу на вашем компьютере. Задача просит приложение сделать ревью, а приложение читает изменения, сверяет их с документацией и кодом проекта и отвечает моделью. Результат публикуется одним комментарием к Pull Request.

### Что вы получаете

- Один комментарий с тремя разделами: «Потенциальные баги», «Архитектурные проблемы», «Рекомендации». Под ними свёрнутый список источников (файлы документации и фрагменты кода, которые видела модель).
- В комментарии указаны коммит и модель. При новых коммитах комментарий заменяется, а не добавляется новый.
- Проверка только информационная: она не блокирует слияние. Если ревью не получилось, задача завершается с ошибкой и одной строкой с причиной (см. «Причины отказа»).

### Что нужно заранее

1. Приложение запущено (`python run.py`), в нём вы вошли в свою учётную запись.
2. LM Studio запущен, загружена модель эмбеддингов. Она нужна, даже если ревью пишет облачная модель: по ней идёт поиск по документации и коду.
3. MCP сервер с названием `github` настроен и включён так, как описано в разделе «Сервер GitHub (github)» (с флагом `--read-only`).
4. Есть база документации проекта (см. ниже, «Что положить в базу документации»).
5. Команда `git` на этом компьютере может выполнить `git fetch` из `origin` под той учётной записью Windows, от которой работает приложение.

### Раздел «Ревью Pull Request» в настройках

Откройте «Настройки» и найдите раздел «Ревью Pull Request».

- «Путь к репозиторию»: папка с клоном проекта на этом компьютере. Это обязательное поле. Папка должна быть git-репозиторием с удалённым `origin`.
- Кнопка «Выбрать папку…» рядом с полем открывает окно выбора папки Windows на компьютере, где запущен агент. Выбранный путь подставляется в поле, после чего нужно нажать «Сохранить настройки ревью». «Отмена» оставляет поле как было. Окно закрывается само через 5 минут; одновременно может быть открыто только одно окно. Если окно не открылось, путь можно ввести вручную.
- «База документации»: база знаний с правилами и описанием проекта. Если не выбрана, берётся база, отмеченная для `/help`.
- «Провайдер» и «Модель»: по умолчанию локальный LM Studio и загруженная в нём модель. Если выбрать облачного провайдера, он получит изменения Pull Request и фрагменты кода и документов; под полем появляется предупреждение об этом.
- «Состояние индекса кода»: ветка, коммит, число файлов и фрагментов. Кнопка «Обновить индекс кода» строит или обновляет индекс сразу, не дожидаясь первого ревью.
- Кнопка «Сохранить настройки ревью» записывает значения.

Индекс кода строится из базовой ветки Pull Request на `origin`, то есть только из запушенных коммитов; незакоммиченные правки и текущая ветка в рабочей папке на него не влияют. Перед каждым ревью индекс обновляется сам, переиндексируются только изменившиеся файлы. Первое ревью для новой базовой ветки поэтому медленное. В базу с названием `Код: …` не добавляйте файлы вручную: приложение управляет ей само.

### Что положить в базу документации

Добавьте в базу документации проекта файл `CLAUDE.md` кнопкой «Добавить файлы». В нём правила проекта (`structlog` вместо `print`, `datetime.now(timezone.utc)`, коммит после записи в базу, `ui` не импортирует `agent`), по которым ревью находит нарушения. Файлы `docs/` и `README.md` лежат там же. После правок этих файлов добавьте их заново: изменившиеся файлы заменяют прежние фрагменты.

### Установка раннера и секретов

Все шаги выполняются один раз.

1. Создайте ключ API: «Настройки» → «API ключи» → «+ Создать ключ», название `github-actions-review`. Скопируйте ключ из окна «Ключ создан»: повторно он не показывается.
2. На GitHub откройте репозиторий `AlekseyYakovlev/AI-Advent-Challenge-9-v2` → Settings → Secrets and variables → Actions. Создайте секрет репозитория `PR_REVIEW_API_KEY` со значением ключа.
3. Только если приложение работает не по адресу `http://127.0.0.1:8001`, создайте там же переменную (вкладка Variables) `PR_REVIEW_APP_URL` с адресом Agent. Запрос идёт на порт Agent, а не интерфейса.
4. Откройте Settings → Actions → Runners → New self-hosted runner. Выберите Windows и x64.
5. Скачайте архив раннера по ссылке со страницы и распакуйте в `C:\actions-runner`.
6. В терминале в этой папке выполните `config.cmd --url https://github.com/AlekseyYakovlev/AI-Advent-Challenge-9-v2 --token <токен со страницы> --labels pr-review`. Токен регистрации берётся со страницы GitHub и действует около часа. Для одноразового раннера добавьте `--ephemeral`.
7. Запустите раннер командой `run.cmd` в терминале того же пользователя Windows, у которого в PATH есть Python 3.11 или новее. Раннер, установленный как служба Windows, работает под другой учётной записью и может не найти Python, поэтому служба не рекомендуется.
8. Когда демонстрация закончена, остановите раннер сочетанием Ctrl+C.

Задача `PR review` в `.github/workflows/pr-review.yml` ищет раннер с метками `self-hosted`, `windows` и `pr-review`. Скрипт `scripts/pr_review_ci.py` вызывает приложение и публикует комментарий.

### Безопасная конфигурация

Это обязательный раздел, потому что репозиторий публичный. GitHub прямо предупреждает: self-hosted раннеры почти никогда не следует использовать в публичных репозиториях. Причина: Pull Request из форка может запустить чужой код на вашем компьютере, с доступом к вашим файлам и сети.

Обязательные настройки (флажки для проверки):

- [ ] Settings → Actions → General → «Approval for running fork pull request workflows from contributors» = «Require approval for all external contributors».
- [ ] Settings → Actions → General → «Workflow permissions» = «Read repository contents and packages permissions» (токен по умолчанию только для чтения; нужные права задаёт сам файл задачи).
- [ ] Никогда не одобряйте запуск задачи из форка незнакомого автора.
- [ ] У раннера уникальная метка `pr-review`, он обслуживает только эту задачу.
- [ ] Раннер остановлен, когда он не нужен (Ctrl+C), либо зарегистрирован с `--ephemeral` и выполняет одну задачу.
- [ ] Для задачи создан отдельный ключ API `github-actions-review`; после демонстрации его отзывают или заменяют.
- [ ] Файл задачи передаёт данные события только через переменные окружения, а `actions/checkout` закреплён по коммиту. Этого не меняйте.
- [ ] MCP сервер GitHub остаётся с флагом `--read-only`; токену дайте минимальные права на чтение.

Что остаётся рискованным. Одобрение запусков не даёт полной защиты: автор, чья правка уже была принята, может больше не требовать одобрения. Условие «только Pull Request из этого же репозитория» записано в самом файле задачи, и Pull Request может изменить этот файл. Поэтому надёжные меры — держать раннер выключенным, пока он не нужен, и принимать запуски только от тех, кому вы доверяете. Единственная полная защита — сделать репозиторий приватным: тогда посторонние не смогут присылать Pull Request, и угроза пропадает.

### Причины отказа

Если ревью не получилось, задача красная, а в сводке задачи одна строка. Что она значит и что делать:

| Строка | Что делать |
|--------|------------|
| «Ревью не выполнено: приложение недоступно по адресу …» | Запустите приложение; проверьте адрес в переменной `PR_REVIEW_APP_URL` (по умолчанию `http://127.0.0.1:8001`). |
| «Ревью не выполнено: ключ API отклонён» | Секрет `PR_REVIEW_API_KEY` не совпадает с ключом в приложении или ключ отозван; создайте ключ заново и обновите секрет. |
| «Ревью не выполнено: приложение ответило HTTP …» | Ответ не от приложения (прокси, другой порт); проверьте адрес. |
| «Ревью не выполнено: сервер GitHub не настроен или не подключён» | Включите MCP сервер `github` в настройках «MCP серверы» и проверьте токен. |
| «Ревью не выполнено: LM Studio недоступен» | Запустите LM Studio, загрузите модель чата и модель эмбеддингов, перезапустите задачу. |
| «Ревью не выполнено: модель вернула ошибку …» | Проверьте провайдера и модель в разделе «Ревью Pull Request»; перезапустите задачу. |
| «Ревью не выполнено: не задана переменная …» | В задаче не хватает секрета или переменной окружения; проверьте имена секретов. |
| «Ревью не выполнено: переменная REPO имеет неверный формат» | Вызов вне GitHub Actions или повреждённое окружение; формат `владелец/репозиторий`. |
| «Ревью не выполнено: переменная PR_NUMBER имеет неверный формат» | То же для номера Pull Request. |
| «Ревью не выполнено: … (код …)» | Прочие причины приложения, код указан в скобках; описания кодов в `docs/API_SPEC.md`. Например, `REVIEW_NOT_CONFIGURED`: не сохранён путь к репозиторию; `PR_TOO_LARGE`: Pull Request слишком большой. |
| «Комментарий не опубликован: GitHub недоступен» | Проверьте сеть раннера и повторите задачу. |
| «Комментарий не опубликован: GitHub ответил HTTP …» | Проверьте права задачи (`pull-requests: write`, при отказе добавьте `issues: write`). |

### Ограничения

- Один общий комментарий, без комментариев к строкам кода.
- Очень большой Pull Request отклоняется с названной причиной (`PR_TOO_LARGE`), а файлы без текста изменений называются в комментарии как не проанализированные.
- Первое ревью для новой базовой ветки медленное: строится индекс кода.
- Задача запускается только для Pull Request, в объединённом дереве которого есть файл `.github/workflows/pr-review.yml`, то есть для Pull Request в ветку `Day32` (или в ветку, отрезанную от неё); Pull Request в `main` не ревьюируются, пока `Day31` и затем `Day32` не слиты в `main`, а это отдельный шаг, который в рамках этой фазы не выполняется.
- Ревью не заменяет проверку человеком: модель может ошибаться и пропускать дефекты.

## Tips

1. **Start with Sliding Window** for most conversations
2. **Use Sticky Facts** for long research sessions
3. **Use Truncate Middle** when initial instructions matter
4. **Use No Compression** only for short, critical chats
5. **Monitor statistics** to avoid overflow
6. **Adjust context_length** based on your model's capacity
7. **Lower temperature** for factual responses
8. **Raise temperature** for creative tasks
9. **Dialog windows** (Settings, Add user, Scheduler) close only with the × button or Cancel; clicking outside the window or pressing Esc does not close them, so unsaved input is not lost by accident

## Troubleshooting

**WebSocket connection failed**
- Check agent is running (port 8001)
- Check firewall/antivirus
- Restart application

**LM Studio models not showing**
- Ensure LM Studio is running
- Check LM Studio server is enabled
- Verify port 1234 is accessible

**Context overflow error**
- Change compression strategy
- Reduce conversation length
- Increase context_length
- Start new chat

**Slow responses**
- Check model is loaded in RAM/VRAM
- Reduce max_tokens
- Use faster model
- Check internet connection (cloud models)

**Statistics not updating**
- Refresh page
- Check WebSocket connection
- Restart application

**Cannot open the app from another computer**
- `BIND_HOST=0.0.0.0` is set in `.env` and the app was restarted
- The address used is one of those printed at startup
- The firewall rule exists and the network is Private
- Both ports 8000 and 8001 are allowed
- A VPN or guest-Wi-Fi client isolation can block LAN traffic

**MCP server local-git does not connect**
- The folder in `--root` is a git repository (run `git init` there if it is not) and exists
- `git` is available in PATH
- The script path in the first argument is absolute and exists

**MCP tool result is truncated**
- The result is cut at `--max-chars` (18000 by default); narrow the question with `path`
- A git command that runs longer than `--timeout` (10 seconds by default) is stopped; narrow it or raise the value

**GitHub tools return 401**
- The token is invalid, expired or has no access to `AlekseyYakovlev/AI-Advent-Challenge-9-v2`
- Create a new fine-grained token and save it again in the form
