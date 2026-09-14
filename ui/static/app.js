'use strict';

const AGENT_PORT = 8001;
const AGENT_BASE = `http://${window.location.hostname}:${AGENT_PORT}`;
const WS_BASE = `ws://${window.location.hostname}:${AGENT_PORT}`;
const STOP_RECONNECT_CODES = new Set([1008, 1011, 1003, 1013]);
const MAX_RECONNECT_DELAY = 30000;

const state = {
    chats: [],
    currentChatId: null,
    messages: [],
    childrenByParent: new Map(),
    activeChildByParent: new Map(),
    models: [],
    selectedModel: '',
    isStreaming: false,
    ws: null,
    reconnectAttempt: 0,
    reconnectTimer: null,
    shouldReconnect: true,
    lastFailedMessage: null,
    lastStats: null,
    lastStatsChatId: null,
    contextWindow: null,
    isStatsLocal: false,
    statsAbortController: null,
};

const $ = (id) => document.getElementById(id);

function showToast(message, type = 'error') {
    const container = $('toast-container');
    const el = document.createElement('div');
    const colors = {
        error: 'bg-red-900/90 border-red-700',
        info: 'bg-slate-800/90 border-slate-600',
        success: 'bg-emerald-900/90 border-emerald-700',
        warning: 'bg-yellow-900/90 border-yellow-700',
    };
    el.className = `px-4 py-3 rounded-lg border text-sm shadow-lg ${colors[type] || colors.info}`;
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => el.remove(), 5000);
}

async function apiFetch(path, options = {}) {
    const resp = await fetch(`${AGENT_BASE}${path}`, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
    });
    if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        const detail = body.detail || resp.statusText;
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    if (resp.status === 204) return null;
    return resp.json();
}

function renderMarkdown(text) {
    const raw = marked.parse(text || '', { breaks: true });
    return DOMPurify.sanitize(raw);
}

function parentKey(parentId) {
    return parentId === null || parentId === undefined ? 'root' : String(parentId);
}

function registerChild(parentId, childId) {
    const key = parentKey(parentId);
    if (!state.childrenByParent.has(key)) {
        state.childrenByParent.set(key, []);
    }
    const siblings = state.childrenByParent.get(key);
    if (!siblings.includes(childId)) {
        siblings.push(childId);
        siblings.sort((a, b) => a - b);
    }
}

function getMessageById(id) {
    return state.messages.find((m) => m.id === id);
}

function getBranchLeaf(messageId) {
    let current = messageId;
    while (true) {
        const children = state.childrenByParent.get(String(current)) || [];
        if (!children.length) break;
        const preferred = state.activeChildByParent.get(String(current));
        current = preferred && children.includes(preferred) ? preferred : children[children.length - 1];
    }
    return current;
}

function branchControlsHtml(message) {
    const key = parentKey(message.parent_id);
    const siblings = state.childrenByParent.get(key) || [];
    let nav = '';
    if (siblings.length > 1) {
        const idx = siblings.indexOf(message.id);
        if (idx >= 0) {
            nav = `
                <div class="branch-controls flex items-center gap-1 text-xs text-slate-400">
                    <button data-branch-prev="${message.parent_id ?? 'root'}"
                        class="branch-btn px-1 hover:text-white disabled:opacity-30"
                        ${idx === 0 ? 'disabled' : ''}>◀</button>
                    <span>${idx + 1}/${siblings.length}</span>
                    <button data-branch-next="${message.parent_id ?? 'root'}"
                        class="branch-btn px-1 hover:text-white disabled:opacity-30"
                        ${idx === siblings.length - 1 ? 'disabled' : ''}>▶</button>
                </div>`;
        }
    }
    return `
        <div class="flex items-center gap-2 mt-1">
            ${nav}
            <button data-branch-from="${message.id}"
                class="text-xs text-slate-500 hover:text-indigo-400 transition">↩ отсюда</button>
        </div>`;
}

