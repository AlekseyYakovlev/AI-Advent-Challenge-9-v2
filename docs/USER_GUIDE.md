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

## Tips

1. **Start with Sliding Window** for most conversations
2. **Use Sticky Facts** for long research sessions
3. **Use Truncate Middle** when initial instructions matter
4. **Use No Compression** only for short, critical chats
5. **Monitor statistics** to avoid overflow
6. **Adjust context_length** based on your model's capacity
7. **Lower temperature** for factual responses
8. **Raise temperature** for creative tasks

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
