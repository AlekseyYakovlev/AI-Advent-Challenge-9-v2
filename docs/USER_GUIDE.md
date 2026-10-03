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
1. Нажмите «+ Новая база знаний», введите название и выберите файлы (PDF, TXT, MD; до 10 файлов, до 50 МБ каждый, до 100 МБ всего). Одинаковые файлы добавить нельзя.
2. Выберите стратегию разбиения. «Фиксированная длина» режет текст на куски заданного размера (100-2000 символов) с перекрытием (меньше размера и не больше его половины). «Структурная» делит по главам, статьям и заголовкам, а размер служит верхним пределом.
3. Выберите модель эмбеддингов. В списке только модели LM Studio типа embeddings (по умолчанию `text-embedding-nomic-embed-text-v1.5`). Галочка «показать все модели» показывает и остальные, но обычные языковые модели, например `giga-embeddings-instruct-480m-0826`, не поддерживают эмбеддинги в LM Studio, и проверка вернёт ошибку. Кнопка «Проверить эмбеддинг» показывает размерность вектора.
4. Нажмите «Индексировать». Ошибки проверки показываются под формой, окно остаётся открытым.

Прогресс виден на карточке: «в очереди», «загрузка модели…», «разбор файлов…», «индексация N из M», затем «готово» с числом файлов и чанков. Если индексация не удалась, статус «ошибка»; нажмите на текст ошибки, чтобы развернуть его. PDF-скан без текстового слоя не поддерживается. Если приложение перезапущено во время индексации, такая база получает ошибку, её нужно удалить и создать заново.

«Тест поиска» (доступен для готовой базы) принимает вопрос и показывает 5 ближайших фрагментов: оценка сходства, файл, раздел и идентификатор чанка; «показать полностью» раскрывает текст.

«Удалить» просит подтверждение («Точно удалить?»); база удаляется вместе с файлами, даже если идёт индексация.

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