function renderMessages() {
    const container = $('messages');
    container.innerHTML = '';
    const ordered = [...state.messages].reverse();
    ordered.forEach((msg) => {
        const isUser = msg.role === 'user';
        const wrapper = document.createElement('div');
        wrapper.className = `flex ${isUser ? 'justify-end' : 'justify-start'}`;
        wrapper.dataset.messageId = msg.id;
        const bubble = document.createElement('div');
        bubble.className = `max-w-[75%] rounded-xl px-4 py-2 text-sm ${
            isUser ? 'bg-indigo-600 text-white' : 'bg-slate-800 text-slate-100'
        }`;
        const content = document.createElement('div');
        content.className = 'message-content prose prose-invert prose-sm max-w-none';
        content.innerHTML = isUser ? DOMPurify.sanitize(msg.content) : renderMarkdown(msg.content);
        bubble.appendChild(content);
        if (msg.token_count && msg.token_count > 0) {
            const tokenInfo = document.createElement('div');
            tokenInfo.className = `text-xs mt-1 ${isUser ? 'text-indigo-200' : 'text-slate-400'}`;
            tokenInfo.textContent = `${msg.token_count} tokens`;
            bubble.appendChild(tokenInfo);
        }
        const controls = document.createElement('div');
        controls.innerHTML = branchControlsHtml(msg);
        bubble.appendChild(controls);
        wrapper.appendChild(bubble);
        container.appendChild(wrapper);
    });
    if (state.isStreaming) {
        appendLoadingBubble();
    }
    container.scrollTop = container.scrollHeight;
    renderStatsPanel();
}

function renderStatsPanel() {
    const userTokens = state.messages
        .filter((m) => m.role === 'user')
        .reduce((s, m) => s + (m.token_count || 0), 0);
    const assistantTokens = state.messages
        .filter((m) => m.role === 'assistant')
        .reduce((s, m) => s + (m.token_count || 0), 0);
    const reqEl = $('stats-request-tokens');
    const respEl = $('stats-response-tokens');
    if (reqEl) reqEl.textContent = `${userTokens} tokens`;
    if (respEl) respEl.textContent = `${assistantTokens} tokens`;

    const stats = state.lastStats;
    const ctxSize = stats?.current_context_size ?? (userTokens + assistantTokens);
    const ctxWindow = stats?.context_window_size ?? state.contextWindow ?? 4096;
    const percent = stats?.usage_percent
        ?? (ctxWindow > 0 ? Math.round((ctxSize / ctxWindow) * 1000) / 10 : 0);

    const ctxEl = $('stats-current-context');
    if (ctxEl) {
        ctxEl.textContent = `${ctxSize} / ${ctxWindow}`;
        ctxEl.className = percent >= 90 ? 'text-red-400 font-semibold'
            : percent >= 75 ? 'text-yellow-400 font-semibold'
            : 'text-white font-semibold';
    }
    const bar = $('stats-usage-bar');
    if (bar) {
        bar.style.width = `${Math.min(percent, 100)}%`;
        bar.className = `absolute left-0 top-0 h-full rounded-full transition-all ${
            percent > 90 ? 'bg-red-500' : percent > 75 ? 'bg-yellow-500' : 'bg-indigo-500'}`;
    }
    const pctEl = $('stats-usage-percent');
    if (pctEl) pctEl.textContent = `${percent}%`;

    const localIndicator = $('stats-local-indicator');
    if (localIndicator) {
        localIndicator.style.display = state.lastStats ? 'none' : 'block';
        if (!state.lastStats) {
            localIndicator.textContent = '⚠️ Локальный подсчет (сервер недоступен)';
            localIndicator.className = 'text-xs text-yellow-500 mt-1';
        }
    }
}

function updateStats(stats) {
    if (stats?.chat_id && stats.chat_id !== state.currentChatId) {
        return;
    }
    if (!stats || stats.error) {
        state.lastStats = null;
        state.isStatsLocal = true;
    } else {
        state.lastStats = stats;
        state.lastStatsChatId = stats.chat_id;
        state.contextWindow = stats.context_window_size;
        state.isStatsLocal = false;
    }
    renderStatsPanel();
}

