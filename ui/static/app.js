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
    lastMemory: null,
    lastProfile: null,
    lastTasks: null,
    lastGlobalInvariants: null,
    editingGlobalInvariantId: null,
    lastChatInvariants: null,
    editingChatInvariantId: null,
    lastConflicts: [],
};

const TASK_STATE_LABELS = {
    planning: 'планирование',
    execution: 'выполнение',
    validation: 'проверка',
    done: 'готово',
    cancelled: 'отменено',
};

const TASK_STATE_BADGE_CLASSES = {
    planning: 'text-slate-400',
    execution: 'text-sky-400',
    validation: 'text-amber-400',
    done: 'text-emerald-400',
    cancelled: 'text-red-400',
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
        credentials: 'include',
        ...options,
    });
    if (resp.status === 401) {
        window.location.href = '/static/login.html?expired=1';
        return;
    }
    if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        const detail = body.detail || resp.statusText;
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    if (resp.status === 204) return null;
    return resp.json();
}

async function logout() {
    await apiFetch('/api/v1/auth/logout', { method: 'POST' });
    window.location.href = '/static/login.html';
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
        state.lastConflicts
            .filter((conflict) => conflict.message_id === msg.id)
            .forEach((conflict) => {
                container.appendChild(buildConflictBanner(conflict));
            });
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

async function loadChatMemory(chatId) {
    try {
        const data = await apiFetch(`/api/v1/chats/${chatId}/memory`);
        state.lastMemory = data;
        renderMemoryPanel();
    } catch (err) {
        console.error('Failed to load memory:', err);
    }
}

function renderMemoryEntries(container, entries) {
    container.replaceChildren();
    if (!entries.length) {
        const empty = document.createElement('div');
        empty.className = 'text-slate-600';
        empty.textContent = '—';
        container.appendChild(empty);
        return;
    }
    entries.forEach((entry) => {
        const row = document.createElement('div');
        row.className = 'rounded-lg bg-slate-800 px-2 py-1';

        const keyEl = document.createElement('div');
        keyEl.className = 'text-slate-300 font-semibold';
        keyEl.textContent = entry.key;

        const valueEl = document.createElement('div');
        valueEl.className = 'text-slate-400 truncate';
        const truncated = entry.value.length > 160 ? `${entry.value.slice(0, 160)}…` : entry.value;
        valueEl.textContent = truncated;
        valueEl.title = entry.value;

        row.appendChild(keyEl);
        row.appendChild(valueEl);
        container.appendChild(row);
    });
}

function renderMemoryPanel() {
    const data = state.lastMemory;
    const shortTermEl = $('memory-short-term-count');
    const workingCountEl = $('memory-working-count');
    const longTermCountEl = $('memory-long-term-count');
    const workingEl = $('memory-working');
    const longTermEl = $('memory-long-term');
    if (!data) return;

    if (shortTermEl) shortTermEl.textContent = String(data.short_term_message_count);
    if (workingCountEl) workingCountEl.textContent = String(data.working.length);
    if (longTermCountEl) longTermCountEl.textContent = String(data.long_term.length);
    if (workingEl) renderMemoryEntries(workingEl, data.working);
    if (longTermEl) renderMemoryEntries(longTermEl, data.long_term);
}

async function loadChatTasks(chatId) {
    try {
        const data = await apiFetch(`/api/v1/chats/${chatId}/tasks`);
        state.lastTasks = data;
        renderTaskPanel();
    } catch (err) {
        console.error('Failed to load tasks:', err);
        showToast('Не удалось загрузить задачи. Проверьте соединение и попробуйте снова.', 'error');
    }
}

function formatTaskTimestamp(isoString) {
    return new Date(isoString).toLocaleString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function renderTaskHistory(container, history) {
    container.replaceChildren();
    history.forEach((entry) => {
        const line = document.createElement('div');

        if (entry.rejected) {
            line.className = 'text-red-400 line-through';
            let text;
            if (entry.from_state === entry.to_state) {
                text = `операция отклонена (${TASK_STATE_LABELS[entry.to_state]})`;
            } else {
                text = `попытка → ${TASK_STATE_LABELS[entry.to_state]}: отклонено`;
            }
            if (entry.rejection_reason) {
                text += ` (${entry.rejection_reason})`;
            }
            text += ` · ${formatTaskTimestamp(entry.created_at)}`;
            line.textContent = text;
        } else {
            const fromLabel = entry.from_state ? TASK_STATE_LABELS[entry.from_state] : 'создана';
            line.className = 'text-slate-500';
            line.textContent =
                `${fromLabel} → ${TASK_STATE_LABELS[entry.to_state]} · ${formatTaskTimestamp(entry.created_at)}`;
        }
        container.appendChild(line);

        if (entry.note) {
            const noteEl = document.createElement('div');
            noteEl.className = 'text-slate-600 pl-2';
            noteEl.textContent = entry.note;
            container.appendChild(noteEl);
        }
    });
}

function renderTaskPanel() {
    const tasks = state.lastTasks;
    if (tasks === null) return;
    const countEl = $('task-count');
    const listEl = $('task-list');
    if (countEl) countEl.textContent = String(tasks.length);
    if (!listEl) return;
    listEl.replaceChildren();

    if (!tasks.length) {
        const empty = document.createElement('div');
        empty.className = 'text-slate-600';
        empty.textContent = '—';
        listEl.appendChild(empty);

        const helper = document.createElement('div');
        helper.className = 'text-slate-600';
        helper.textContent = 'Задачи появятся здесь, когда агент создаст новую';
        listEl.appendChild(helper);
        return;
    }

    tasks.forEach((task) => {
        const card = document.createElement('div');
        card.className = 'rounded-lg bg-slate-800 px-2 py-1';

        const titleEl = document.createElement('div');
        titleEl.className = 'text-sm font-semibold text-slate-300';
        titleEl.textContent = task.title;
        card.appendChild(titleEl);

        const metaEl = document.createElement('div');
        metaEl.className = 'flex items-center gap-1';

        const stateChip = document.createElement('span');
        stateChip.className = `rounded px-1 ${TASK_STATE_BADGE_CLASSES[task.state]}`;
        stateChip.textContent = TASK_STATE_LABELS[task.state];
        metaEl.appendChild(stateChip);

        if (task.is_paused) {
            const pausedChip = document.createElement('span');
            pausedChip.className = 'text-slate-500';
            pausedChip.textContent = '⏸';
            metaEl.appendChild(pausedChip);
        }
        card.appendChild(metaEl);

        const goalEl = document.createElement('div');
        goalEl.className = 'text-slate-400 truncate';
        goalEl.textContent = task.goal;
        goalEl.title = task.goal;
        card.appendChild(goalEl);

        const historyEl = document.createElement('div');
        historyEl.className = 'mt-1 space-y-0.5';
        renderTaskHistory(historyEl, task.history || []);
        card.appendChild(historyEl);

        if (task.state !== 'done' && task.state !== 'cancelled') {
            const actionsEl = document.createElement('div');
            actionsEl.className = 'mt-1 flex gap-1';

            if (task.is_paused) {
                const resumeBtn = document.createElement('button');
                resumeBtn.type = 'button';
                resumeBtn.className =
                    'rounded-lg bg-indigo-600 hover:bg-indigo-500 px-2 py-1 text-xs font-medium transition';
                resumeBtn.textContent = 'Продолжить';
                resumeBtn.addEventListener('click', () => {
                    resumeTask(task.id).catch((err) => showToast(err.message, 'error'));
                });
                actionsEl.appendChild(resumeBtn);
            } else {
                const pauseBtn = document.createElement('button');
                pauseBtn.type = 'button';
                pauseBtn.className =
                    'rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 px-2 py-1 text-xs font-medium transition';
                pauseBtn.textContent = 'Пауза';
                pauseBtn.addEventListener('click', () => {
                    pauseTask(task.id).catch((err) => showToast(err.message, 'error'));
                });
                actionsEl.appendChild(pauseBtn);
            }

            const cancelBtn = document.createElement('button');
            cancelBtn.type = 'button';
            cancelBtn.className =
                'rounded-lg bg-red-700 hover:bg-red-600 px-2 py-1 text-xs font-medium transition';
            cancelBtn.textContent = 'Отменить';
            cancelBtn.addEventListener('click', () => {
                cancelTask(task.id).catch((err) => showToast(err.message, 'error'));
            });
            actionsEl.appendChild(cancelBtn);

            card.appendChild(actionsEl);
        }

        listEl.appendChild(card);
    });
}

async function pauseTask(taskId) {
    try {
        await apiFetch(`/api/v1/tasks/${taskId}/pause`, { method: 'POST' });
        await loadChatTasks(state.currentChatId);
        showToast('Задача поставлена на паузу', 'success');
    } catch (err) {
        showToast('Не удалось поставить задачу на паузу. Проверьте соединение и попробуйте снова.', 'error');
    }
}

async function resumeTask(taskId) {
    try {
        await apiFetch(`/api/v1/tasks/${taskId}/resume`, { method: 'POST' });
        await loadChatTasks(state.currentChatId);
        showToast('Задача возобновлена', 'success');
    } catch (err) {
        showToast('Не удалось возобновить задачу. Проверьте соединение и попробуйте снова.', 'error');
    }
}

async function cancelTask(taskId) {
    if (!confirm('Отменить эту задачу? Это действие нельзя отменить.')) return;
    try {
        await apiFetch(`/api/v1/tasks/${taskId}/cancel`, { method: 'POST' });
        await loadChatTasks(state.currentChatId);
        showToast('Задача отменена', 'success');
    } catch (err) {
        showToast('Не удалось отменить задачу. Проверьте соединение и попробуйте снова.', 'error');
    }
}

async function loadProfile() {
    try {
        const data = await apiFetch('/api/v1/profile');
        if (!data) return;
        state.lastProfile = data;
        renderProfilePanel();
    } catch (err) {
        console.error('Failed to load profile:', err);
    }
}

function renderProfilePanel() {
    const data = state.lastProfile;
    if (!data) return;
    $('profile-style').value = data.style;
    $('profile-format').value = data.format;
    $('profile-constraints').value = data.constraints;
}

async function saveProfile() {
    const body = {
        style: $('profile-style').value,
        format: $('profile-format').value,
        constraints: $('profile-constraints').value,
    };
    try {
        const data = await apiFetch('/api/v1/profile', { method: 'PUT', body: JSON.stringify(body) });
        state.lastProfile = data;
        showToast('Профиль сохранён', 'success');
    } catch (err) {
        showToast('Не удалось сохранить профиль. Проверьте соединение и попробуйте снова.', 'error');
    }
}

function setupFoldablePanels() {
    document.querySelectorAll('[data-fold-toggle]').forEach((btn) => {
        const targetId = btn.dataset.foldToggle;
        const body = document.getElementById(targetId);
        if (!body) return;
        btn.addEventListener('click', () => {
            const collapsed = body.classList.toggle('hidden');
            btn.textContent = collapsed ? '▸' : '▾';
            btn.setAttribute('aria-expanded', String(!collapsed));
        });
    });
}

async function loadInvariants() {
    try {
        const data = await apiFetch('/api/v1/invariants');
        state.lastGlobalInvariants = data;
        renderInvariantsPanel();
    } catch (err) {
        console.error('Failed to load invariants:', err);
        showToast('Не удалось загрузить инварианты. Проверьте соединение и попробуйте снова.', 'error');
    }
}

async function loadChatInvariants(chatId) {
    try {
        const data = await apiFetch(`/api/v1/chats/${chatId}/invariants`);
        state.lastChatInvariants = data;
        renderInvariantsPanel();
    } catch (err) {
        console.error('Failed to load chat invariants:', err);
        showToast('Не удалось загрузить инварианты. Проверьте соединение и попробуйте снова.', 'error');
    }
}

async function loadChatConflicts(chatId) {
    try {
        const data = await apiFetch(`/api/v1/chats/${chatId}/invariant-conflicts`);
        state.lastConflicts = data;
        renderConflictBadge();
        renderMessages();
    } catch (err) {
        console.error('Failed to load invariant conflicts:', err);
        showToast('Не удалось загрузить инварианты. Проверьте соединение и попробуйте снова.', 'error');
        state.lastConflicts = [];
    }
}

function renderConflictBadge() {
    const badge = $('invariant-conflict-badge');
    if (!badge) return;
    badge.textContent = String(state.lastConflicts.length);
    badge.classList.toggle('hidden', state.lastConflicts.length === 0);
}

function buildConflictBanner(conflict) {
    const wrapper = document.createElement('div');
    wrapper.className = 'flex justify-start';

    const bubble = document.createElement('div');
    bubble.className = 'max-w-[75%] rounded-xl border border-amber-700 bg-amber-900/50 px-4 py-2 text-sm';

    const heading = document.createElement('div');
    heading.className = 'text-amber-400 font-semibold';
    heading.textContent = '⚠️ Обнаружен конфликт с инвариантом';
    bubble.appendChild(heading);

    const body = document.createElement('div');
    body.className = 'message-content prose prose-invert prose-sm max-w-none mt-1';

    const titleEl = document.createElement('span');
    titleEl.textContent = `«${conflict.invariant_title}» — `;
    body.appendChild(titleEl);

    const noteEl = document.createElement('span');
    noteEl.innerHTML = renderMarkdown(conflict.note);
    body.appendChild(noteEl);

    bubble.appendChild(body);
    wrapper.appendChild(bubble);
    return wrapper;
}

function populateOverridesSelect() {
    const select = $('invariant-chat-overrides');
    if (!select) return;
    const previousValue = select.value;
    select.replaceChildren();

    const defaultOption = document.createElement('option');
    defaultOption.value = '';
    defaultOption.textContent = 'Не переопределяет';
    select.appendChild(defaultOption);

    const globals = state.lastGlobalInvariants || [];
    globals.forEach((item) => {
        const opt = document.createElement('option');
        opt.value = String(item.id);
        opt.textContent = item.title;
        select.appendChild(opt);
    });

    const stillExists = globals.some((item) => String(item.id) === previousValue);
    select.value = stillExists ? previousValue : '';
}

function renderInvariantsPanel() {
    const items = state.lastGlobalInvariants;
    if (items === null) return;
    const countEl = $('invariant-global-count');
    const listEl = $('invariant-global-list');
    if (countEl) countEl.textContent = String(items.length);
    if (!listEl) return;
    listEl.replaceChildren();

    if (!items.length) {
        const empty = document.createElement('div');
        empty.className = 'text-slate-600';
        empty.textContent = 'Глобальных инвариантов пока нет';
        listEl.appendChild(empty);

        const helper = document.createElement('div');
        helper.className = 'text-slate-600';
        helper.textContent = 'Добавьте первое правило — оно будет действовать во всех чатах';
        listEl.appendChild(helper);
        return;
    }

    items.forEach((item) => {
        const card = document.createElement('div');
        card.className = 'rounded-lg bg-slate-800 px-2 py-1';

        const titleEl = document.createElement('div');
        titleEl.className = 'text-sm font-semibold text-slate-300';
        titleEl.textContent = item.title;
        card.appendChild(titleEl);

        const ruleEl = document.createElement('div');
        ruleEl.className = 'text-slate-400';
        ruleEl.textContent = item.rule_text;
        card.appendChild(ruleEl);

        const actionsEl = document.createElement('div');
        actionsEl.className = 'flex items-center gap-2 mt-1';

        const editBtn = document.createElement('button');
        editBtn.type = 'button';
        editBtn.className = 'text-slate-400 hover:text-white';
        editBtn.textContent = 'Изменить';
        editBtn.addEventListener('click', () => {
            state.editingGlobalInvariantId = item.id;
            $('invariant-global-title').value = item.title;
            $('invariant-global-rule').value = item.rule_text;
            $('btn-save-global-invariant').textContent = 'Сохранить инвариант';
        });
        actionsEl.appendChild(editBtn);

        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'text-red-400 hover:text-red-300';
        deleteBtn.textContent = 'Удалить';
        deleteBtn.addEventListener('click', () => {
            deleteGlobalInvariant(item.id).catch((err) => showToast(err.message, 'error'));
        });
        actionsEl.appendChild(deleteBtn);

        card.appendChild(actionsEl);
        listEl.appendChild(card);
    });

    populateOverridesSelect();

    const chatItems = state.lastChatInvariants;
    const chatCountEl = $('invariant-chat-count');
    const chatListEl = $('invariant-chat-list');
    if (chatCountEl) chatCountEl.textContent = String((chatItems || []).length);
    if (chatListEl) {
        chatListEl.replaceChildren();
        if (!chatItems || !chatItems.length) {
            const empty = document.createElement('div');
            empty.className = 'text-slate-600';
            empty.textContent = 'У этого чата нет отдельных инвариантов';
            chatListEl.appendChild(empty);

            const helper = document.createElement('div');
            helper.className = 'text-slate-600';
            helper.textContent = 'Правила чата могут дополнять или переопределять глобальные';
            chatListEl.appendChild(helper);
        } else {
            chatItems.forEach((item) => {
                const card = document.createElement('div');
                card.className = 'rounded-lg bg-slate-800 px-2 py-1';

                const titleEl = document.createElement('div');
                titleEl.className = 'text-sm font-semibold text-slate-300';
                titleEl.textContent = item.title;
                card.appendChild(titleEl);

                const ruleEl = document.createElement('div');
                ruleEl.className = 'text-slate-400';
                ruleEl.textContent = item.rule_text;
                card.appendChild(ruleEl);

                if (item.overrides_title) {
                    const overrideEl = document.createElement('div');
                    overrideEl.className = 'text-amber-400';
                    overrideEl.textContent = `переопределяет: ${item.overrides_title}`;
                    card.appendChild(overrideEl);
                }

                const actionsEl = document.createElement('div');
                actionsEl.className = 'flex items-center gap-2 mt-1';

                const editBtn = document.createElement('button');
                editBtn.type = 'button';
                editBtn.className = 'text-slate-400 hover:text-white';
                editBtn.textContent = 'Изменить';
                editBtn.addEventListener('click', () => {
                    state.editingChatInvariantId = item.id;
                    $('invariant-chat-title').value = item.title;
                    $('invariant-chat-rule').value = item.rule_text;
                    const select = $('invariant-chat-overrides');
                    if (select) select.value = item.overrides_id ? String(item.overrides_id) : '';
                    $('btn-save-chat-invariant').textContent = 'Сохранить инвариант';
                });
                actionsEl.appendChild(editBtn);

                const deleteBtn = document.createElement('button');
                deleteBtn.type = 'button';
                deleteBtn.className = 'text-red-400 hover:text-red-300';
                deleteBtn.textContent = 'Удалить';
                deleteBtn.addEventListener('click', () => {
                    deleteChatInvariant(item.id).catch((err) => showToast(err.message, 'error'));
                });
                actionsEl.appendChild(deleteBtn);

                card.appendChild(actionsEl);
                chatListEl.appendChild(card);
            });
        }
    }
}

async function deleteChatInvariant(invariantId) {
    if (!state.currentChatId) return;
    if (!confirm('Удалить этот инвариант? Это действие нельзя отменить.')) return;
    try {
        await apiFetch(`/api/v1/chats/${state.currentChatId}/invariants/${invariantId}`, {
            method: 'DELETE',
        });
        showToast('Инвариант удалён', 'success');
        await loadChatInvariants(state.currentChatId);
    } catch (err) {
        showToast('Не удалось удалить инвариант. Проверьте соединение и попробуйте снова.', 'error');
    }
}

async function saveChatInvariant() {
    const title = $('invariant-chat-title').value.trim();
    const ruleText = $('invariant-chat-rule').value.trim();
    if (!title || !ruleText || !state.currentChatId) {
        showToast('Заполните название и текст правила', 'error');
        return;
    }
    const select = $('invariant-chat-overrides');
    const overridesId = select && select.value !== '' ? Number(select.value) : null;
    const body = { title, rule_text: ruleText, overrides_id: overridesId };
    try {
        if (state.editingChatInvariantId === null) {
            await apiFetch(`/api/v1/chats/${state.currentChatId}/invariants`, {
                method: 'POST',
                body: JSON.stringify(body),
            });
        } else {
            await apiFetch(
                `/api/v1/chats/${state.currentChatId}/invariants/${state.editingChatInvariantId}`,
                { method: 'PUT', body: JSON.stringify(body) },
            );
        }
        $('invariant-chat-title').value = '';
        $('invariant-chat-rule').value = '';
        if (select) select.value = '';
        state.editingChatInvariantId = null;
        $('btn-save-chat-invariant').textContent = 'Добавить инвариант чата';
        showToast('Инвариант сохранён', 'success');
        await loadChatInvariants(state.currentChatId);
    } catch (err) {
        showToast('Не удалось сохранить инвариант. Проверьте соединение и попробуйте снова.', 'error');
    }
}

async function deleteGlobalInvariant(invariantId) {
    if (!confirm('Удалить этот инвариант? Это действие нельзя отменить.')) return;
    try {
        await apiFetch(`/api/v1/invariants/${invariantId}`, { method: 'DELETE' });
        showToast('Инвариант удалён', 'success');
        await loadInvariants();
    } catch (err) {
        showToast('Не удалось удалить инвариант. Проверьте соединение и попробуйте снова.', 'error');
    }
}

async function saveGlobalInvariant() {
    const title = $('invariant-global-title').value.trim();
    const ruleText = $('invariant-global-rule').value.trim();
    if (!title || !ruleText) {
        showToast('Заполните название и текст правила', 'error');
        return;
    }
    const body = { title, rule_text: ruleText };
    try {
        if (state.editingGlobalInvariantId === null) {
            await apiFetch('/api/v1/invariants', { method: 'POST', body: JSON.stringify(body) });
        } else {
            await apiFetch(`/api/v1/invariants/${state.editingGlobalInvariantId}`, {
                method: 'PUT',
                body: JSON.stringify(body),
            });
        }
        $('invariant-global-title').value = '';
        $('invariant-global-rule').value = '';
        state.editingGlobalInvariantId = null;
        $('btn-save-global-invariant').textContent = 'Добавить инвариант';
        showToast('Инвариант сохранён', 'success');
        await loadInvariants();
    } catch (err) {
        showToast('Не удалось сохранить инвариант. Проверьте соединение и попробуйте снова.', 'error');
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
        const resp = await fetch(`${AGENT_BASE}/health`, {
            signal: AbortSignal.timeout(2000),
            credentials: 'include',
        });
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
    state.lastConflicts = [];
    const chat = state.chats.find((c) => c.id === chatId);
    $('chat-title').textContent = chat?.title || 'Чат';
    renderChatList();
    await loadChatTree(chatId);
    await loadChatStats(chatId);
    await loadChatMemory(chatId);
    await loadChatTasks(chatId);
    await loadChatInvariants(chatId);
    await loadChatConflicts(chatId);
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
            if (state.currentChatId) {
                loadChatTree(state.currentChatId);
                loadChatMemory(state.currentChatId);
                loadChatTasks(state.currentChatId);
                loadChatInvariants(state.currentChatId);
                loadChatConflicts(state.currentChatId);
            }
            if (data.invariant_conflict) {
                showToast('⚠️ Обнаружен конфликт с инвариантом', 'warning');
            }
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

function openAddUserModal() {
    $('add-user-username').value = '';
    $('add-user-password').value = '';
    $('add-user-error').textContent = '';
    $('add-user-modal').classList.remove('hidden');
    $('add-user-username').focus();
}

function closeAddUserModal() {
    $('add-user-modal').classList.add('hidden');
}

async function createUser(event) {
    event.preventDefault();
    const username = $('add-user-username').value;
    const password = $('add-user-password').value;
    $('add-user-error').textContent = '';
    try {
        await apiFetch('/api/v1/auth/users', {
            method: 'POST',
            body: JSON.stringify({ username, password }),
        });
        closeAddUserModal();
        showToast(`Пользователь ${username} создан`, 'success');
    } catch (err) {
        $('add-user-error').textContent = err.message;
    }
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
    $('btn-logout').addEventListener('click', () => logout().catch((e) => showToast(e.message, 'error')));
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
    $('btn-users').addEventListener('click', openAddUserModal);
    $('btn-close-add-user').addEventListener('click', closeAddUserModal);
    $('btn-cancel-add-user').addEventListener('click', closeAddUserModal);
    $('add-user-form').addEventListener('submit', (e) => {
        createUser(e).catch((err) => showToast(err.message, 'error'));
    });
    $('add-user-modal').addEventListener('click', (e) => {
        if (e.target === $('add-user-modal')) closeAddUserModal();
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
        if (e.key === 'Escape') {
            closeSettingsModal();
            closeAddUserModal();
        }
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
    $('btn-save-profile').addEventListener('click', () => {
        saveProfile().catch((err) => showToast(err.message, 'error'));
    });
    $('btn-save-global-invariant').addEventListener('click', () => {
        saveGlobalInvariant().catch((err) => showToast(err.message, 'error'));
    });
    $('btn-save-chat-invariant').addEventListener('click', () => {
        saveChatInvariant().catch((err) => showToast(err.message, 'error'));
    });
    setupFoldablePanels();
}

async function init() {
    bindEvents();
    await checkAgentHealth();
    setInterval(checkAgentHealth, 5000);
    await loadModels();
    await loadProfile();
    await loadInvariants();
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