async function loadChatStats(chatId) {
    if (state.statsAbortController) {
        state.statsAbortController.abort();
        state.statsAbortController = null;
    }
    state.statsAbortController = new AbortController();
    try {
        const stats = await apiFetch(
            `/api/v1/chats/${chatId}/stats`,
            { signal: state.statsAbortController.signal },
        );
        updateStats(stats);
    } catch (err) {
        if (err.name !== 'AbortError') {
            console.error('Failed to load stats:', err);
            updateStats(null);
        }
    }
}

function appendLoadingBubble() {
    const container = $('messages');
    const existing = container.querySelector('[data-streaming="true"]');
    if (existing) return;
    const wrapper = document.createElement('div');
    wrapper.className = 'flex justify-start';
    wrapper.dataset.streaming = 'true';
    wrapper.innerHTML = `
        <div class="max-w-[75%] rounded-xl px-4 py-3 bg-slate-800 text-sm">
            <div class="flex gap-1 items-center">
                <span class="typing-dot w-2 h-2 rounded-full bg-slate-400 inline-block"></span>
                <span class="typing-dot w-2 h-2 rounded-full bg-slate-400 inline-block"></span>
                <span class="typing-dot w-2 h-2 rounded-full bg-slate-400 inline-block"></span>
            </div>
            <div class="message-content mt-1" data-stream-content></div>
        </div>`;
    container.appendChild(wrapper);
    container.scrollTop = container.scrollHeight;
}

function appendTokenToStream(token) {
    const bubble = document.querySelector('[data-streaming="true"]');
    if (!bubble) appendLoadingBubble();
    const contentEl = document.querySelector('[data-stream-content]');
    if (!contentEl) return;
    const current = contentEl.dataset.raw || '';
    const updated = current + token;
    contentEl.dataset.raw = updated;
    contentEl.innerHTML = renderMarkdown(updated);
    $('messages').scrollTop = $('messages').scrollHeight;
}

function removeLoadingBubble() {
    const el = document.querySelector('[data-streaming="true"]');
    if (el) el.remove();
}

function setStreaming(active) {
    state.isStreaming = active;
    $('message-input').disabled = active;
    $('btn-send').disabled = active;
}

function renderChatList() {
    const list = $('chat-list');
    list.innerHTML = '';
    state.chats.forEach((chat) => {
        const btn = document.createElement('button');
        const active = chat.id === state.currentChatId;
        btn.className = `w-full text-left rounded-lg px-3 py-2 text-sm truncate transition ${
            active ? 'bg-slate-800 text-white' : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
        }`;
        btn.textContent = chat.title;
        btn.dataset.chatId = chat.id;
        btn.addEventListener('click', () => selectChat(chat.id));
        list.appendChild(btn);
    });
}

async function checkAgentHealth() {
    try {
        const resp = await fetch(`${AGENT_BASE}/health`, { signal: AbortSignal.timeout(2000) });
        const ok = resp.ok;
        $('agent-status-text').textContent = ok ? 'online' : 'offline';
        $('agent-status-text').className = ok ? 'text-emerald-400' : 'text-red-400';
        return ok;
    } catch {
        $('agent-status-text').textContent = 'offline';
        $('agent-status-text').className = 'text-red-400';
        return false;
    }
}

async function loadChats() {
    state.chats = await apiFetch('/api/v1/chats');
    renderChatList();
}

async function loadChatTree(chatId) {
    const path = await apiFetch(`/api/v1/chats/${chatId}/tree`);
    state.messages = path;
    path.forEach((msg) => registerChild(msg.parent_id, msg.id));
    renderMessages();
}

async function selectChat(chatId) {
    if (state.currentChatId === chatId && state.ws?.readyState === WebSocket.OPEN) {
        renderChatList();
        return;
    }
    disconnectWs(false);
    state.currentChatId = chatId;
    state.lastStats = null;
    state.lastStatsChatId = null;
    state.isStatsLocal = false;
    state.childrenByParent.clear();
    state.activeChildByParent.clear();
    const chat = state.chats.find((c) => c.id === chatId);
    $('chat-title').textContent = chat?.title || 'Чат';
    renderChatList();
    await loadChatTree(chatId);
    await loadChatStats(chatId);
    connectWs(chatId);
}

async function createChat() {
    const chat = await apiFetch('/api/v1/chats', {
        method: 'POST',
        body: JSON.stringify({ title: 'New Chat' }),
    });
    await loadChats();
    await selectChat(chat.id);
}

async function deleteChat(chatId) {
    await apiFetch(`/api/v1/chats/${chatId}`, { method: 'DELETE' });
    if (state.currentChatId === chatId) {
        state.currentChatId = null;
        disconnectWs(false);
        state.messages = [];
        renderMessages();
    }
    await loadChats();
    if (!state.currentChatId && state.chats.length) {
        await selectChat(state.chats[0].id);
    }
}

function disconnectWs(allowReconnect) {
    state.shouldReconnect = allowReconnect;
    if (state.reconnectTimer) {
        clearTimeout(state.reconnectTimer);
        state.reconnectTimer = null;
    }
    if (state.ws) {
        state.ws.onclose = null;
        state.ws.close();
        state.ws = null;
    }
}

function scheduleReconnect(chatId) {
    if (!state.shouldReconnect || !chatId) return;
    const delay = Math.min(1000 * 2 ** state.reconnectAttempt, MAX_RECONNECT_DELAY);
    state.reconnectAttempt += 1;
    state.reconnectTimer = setTimeout(() => connectWs(chatId), delay);
}

function connectWs(chatId) {
    if (!chatId) return;
    disconnectWs(true);
    state.shouldReconnect = true;
    const ws = new WebSocket(`${WS_BASE}/ws/chat/${chatId}`);
    state.ws = ws;

    ws.onopen = () => {
        state.reconnectAttempt = 0;
        if (state.currentChatId === chatId) loadChatStats(chatId);
    };

    ws.onmessage = (event) => {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch {
            return;
        }
        handleWsMessage(data);
    };

    ws.onclose = (event) => {
        if (STOP_RECONNECT_CODES.has(event.code)) {
            state.shouldReconnect = false;
            showToast(`Соединение закрыто: ${event.reason || event.code}`, 'error');
            return;
        }
        if (state.shouldReconnect && state.currentChatId === chatId) {
            scheduleReconnect(chatId);
        }
    };

    ws.onerror = () => {
        showToast('Ошибка WebSocket', 'error');
    };
}

function blockInputWithMessage(message, suggestedStrategy = 'sliding') {
    const input = $('message-input');
    const sendBtn = $('btn-send');

    if (input) {
        input.disabled = true;
        input.placeholder = 'Контекст переполнен';
    }
    if (sendBtn) sendBtn.disabled = true;

    const bannerContainer = $('overflow-banner-container');
    if (bannerContainer && !bannerContainer.hasChildNodes()) {
        const banner = document.createElement('div');
        banner.id = 'overflow-banner';
        banner.className = 'bg-red-900/50 border border-red-700 rounded-lg p-4 mb-4';

        const strategyNames = {
            sliding: 'Sliding Window',
            sticky: 'Sticky Facts',
            truncate_middle: 'Truncate Middle',
            no_compression: 'Без сжатия',
        };

        banner.innerHTML = `
            <div class="flex items-start gap-3">
                <svg class="w-6 h-6 text-red-400 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
                </svg>
                <div class="flex-1">
                    <h3 class="text-red-200 font-semibold mb-1">Контекст переполнен</h3>
                    <p class="text-red-300 text-sm mb-3">${message}</p>
                    <div class="flex gap-2">
                        <button onclick="autoSwitchStrategy('${suggestedStrategy}')"
                                class="bg-red-700 hover:bg-red-600 text-white px-4 py-2 rounded-lg text-sm font-medium transition">
                            Переключить на ${strategyNames[suggestedStrategy] || suggestedStrategy}
                        </button>
                        <button onclick="openSettingsModal()"
                                class="bg-slate-700 hover:bg-slate-600 text-white px-4 py-2 rounded-lg text-sm font-medium transition">
                            Открыть настройки
                        </button>
                    </div>
                </div>
            </div>
        `;
        bannerContainer.appendChild(banner);
    }
}

function unblockInput() {
    const input = $('message-input');
    const sendBtn = $('btn-send');
    const banner = $('overflow-banner');

    if (input) {
        input.disabled = false;
        input.placeholder = 'Введите сообщение...';
    }
    if (sendBtn) sendBtn.disabled = false;
    if (banner) banner.remove();
}

async function autoSwitchStrategy(strategy, retryCount = 0) {
    try {
        const body = {
            strategy,
            chat_id: state.currentChatId || null,
        };

        await apiFetch('/api/v1/settings', { method: 'PUT', body: JSON.stringify(body) });
        showToast(`Стратегия изменена на ${strategy}`, 'success');
        unblockInput();

        const lastMessage = state.lastFailedMessage;
        if (lastMessage) {
            state.lastFailedMessage = null;
            await sendMessage(lastMessage);
        }
    } catch (err) {
        if (retryCount < 3) {
            showToast(`Ошибка сети. Повторная попытка ${retryCount + 1}/3...`, 'warning');
            setTimeout(() => autoSwitchStrategy(strategy, retryCount + 1), 2000);
        } else {
            showToast('Не удалось сменить стратегию: ' + err.message, 'error');
        }
    }
}

function handleWsMessage(data) {
    switch (data.type) {
        case 'token':
            appendTokenToStream(data.content || '');
            break;
        case 'done':
            setStreaming(false);
            removeLoadingBubble();
            unblockInput();
            state.lastFailedMessage = null;
            if (data.stats) updateStats(data.stats);
            if (state.currentChatId) loadChatTree(state.currentChatId);
            break;
        case 'error':
            setStreaming(false);
            removeLoadingBubble();

            if (data.code === 'CONTEXT_OVERFLOW') {
                const input = $('message-input');
                if (input && input.value) {
                    state.lastFailedMessage = input.value;
                }

                const suggestedStrategy = data.suggested_strategy || 'sliding';
                blockInputWithMessage(data.detail, suggestedStrategy);
                showToast('⚠️ ' + data.detail, 'error');
            } else {
                showToast(data.detail || 'Ошибка', 'error');
            }
            break;
        case 'model_loaded':
        case 'model_event':
            refreshModelSelector();
            break;
        default:
            break;
    }
}

async function sendMessage(content) {
    if (!state.currentChatId || !content.trim() || state.isStreaming) return;

    if (!state.selectedModel) {
        showToast('Выберите модель', 'error');
        return;
    }

    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
        showToast('WebSocket не подключён', 'error');
        connectWs(state.currentChatId);
        return;
    }

    const trimmed = content.trim();
    state.lastFailedMessage = trimmed;

    setStreaming(true);
    appendLoadingBubble();

    state.ws.send(JSON.stringify({
        content: trimmed,
        model: state.selectedModel,
    }));

    const input = $('message-input');
    if (input) input.value = '';
}

async function branchFromMessage(messageId) {
    if (!state.currentChatId || state.isStreaming) return;
    await apiFetch(`/api/v1/chats/${state.currentChatId}/branch`, {
        method: 'POST',
        body: JSON.stringify({ message_id: messageId }),
    });
    await loadChatTree(state.currentChatId);
    loadChatStats(state.currentChatId);
    $('message-input').focus();
}

async function switchBranch(parentRef, direction) {
    const key = parentRef === 'root' ? 'root' : String(parentRef);
    const parentId = parentRef === 'root' ? null : Number(parentRef);
    const siblings = state.childrenByParent.get(key) || [];
    const currentMsg = state.messages.find((m) => m.parent_id === parentId);
    if (!currentMsg || siblings.length <= 1) return;
    const idx = siblings.indexOf(currentMsg.id);
    const newIdx = direction === 'prev' ? idx - 1 : idx + 1;
    if (newIdx < 0 || newIdx >= siblings.length) return;
    const targetSibling = siblings[newIdx];
    const leafId = getBranchLeaf(targetSibling);
    state.activeChildByParent.set(key, targetSibling);
    await apiFetch(`/api/v1/chats/${state.currentChatId}/branch`, {
        method: 'POST',
        body: JSON.stringify({ message_id: leafId }),
    });
    await loadChatTree(state.currentChatId);
    loadChatStats(state.currentChatId);
}

async function loadModels() {
    const select = $('model-select');
    try {
        state.models = await apiFetch('/api/v1/lm-studio/models');
        populateModelSelect();
    } catch (err) {
        select.innerHTML = '<option value="">⚠️ LM Studio не запущен</option>';
        showToast('⚠️ LM Studio не запущен. Запустите LM Studio и обновите страницу.', 'error');
    }
}

function populateModelSelect() {
    const select = $('model-select');
    select.innerHTML = '';
    if (!state.models.length) {
        select.innerHTML = '<option value="">Нет моделей</option>';
        return;
    }
    state.models.forEach((model) => {
        const opt = document.createElement('option');
        opt.value = model.id;
        const loaded = model.loaded ? ' ✓' : '';
        opt.textContent = `${model.id}${loaded}`;
        select.appendChild(opt);
    });
    const loaded = state.models.find((m) => m.loaded);
    if (loaded) {
        select.value = loaded.id;
        state.selectedModel = loaded.id;
    } else if (state.models.length) {
        select.value = state.models[0].id;
        state.selectedModel = state.models[0].id;
    }
}

async function refreshModelSelector() {
    try {
        state.models = await apiFetch('/api/v1/lm-studio/models');
        populateModelSelect();
    } catch {
        $('model-select').innerHTML = '<option value="">⚠️ LM Studio не запущен</option>';
    }
}

async function onModelSelect(modelId) {
    if (!modelId) return;
    state.contextWindow = null;
    try {
        state.models = await apiFetch('/api/v1/lm-studio/models');
    } catch {
        showToast('⚠️ LM Studio не запущен. Запустите LM Studio и попробуйте снова.', 'error');
        return;
    }
    const model = state.models.find((m) => m.id === modelId);
    if (!model) {
        showToast('Модель не найдена', 'error');
        return;
    }
    if (model.loaded) {
        state.selectedModel = modelId;
        return;
    }
    const confirmed = confirm(`Модель «${modelId}» не загружена. Загрузить?`);
    if (!confirmed) {
        populateModelSelect();
        return;
    }
    try {
        const result = await apiFetch('/api/v1/lm-studio/load-model', {
            method: 'POST',
            body: JSON.stringify({ model_id: modelId, gpu_offload: 0 }),
        });
        if (result.status === 'LOADED') {
            state.selectedModel = modelId;
            showToast(`Модель ${modelId} загружена`, 'success');
            await refreshModelSelector();
        } else if (result.status === 'UNREACHABLE') {
            showToast('⚠️ LM Studio не запущен. Запустите LM Studio и попробуйте снова.', 'error');
        } else {
            showToast(result.message || 'Ошибка загрузки модели', 'error');
            populateModelSelect();
        }
    } catch (err) {
        showToast(err.message, 'error');
        populateModelSelect();
    }
}

async function openSettingsModal() {
    const perChat = $('settings-per-chat').checked;
    const chatId = perChat && state.currentChatId ? state.currentChatId : null;
    const query = chatId ? `?chat_id=${chatId}` : '';
    const settings = await apiFetch(`/api/v1/settings${query}`);

    $('settings-strategy').value = settings.strategy;

    const contextLength = settings.context_length || 4096;
    $('settings-context-length').value = contextLength;
    $('context-length-value').textContent = contextLength;

    const warning = $('context-length-warning');
    if (warning) {
        warning.style.display = contextLength > 8192 ? 'block' : 'none';
    }

    $('settings-temperature').value = settings.temperature;
    $('temperature-value').textContent = settings.temperature;
    $('settings-max-tokens').value = settings.max_tokens;
    $('settings-system-prompt').value = settings.system_prompt;
    $('settings-modal').classList.remove('hidden');
}

function closeSettingsModal() {
    $('settings-modal').classList.add('hidden');
}

async function saveSettings(event) {
    event.preventDefault();
    const perChat = $('settings-per-chat').checked;
    const body = {
        strategy: $('settings-strategy').value,
        context_length: parseInt($('settings-context-length').value, 10),
        temperature: parseFloat($('settings-temperature').value),
        max_tokens: parseInt($('settings-max-tokens').value, 10),
        system_prompt: $('settings-system-prompt').value,
    };
    if (perChat && state.currentChatId) {
        body.chat_id = state.currentChatId;
    } else {
        body.chat_id = null;
    }
    await apiFetch('/api/v1/settings', { method: 'PUT', body: JSON.stringify(body) });
    showToast('Настройки сохранены', 'success');
    closeSettingsModal();

    unblockInput();

    if (state.currentChatId) {
        await loadChatStats(state.currentChatId);
    }
}

function bindEvents() {
    $('btn-new-chat').addEventListener('click', () => createChat().catch((e) => showToast(e.message)));
    $('chat-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const text = $('message-input').value;
        sendMessage(text).catch((err) => {
            setStreaming(false);
            removeLoadingBubble();
            showToast(err.message, 'error');
        });
    });
    $('message-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            $('chat-form').requestSubmit();
        }
    });
    $('btn-settings').addEventListener('click', () => {
        openSettingsModal().catch((e) => showToast(e.message));
    });
    $('btn-close-settings').addEventListener('click', closeSettingsModal);
    $('btn-cancel-settings').addEventListener('click', closeSettingsModal);
    $('settings-form').addEventListener('submit', (e) => {
        saveSettings(e).catch((err) => showToast(err.message, 'error'));
    });
    $('settings-temperature').addEventListener('input', (e) => {
        $('temperature-value').textContent = e.target.value;
    });
    $('settings-context-length').addEventListener('input', (e) => {
        const val = parseInt(e.target.value, 10);
        $('context-length-value').textContent = val;

        const warning = $('context-length-warning');
        if (warning) {
            warning.style.display = val > 8192 ? 'block' : 'none';
        }
    });
    $('model-select').addEventListener('change', (e) => {
        onModelSelect(e.target.value).catch((err) => showToast(err.message, 'error'));
    });
    $('messages').addEventListener('click', (e) => {
        const fromBtn = e.target.closest('[data-branch-from]');
        if (fromBtn) {
            branchFromMessage(Number(fromBtn.dataset.branchFrom))
                .catch((err) => showToast(err.message, 'error'));
            return;
        }
        const prev = e.target.closest('[data-branch-prev]');
        const next = e.target.closest('[data-branch-next]');
        if (prev) {
            switchBranch(prev.dataset.branchPrev, 'prev').catch((err) => showToast(err.message, 'error'));
        }
        if (next) {
            switchBranch(next.dataset.branchNext, 'next').catch((err) => showToast(err.message, 'error'));
        }
    });
    $('settings-modal').addEventListener('click', (e) => {
        if (e.target === $('settings-modal')) closeSettingsModal();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeSettingsModal();
    });
    $('chat-list').addEventListener('contextmenu', (e) => {
        const btn = e.target.closest('[data-chat-id]');
        if (!btn) return;
        e.preventDefault();
        const chatId = Number(btn.dataset.chatId);
        if (confirm('Удалить этот чат?')) {
            deleteChat(chatId).catch((err) => showToast(err.message, 'error'));
        }
    });
}

async function init() {
    bindEvents();
    await checkAgentHealth();
    setInterval(checkAgentHealth, 5000);
    await loadModels();
    try {
        await loadChats();
        if (state.chats.length) {
            await selectChat(state.chats[0].id);
        } else {
            await createChat();
        }
    } catch (err) {
        showToast(`Не удалось загрузить чаты: ${err.message}`, 'error');
    }
}

init();
