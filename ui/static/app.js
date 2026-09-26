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
    pendingMessage: null,
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
    pendingToolCalls: [],
    toolCallsByMessage: {},
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
        if (!isUser) {
            (state.toolCallsByMessage[msg.id] || []).forEach((evt) => {
                container.appendChild(wrapToolCard(buildToolCallCard(evt)));
            });
        }
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
    const ctxWindow = stats?.context_window_size ?? state.contextWindow ?? 16384;
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

function appendUserBubble(content) {
    const container = $('messages');
    const wrapper = document.createElement('div');
    wrapper.className = 'flex justify-end';
    const bubble = document.createElement('div');
    bubble.className = 'max-w-[75%] rounded-xl px-4 py-2 text-sm bg-indigo-600 text-white';
    const contentEl = document.createElement('div');
    contentEl.className = 'message-content prose prose-invert prose-sm max-w-none';
    contentEl.innerHTML = DOMPurify.sanitize(content);
    bubble.appendChild(contentEl);
    wrapper.appendChild(bubble);
    container.appendChild(wrapper);
    container.scrollTop = container.scrollHeight;
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

function buildToolCallCard(evt) {
    const card = document.createElement('details');
    card.className = 'tool-call-card max-w-[75%] rounded-lg border border-slate-700 '
        + 'bg-slate-900 text-xs text-slate-300 px-3 py-2';
    if (evt.tool_call_id) card.dataset.toolCallId = evt.tool_call_id;

    const summary = document.createElement('summary');
    summary.className = `cursor-pointer select-none ${evt.ok ? '' : 'text-red-400'}`;
    summary.textContent = '🔧 ' + (evt.server ? evt.server + ' · ' : '')
        + (evt.tool || evt.name || '?') + (evt.ok ? ' — ok' : ' — ошибка');
    card.appendChild(summary);

    const argsLabel = document.createElement('div');
    argsLabel.className = 'mt-2 text-slate-500';
    argsLabel.textContent = 'Аргументы';
    const argsPre = document.createElement('pre');
    argsPre.className = 'whitespace-pre-wrap break-all';
    argsPre.textContent = evt.arguments || '{}';

    const resultLabel = document.createElement('div');
    resultLabel.className = `mt-2 ${evt.ok ? 'text-slate-500' : 'text-red-400'}`;
    resultLabel.textContent = (evt.ok ? 'Результат' : 'Ошибка') + (evt.truncated ? ' (обрезано)' : '');
    const resultPre = document.createElement('pre');
    resultPre.className = 'whitespace-pre-wrap break-all';
    resultPre.textContent = evt.result || '';

    card.append(argsLabel, argsPre, resultLabel, resultPre);
    return card;
}

function wrapToolCard(card) {
    const wrapper = document.createElement('div');
    wrapper.className = 'flex justify-start';
    wrapper.appendChild(card);
    return wrapper;
}

function appendToolCallCard(evt) {
    const container = $('messages');
    const wrapper = wrapToolCard(buildToolCallCard(evt));
    const streaming = container.querySelector('[data-streaming="true"]');
    if (streaming) {
        container.insertBefore(wrapper, streaming);
    } else {
        container.appendChild(wrapper);
    }
    container.scrollTop = container.scrollHeight;
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
        trySendPending();
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
        case 'tool_call':
            state.pendingToolCalls.push(data);
            appendToolCallCard(data);
            break;
        case 'done':
            if (data.message_id && state.pendingToolCalls.length) {
                state.toolCallsByMessage[data.message_id] = state.pendingToolCalls;
            }
            state.pendingToolCalls = [];
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
            if (data.code !== 'TOOL_ERROR') state.pendingToolCalls = [];
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

function trySendPending() {
    if (!state.pendingMessage) return;
    if (!state.currentChatId) return;
    if (state.isStreaming) return;
    if (!state.selectedModel) return;
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;

    const queued = state.pendingMessage;
    state.pendingMessage = null;
    sendMessage(queued).catch((err) => showToast(err.message, 'error'));
}

async function sendMessage(content) {
    if (!state.currentChatId || !content.trim() || state.isStreaming) return;

    if (!state.selectedModel) {
        state.pendingMessage = content.trim();
        showToast('Модель ещё загружается — сообщение будет отправлено автоматически', 'info');
        return;
    }

    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
        state.pendingMessage = content.trim();
        showToast('Переподключение к серверу — сообщение будет отправлено автоматически', 'info');
        connectWs(state.currentChatId);
        return;
    }

    const trimmed = content.trim();
    state.lastFailedMessage = trimmed;

    setStreaming(true);
    appendUserBubble(trimmed);
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
    trySendPending();
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

// ---- MCP servers (Phase 7) ----

const MCP_STATUS_BADGE_CLASSES = {
    not_connected: 'text-slate-400',
    connecting: 'text-sky-400',
    connected: 'text-emerald-400',
    error: 'text-red-400',
};

const MCP_STATUS_LABELS = {
    not_connected: 'не подключён',
    connecting: 'подключение…',
    connected: 'подключено',
    error: 'ошибка',
};

const MCP_ENV_MASK = '•••';

const MCP_NEUTRAL_BTN_CLASSES = 'px-2 py-1 text-xs rounded bg-slate-800 hover:bg-slate-700 border border-slate-700 disabled:opacity-50 disabled:cursor-not-allowed';
const MCP_PRE_CLASSES = 'text-xs text-slate-400 bg-black/30 rounded-md p-2 overflow-x-auto whitespace-pre-wrap';

state.mcpServers = [];
state.mcpEditingId = null;
state.mcpConnecting = new Set();
state.mcpSaving = false;
state.mcpExpanded = new Set();

function mcpEl(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
}

async function loadMcpServers() {
    state.mcpServers = await apiFetch('/api/v1/mcp/servers') || [];
    renderMcpServers();
}

function replaceMcpServer(updated) {
    const idx = state.mcpServers.findIndex((s) => s.id === updated.id);
    if (idx === -1) {
        state.mcpServers.push(updated);
    } else {
        state.mcpServers[idx] = updated;
    }
}

function bindMcpFold(button, body, key, caret) {
    const apply = (open) => {
        body.classList.toggle('hidden', !open);
        button.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (caret) caret.textContent = open ? '▾' : '▸';
    };
    apply(state.mcpExpanded.has(key));
    button.addEventListener('click', () => {
        const open = body.classList.contains('hidden');
        apply(open);
        if (open) {
            state.mcpExpanded.add(key);
        } else {
            state.mcpExpanded.delete(key);
        }
    });
}

function describeMcpParams(schema) {
    const props = schema && typeof schema.properties === 'object' && schema.properties ? schema.properties : {};
    const required = schema && Array.isArray(schema.required) ? schema.required : [];
    return Object.keys(props).map((name) => {
        const spec = props[name] && typeof props[name] === 'object' ? props[name] : {};
        const type = Array.isArray(spec.type) ? spec.type.join('|') : (spec.type || 'any');
        return {
            name,
            type: String(type),
            required: required.includes(name),
            description: typeof spec.description === 'string' ? spec.description : '',
        };
    });
}

function renderMcpParamLine(param) {
    const line = mcpEl('div', 'flex flex-wrap items-baseline gap-2');
    line.appendChild(mcpEl('span', 'font-mono text-xs', param.name));
    line.appendChild(mcpEl('span', 'text-slate-500 text-xs', param.type));
    if (param.required) line.appendChild(mcpEl('span', 'text-red-400 text-xs', '*'));
    if (param.description) line.appendChild(mcpEl('span', 'text-xs text-slate-400', param.description));
    return line;
}

async function copyMcpToolName(name) {
    try {
        await navigator.clipboard.writeText(name);
    } catch (err) {
        // navigator.clipboard is unavailable outside secure contexts (e.g. opened via a LAN IP).
        const area = document.createElement('textarea');
        area.value = name;
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.select();
        const copied = document.execCommand('copy');
        area.remove();
        if (!copied) {
            showToast('Не удалось скопировать в буфер обмена', 'error');
            return;
        }
    }
    showToast(`Скопировано: ${name}`, 'success');
}

function renderMcpToolRow(server, tool) {
    const toolKey = `tool-${server.id}-${tool.name}`;
    const jsonKey = `json-${server.id}-${tool.name}`;
    const row = mcpEl('div', 'rounded-md bg-slate-900/60 border border-slate-700 p-2');

    const toggle = mcpEl('button', 'w-full text-left flex items-center gap-2');
    toggle.type = 'button';
    const caret = mcpEl('span', 'text-slate-500 text-xs', '▸');
    toggle.appendChild(caret);
    const nameEl = mcpEl('span', 'text-sm font-semibold cursor-copy hover:text-indigo-300', tool.name);
    nameEl.title = 'Нажмите, чтобы скопировать имя';
    nameEl.addEventListener('click', (event) => {
        event.stopPropagation();
        copyMcpToolName(tool.name);
    });
    toggle.appendChild(nameEl);
    toggle.appendChild(mcpEl('span', 'text-xs text-slate-400 truncate min-w-0 flex-1', tool.description || ''));
    row.appendChild(toggle);

    const body = mcpEl('div', 'mt-2 space-y-1 hidden');
    const params = describeMcpParams(tool.input_schema);
    params.forEach((param) => body.appendChild(renderMcpParamLine(param)));

    const jsonRow = mcpEl('div', 'flex justify-end');
    const jsonBtn = mcpEl('button', 'text-xs text-slate-400 hover:text-white', 'JSON');
    jsonBtn.type = 'button';
    jsonRow.appendChild(jsonBtn);
    body.appendChild(jsonRow);
    const jsonPre = mcpEl('pre', MCP_PRE_CLASSES, JSON.stringify(tool.input_schema, null, 2));
    body.appendChild(jsonPre);
    bindMcpFold(jsonBtn, jsonPre, jsonKey, null);

    row.appendChild(body);
    bindMcpFold(toggle, body, toolKey, caret);
    return row;
}

function renderMcpTools(server) {
    const tools = server.connection.tools || [];
    const wrap = mcpEl('div', 'space-y-2');
    const key = `tools-${server.id}`;

    const header = mcpEl('button', 'flex items-center gap-2 text-left');
    header.type = 'button';
    const caret = mcpEl('span', 'text-slate-500 text-xs', '▸');
    header.appendChild(caret);
    header.appendChild(mcpEl('span', 'text-sm font-semibold', `Инструменты (${tools.length})`));
    wrap.appendChild(header);

    const list = mcpEl('div', 'space-y-2 hidden');
    tools.forEach((tool) => list.appendChild(renderMcpToolRow(server, tool)));
    wrap.appendChild(list);
    bindMcpFold(header, list, key, caret);
    return wrap;
}

function renderMcpError(server) {
    const conn = server.connection;
    const wrap = mcpEl('div', 'space-y-2');
    wrap.appendChild(mcpEl('p', 'text-red-400 text-sm', conn.error_message || ''));
    if (conn.detail) wrap.appendChild(mcpEl('p', 'text-xs text-slate-500', conn.detail));
    if (conn.stderr_tail) {
        const key = `stderr-${server.id}`;
        const toggle = mcpEl('button', 'flex items-center gap-2 text-left');
        toggle.type = 'button';
        const caret = mcpEl('span', 'text-slate-500 text-xs', '▸');
        toggle.appendChild(caret);
        toggle.appendChild(mcpEl('span', 'text-xs text-slate-400', 'Показать журнал сервера (stderr)'));
        wrap.appendChild(toggle);
        const pre = mcpEl('pre', MCP_PRE_CLASSES, conn.stderr_tail);
        wrap.appendChild(pre);
        bindMcpFold(toggle, pre, key, caret);
    }
    return wrap;
}

function renderMcpServerRow(server) {
    const conn = server.connection;
    const connecting = state.mcpConnecting.has(server.id);
    const status = connecting ? 'connecting' : conn.status;
    const card = mcpEl('div', 'rounded-lg bg-slate-800 border border-slate-700 p-3 space-y-2');

    const header = mcpEl('div', 'flex flex-wrap items-center justify-between gap-2');
    const title = mcpEl('div', 'flex items-center gap-2 min-w-0');
    title.appendChild(mcpEl('span', 'text-sm font-semibold truncate', server.name));
    title.appendChild(mcpEl('span', `text-xs ${MCP_STATUS_BADGE_CLASSES[status] || 'text-slate-400'}`, MCP_STATUS_LABELS[status] || status));
    header.appendChild(title);

    const actions = mcpEl('div', 'flex items-center gap-2');
    if (conn.status === 'connected') {
        const btn = mcpEl('button', MCP_NEUTRAL_BTN_CLASSES, 'Отключить');
        btn.type = 'button';
        btn.disabled = connecting;
        btn.addEventListener('click', () => {
            disconnectMcpServer(server).catch((err) => showToast(err.message, 'error'));
        });
        actions.appendChild(btn);
    } else {
        const btn = mcpEl('button', 'px-2 py-1 text-xs rounded bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed', 'Подключить');
        btn.type = 'button';
        btn.disabled = connecting || !server.enabled;
        if (!server.enabled) btn.classList.add('opacity-50', 'cursor-not-allowed');
        btn.addEventListener('click', () => {
            connectMcpServer(server);
        });
        actions.appendChild(btn);
    }
    const editBtn = mcpEl('button', MCP_NEUTRAL_BTN_CLASSES, 'Изменить');
    editBtn.type = 'button';
    editBtn.disabled = connecting;
    editBtn.addEventListener('click', () => openMcpForm(server));
    actions.appendChild(editBtn);
    const delBtn = mcpEl('button', 'px-2 py-1 text-xs text-red-400 hover:text-red-300 disabled:opacity-50 disabled:cursor-not-allowed', 'Удалить');
    delBtn.type = 'button';
    delBtn.disabled = connecting;
    delBtn.addEventListener('click', () => {
        deleteMcpServer(server).catch((err) => showToast(err.message, 'error'));
    });
    actions.appendChild(delBtn);
    header.appendChild(actions);
    card.appendChild(header);

    if (!connecting && conn.status === 'connected') {
        const info = conn.server_info;
        if (info) {
            card.appendChild(mcpEl('p', 'text-xs text-slate-500', `${info.name} · v${info.version} · MCP ${info.protocol_version}`));
        }
        card.appendChild(renderMcpTools(server));
    } else if (!connecting && conn.status === 'error') {
        card.appendChild(renderMcpError(server));
    }
    return card;
}

function renderMcpServers() {
    const listEl = $('mcp-server-list');
    $('mcp-empty').classList.toggle('hidden', state.mcpServers.length > 0);
    listEl.textContent = '';
    state.mcpServers.forEach((server) => listEl.appendChild(renderMcpServerRow(server)));
}

function openMcpForm(server) {
    state.mcpEditingId = server ? server.id : null;
    $('mcp-name').value = server ? server.name : '';
    $('mcp-command').value = server ? server.command : '';
    $('mcp-args').value = server ? (server.args || []).join('\n') : '';
    $('mcp-env').value = server ? (server.env_keys || []).map((k) => `${k}=${MCP_ENV_MASK}`).join('\n') : '';
    $('mcp-cwd').value = server && server.cwd ? server.cwd : '';
    $('mcp-enabled').checked = server ? server.enabled : true;
    $('mcp-form-error').textContent = '';
    $('mcp-server-form').classList.remove('hidden');
    $('mcp-name').focus();
}

function closeMcpForm() {
    state.mcpEditingId = null;
    $('mcp-server-form').classList.add('hidden');
    $('mcp-form-error').textContent = '';
    ['mcp-name', 'mcp-command', 'mcp-args', 'mcp-env', 'mcp-cwd'].forEach((id) => { $(id).value = ''; });
    $('mcp-enabled').checked = true;
}

function parseMcpArgs(text) {
    return text.split('\n').map((line) => line.replace(/\r$/, '')).filter((line) => line !== '');
}

function parseMcpEnv(text) {
    const env = {};
    text.split('\n').forEach((rawLine, idx) => {
        const line = rawLine.replace(/\r$/, '');
        if (line.trim() === '') return;
        const eq = line.indexOf('=');
        const key = eq === -1 ? '' : line.slice(0, eq).trim();
        if (!key) throw new Error(`Строка ${idx + 1}: ожидается KEY=VALUE`);
        env[key] = line.slice(eq + 1);
    });
    return env;
}

async function saveMcpServer() {
    if (state.mcpSaving) return;
    const saveBtn = $('btn-mcp-save');
    state.mcpSaving = true;
    saveBtn.disabled = true;
    try {
        const name = $('mcp-name').value.trim();
        const command = $('mcp-command').value.trim();
        const errorEl = $('mcp-form-error');
        errorEl.textContent = '';
        if (!name || !command) {
            errorEl.textContent = 'Заполните название и команду';
            return;
        }
        let env;
        try {
            env = parseMcpEnv($('mcp-env').value);
        } catch (err) {
            errorEl.textContent = err.message;
            return;
        }
        const body = {
            name,
            command,
            args: parseMcpArgs($('mcp-args').value),
            env,
            cwd: $('mcp-cwd').value.trim() || null,
            enabled: $('mcp-enabled').checked,
        };
        const editingId = state.mcpEditingId;
        const previous = editingId === null ? null : state.mcpServers.find((s) => s.id === editingId);
        const wasConnected = Boolean(previous && previous.connection.status === 'connected');
        try {
            if (editingId === null) {
                await apiFetch('/api/v1/mcp/servers', { method: 'POST', body: JSON.stringify(body) });
            } else {
                await apiFetch(`/api/v1/mcp/servers/${editingId}`, { method: 'PUT', body: JSON.stringify(body) });
            }
        } catch (err) {
            errorEl.textContent = err.message;
            return;
        }
        if (wasConnected) {
            showToast(`Сервер «${name}» отключён из-за изменения настроек. Подключите заново.`, 'info');
        } else {
            showToast('Сервер сохранён', 'success');
        }
        closeMcpForm();
        await loadMcpServers();
    } finally {
        state.mcpSaving = false;
        saveBtn.disabled = false;
    }
}

async function deleteMcpServer(server) {
    const message = server.connection.status === 'connected'
        ? `Сервер «${server.name}» подключён и будет отключён перед удалением. Продолжить?`
        : `Удалить сервер «${server.name}»? Это действие нельзя отменить.`;
    if (!confirm(message)) return;
    await apiFetch(`/api/v1/mcp/servers/${server.id}`, { method: 'DELETE' });
    if (state.mcpEditingId === server.id) closeMcpForm();
    showToast('Сервер удалён', 'success');
    await loadMcpServers();
}

async function connectMcpServer(server) {
    state.mcpConnecting.add(server.id);
    renderMcpServers();
    try {
        const updated = await apiFetch(`/api/v1/mcp/servers/${server.id}/connect`, { method: 'POST' });
        if (updated) {
            replaceMcpServer(updated);
            const conn = updated.connection;
            if (conn.status === 'connected') {
                showToast(`Подключено: ${(conn.tools || []).length} инструментов`, 'success');
            } else if (conn.status === 'error') {
                showToast(conn.error_message || 'Не удалось подключиться к серверу', 'error');
            }
        }
    } catch (err) {
        showToast(err.message, 'error');
    } finally {
        state.mcpConnecting.delete(server.id);
        renderMcpServers();
    }
}

async function disconnectMcpServer(server) {
    const updated = await apiFetch(`/api/v1/mcp/servers/${server.id}/disconnect`, { method: 'POST' });
    if (updated) replaceMcpServer(updated);
    renderMcpServers();
}

// ---- end MCP servers ----

// ---- Scheduler ----

const SCHEDULER_JOB_STATUS_LABELS = {
    active: 'активно',
    paused: 'на паузе',
    completed: 'завершено',
    cancelled: 'отменено',
};

const SCHEDULER_JOB_STATUS_CLASSES = {
    active: 'text-sky-400',
    paused: 'text-amber-400',
    completed: 'text-emerald-400',
    cancelled: 'text-slate-500',
};

const SCHEDULER_RUN_STATUS_LABELS = {
    running: 'выполняется',
    success: 'успешно',
    failed: 'ошибка',
    skipped: 'пропущено',
};

const SCHEDULER_RUN_STATUS_CLASSES = {
    running: 'text-sky-400',
    success: 'text-emerald-400',
    failed: 'text-red-400',
    skipped: 'text-slate-400',
};

const SCHEDULER_LIVE_STATUSES = new Set(['active', 'paused']);
const SCHEDULER_ACTION_ERROR = 'Не удалось выполнить действие. Проверьте соединение и попробуйте снова.';
const SCHEDULER_NEUTRAL_BTN_CLASSES = 'rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 px-2 py-1 text-xs font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed';
const SCHEDULER_ACCENT_BTN_CLASSES = 'rounded-lg bg-indigo-600 hover:bg-indigo-500 px-2 py-1 text-xs font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed';
const SCHEDULER_DESTRUCTIVE_BTN_CLASSES = 'rounded-lg bg-red-700 hover:bg-red-600 px-2 py-1 text-xs font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed';
const SCHEDULER_LINK_BTN_CLASSES = 'text-xs text-red-400 hover:text-red-300 disabled:opacity-50 disabled:cursor-not-allowed';
const SCHEDULER_FOCUS_CLASSES = 'focus:outline-none focus:ring-2 focus:ring-indigo-500';

state.lastSchedulerTasks = null;
state.schedulerExpanded = new Set();
state.schedulerRuns = new Map();
state.schedulerBusy = new Set();

function parseSchedulerDate(iso) {
    const text = String(iso);
    return new Date(/(Z|[+-]\d{2}:?\d{2})$/.test(text) ? text : `${text}Z`);
}

function formatSchedulerTimestamp(iso, withSeconds = false) {
    if (!iso) return '—';
    const options = { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' };
    if (withSeconds) options.second = '2-digit';
    return parseSchedulerDate(iso).toLocaleString('ru-RU', options).replace(',', '');
}

function formatSchedulerDuration(ms) {
    if (ms === null || ms === undefined) return '';
    const totalSeconds = Math.max(0, Math.round(ms / 1000));
    if (totalSeconds < 60) return `${totalSeconds} с`;
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return seconds ? `${minutes} мин ${seconds} с` : `${minutes} мин`;
}

function formatSchedulerInterval(seconds) {
    if (seconds % 3600 === 0) return `Каждые ${seconds / 3600} ч`;
    if (seconds % 60 === 0) return `Каждые ${seconds / 60} мин`;
    return `Каждые ${seconds} сек`;
}

function formatSchedulerRunCount(task) {
    return task.max_runs
        ? ` · запусков: ${task.run_count}/${task.max_runs}`
        : ` · запусков: ${task.run_count}`;
}

function formatSchedulerSchedule(task) {
    let head;
    if (task.schedule_type === 'once') {
        head = `Однократно · ${formatSchedulerTimestamp(task.run_at || task.next_run_at)}`;
    } else if (task.schedule_type === 'interval') {
        head = formatSchedulerInterval(task.interval_seconds);
    } else {
        head = `Cron: ${task.cron}`;
    }
    return head + formatSchedulerRunCount(task);
}

function schedulerStatusChip(labels, classes, status) {
    const chip = mcpEl('span', `inline-flex items-center rounded px-1 text-xs ${classes[status] || 'text-slate-400'}`);
    if (status === 'running') {
        chip.appendChild(mcpEl('span', 'inline-block w-2 h-2 mr-1 rounded-full bg-sky-400 animate-pulse motion-reduce:animate-none'));
    }
    chip.appendChild(document.createTextNode(labels[status] || String(status)));
    return chip;
}

function schedulerRunChip(status) {
    return schedulerStatusChip(SCHEDULER_RUN_STATUS_LABELS, SCHEDULER_RUN_STATUS_CLASSES, status);
}

function schedulerLateChip() {
    return mcpEl('span', 'rounded px-1 text-xs text-amber-400', 'с опозданием');
}

function compareSchedulerTasks(a, b) {
    const liveA = SCHEDULER_LIVE_STATUSES.has(a.status) ? 0 : 1;
    const liveB = SCHEDULER_LIVE_STATUSES.has(b.status) ? 0 : 1;
    if (liveA !== liveB) return liveA - liveB;
    const nullA = a.next_run_at ? 0 : 1;
    const nullB = b.next_run_at ? 0 : 1;
    if (nullA !== nullB) return nullA - nullB;
    if (a.next_run_at && b.next_run_at) {
        const diff = parseSchedulerDate(a.next_run_at) - parseSchedulerDate(b.next_run_at);
        if (diff !== 0) return diff;
    }
    return parseSchedulerDate(b.created_at) - parseSchedulerDate(a.created_at);
}

function sortSchedulerTasks(tasks) {
    return tasks.slice().sort(compareSchedulerTasks);
}

async function loadSchedulerTasks(silent = false) {
    try {
        const data = await apiFetch('/api/v1/scheduler/tasks');
        if (!data) return;
        state.lastSchedulerTasks = sortSchedulerTasks(data);
        const knownIds = new Set(data.map((task) => task.id));
        state.schedulerExpanded.forEach((id) => {
            if (!knownIds.has(id)) state.schedulerExpanded.delete(id);
        });
        renderSchedulerPanel();
        state.schedulerExpanded.forEach((id) => {
            loadSchedulerRuns(id, true);
        });
    } catch (err) {
        console.error('Failed to load scheduler tasks:', err);
        if (!silent) {
            showToast('Не удалось загрузить расписание. Проверьте соединение и попробуйте снова.', 'error');
        }
    }
}

async function loadSchedulerRuns(taskId, silent = false) {
    try {
        const runs = await apiFetch(`/api/v1/scheduler/tasks/${taskId}/runs?limit=20`);
        if (!runs) return;
        state.schedulerRuns.set(taskId, runs.slice(0, 20));
        renderSchedulerPanel();
    } catch (err) {
        console.error('Failed to load scheduler runs:', err);
        if (!silent) showToast(err.message || SCHEDULER_ACTION_ERROR, 'error');
    }
}

function bindSchedulerFold(button, body, taskId, caret) {
    const apply = (open) => {
        body.classList.toggle('hidden', !open);
        button.setAttribute('aria-expanded', open ? 'true' : 'false');
        caret.textContent = open ? '▾' : '▸';
    };
    apply(state.schedulerExpanded.has(taskId));
    button.addEventListener('click', () => {
        const open = body.classList.contains('hidden');
        apply(open);
        if (open) {
            state.schedulerExpanded.add(taskId);
            loadSchedulerRuns(taskId);
        } else {
            state.schedulerExpanded.delete(taskId);
        }
    });
}

function renderSchedulerRunRow(run) {
    const row = mcpEl('button', `w-full text-left rounded bg-slate-900 hover:bg-slate-700 px-2 py-1 flex items-center justify-between gap-2 ${SCHEDULER_FOCUS_CLASSES}`);
    row.type = 'button';
    row.appendChild(mcpEl('span', 'text-slate-500', formatSchedulerTimestamp(run.started_at || run.scheduled_for, true)));

    const middle = mcpEl('span', 'flex items-center gap-1');
    middle.appendChild(schedulerRunChip(run.status));
    if (run.is_late) middle.appendChild(schedulerLateChip());
    row.appendChild(middle);

    const showDuration = run.status !== 'running' && run.status !== 'skipped';
    row.appendChild(mcpEl('span', 'text-slate-500', showDuration ? formatSchedulerDuration(run.duration_ms) : ''));
    row.addEventListener('click', () => openSchedulerRunModal(run.id, row));
    return row;
}

function renderSchedulerHistory(container, taskId) {
    container.replaceChildren();
    const runs = state.schedulerRuns.get(taskId);
    if (!runs) return;
    if (!runs.length) {
        container.appendChild(mcpEl('div', 'text-slate-600', 'Запусков пока не было'));
        return;
    }
    runs.forEach((run) => container.appendChild(renderSchedulerRunRow(run)));
}

async function runSchedulerAction(task, button, action) {
    if (state.schedulerBusy.has(task.id)) return;
    state.schedulerBusy.add(task.id);
    button.disabled = true;
    try {
        await action();
    } catch (err) {
        showToast(err.message || SCHEDULER_ACTION_ERROR, 'error');
    } finally {
        state.schedulerBusy.delete(task.id);
        button.disabled = false;
    }
}

function schedulerPause(task, button) {
    return runSchedulerAction(task, button, async () => {
        await apiFetch(`/api/v1/scheduler/tasks/${task.id}/pause`, { method: 'POST' });
        await loadSchedulerTasks();
        showToast('Задание поставлено на паузу', 'success');
    });
}

function schedulerResume(task, button) {
    return runSchedulerAction(task, button, async () => {
        await apiFetch(`/api/v1/scheduler/tasks/${task.id}/resume`, { method: 'POST' });
        await loadSchedulerTasks();
        showToast('Задание возобновлено', 'success');
    });
}

function schedulerRunNow(task, button) {
    return runSchedulerAction(task, button, async () => {
        await apiFetch(`/api/v1/scheduler/tasks/${task.id}/run`, { method: 'POST' });
        showToast('Запуск начат', 'info');
        await loadSchedulerTasks();
    });
}

function schedulerCancel(task, button) {
    if (!confirm(`Отменить задание «${task.title}»? Будущие запуски не состоятся, история запусков сохранится.`)) {
        return Promise.resolve();
    }
    return runSchedulerAction(task, button, async () => {
        await apiFetch(`/api/v1/scheduler/tasks/${task.id}/cancel`, { method: 'POST' });
        await loadSchedulerTasks();
        showToast('Задание отменено', 'success');
    });
}

function schedulerDelete(task, button) {
    if (!confirm(`Удалить задание «${task.title}» и всю историю его запусков? Это действие нельзя отменить.`)) {
        return Promise.resolve();
    }
    return runSchedulerAction(task, button, async () => {
        await apiFetch(`/api/v1/scheduler/tasks/${task.id}`, { method: 'DELETE' });
        state.schedulerExpanded.delete(task.id);
        state.schedulerRuns.delete(task.id);
        await loadSchedulerTasks();
        showToast('Задание удалено', 'success');
    });
}

function schedulerButton(label, className, handler) {
    const button = mcpEl('button', className, label);
    button.type = 'button';
    button.addEventListener('click', () => {
        handler(button);
    });
    return button;
}

function renderSchedulerActions(task) {
    const actions = mcpEl('div', 'mt-1 flex flex-wrap items-center gap-1');
    const live = SCHEDULER_LIVE_STATUSES.has(task.status);
    if (live) {
        const runBtn = schedulerButton('Запустить сейчас', SCHEDULER_NEUTRAL_BTN_CLASSES, (btn) => schedulerRunNow(task, btn));
        if (task.is_running) {
            runBtn.disabled = true;
            runBtn.title = 'Задание уже выполняется';
        }
        actions.appendChild(runBtn);
        if (task.status === 'active') {
            actions.appendChild(schedulerButton('Пауза', SCHEDULER_NEUTRAL_BTN_CLASSES, (btn) => schedulerPause(task, btn)));
        } else {
            actions.appendChild(schedulerButton('Продолжить', SCHEDULER_ACCENT_BTN_CLASSES, (btn) => schedulerResume(task, btn)));
        }
        actions.appendChild(schedulerButton('Отменить', SCHEDULER_DESTRUCTIVE_BTN_CLASSES, (btn) => schedulerCancel(task, btn)));
    }
    actions.appendChild(schedulerButton('Удалить', SCHEDULER_LINK_BTN_CLASSES, (btn) => schedulerDelete(task, btn)));
    return actions;
}

function renderSchedulerScheduleRow(task) {
    const row = mcpEl('div', 'text-slate-400');
    if (task.schedule_type === 'cron') {
        row.appendChild(document.createTextNode('Cron: '));
        row.appendChild(mcpEl('span', 'font-mono', task.cron));
        row.appendChild(document.createTextNode(formatSchedulerRunCount(task)));
    } else {
        row.textContent = formatSchedulerSchedule(task);
    }
    return row;
}

function renderSchedulerCard(task) {
    const card = mcpEl('div', 'rounded-lg bg-slate-800 px-2 py-1');
    const expanded = state.schedulerExpanded.has(task.id);

    const header = mcpEl('button', `w-full flex items-center justify-between gap-2 text-left rounded ${SCHEDULER_FOCUS_CLASSES}`);
    header.type = 'button';
    header.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    const left = mcpEl('span', 'flex items-center gap-1 min-w-0');
    const caret = mcpEl('span', 'text-slate-400', expanded ? '▾' : '▸');
    const title = mcpEl('span', 'text-sm font-semibold text-slate-300 truncate', task.title);
    title.title = task.title;
    left.append(caret, title);
    const right = mcpEl('span', 'flex items-center gap-1 shrink-0');
    right.appendChild(schedulerStatusChip(SCHEDULER_JOB_STATUS_LABELS, SCHEDULER_JOB_STATUS_CLASSES, task.status));
    if (task.is_running) right.appendChild(schedulerRunChip('running'));
    header.append(left, right);
    card.appendChild(header);

    card.appendChild(renderSchedulerScheduleRow(task));

    const nextRow = mcpEl('div', 'flex flex-wrap items-center gap-1 text-slate-500');
    const nextText = task.status === 'active' && task.next_run_at ? formatSchedulerTimestamp(task.next_run_at) : '—';
    nextRow.appendChild(mcpEl('span', '', `Следующий: ${nextText}`));
    if (task.last_run) {
        nextRow.appendChild(mcpEl('span', '', 'Последний:'));
        nextRow.appendChild(schedulerRunChip(task.last_run.status));
    }
    card.appendChild(nextRow);

    const history = mcpEl('div', 'mt-1 space-y-1');
    renderSchedulerHistory(history, task.id);
    card.appendChild(history);
    bindSchedulerFold(header, history, task.id, caret);

    card.appendChild(renderSchedulerActions(task));
    return card;
}

function renderSchedulerPanel() {
    const tasks = state.lastSchedulerTasks;
    if (tasks === null) return;
    const countEl = $('scheduler-count');
    const badgeEl = $('scheduler-running-badge');
    const listEl = $('scheduler-list');
    if (countEl) {
        countEl.textContent = String(tasks.filter((task) => SCHEDULER_LIVE_STATUSES.has(task.status)).length);
    }
    if (badgeEl) {
        const running = tasks.filter((task) => task.is_running).length;
        badgeEl.textContent = `${running} выполняется`;
        badgeEl.classList.toggle('hidden', running === 0);
    }
    if (!listEl) return;
    listEl.replaceChildren();

    if (!tasks.length) {
        const empty = mcpEl('div', 'text-center py-2 space-y-1');
        empty.appendChild(mcpEl('p', 'text-sm font-semibold text-slate-300', 'Заданий пока нет'));
        empty.appendChild(mcpEl('p', 'text-slate-500', 'Создайте задание кнопкой выше или попросите агента в чате, например: «через минуту прочитай файл и перескажи его».'));
        listEl.appendChild(empty);
        return;
    }
    tasks.forEach((task) => listEl.appendChild(renderSchedulerCard(task)));
}

const SCHEDULER_INTERVAL_UNIT_SECONDS = { seconds: 1, minutes: 60, hours: 3600 };
const SCHEDULER_SKIPPED_TEXT = 'Запуск пропущен: предыдущий запуск ещё выполнялся';
const SCHEDULER_ERROR_BOX_CLASSES = 'bg-red-900/30 border border-red-700 rounded-lg p-3 text-sm text-red-400';
const SCHEDULER_TRACE_LABEL_CLASSES = 'text-xs text-slate-500';

state.schedulerModalOpener = null;
state.schedulerCreating = false;
state.schedulerOpenRunId = null;

function hideSchedulerModal(modalId) {
    const modal = $(modalId);
    if (!modal || modal.classList.contains('hidden')) return false;
    modal.classList.add('hidden');
    if (modalId === 'scheduler-run-modal') state.schedulerOpenRunId = null;
    return true;
}

function closeSchedulerModal(modalId) {
    if (!hideSchedulerModal(modalId)) return;
    const opener = state.schedulerModalOpener;
    state.schedulerModalOpener = null;
    if (opener && opener.isConnected) opener.focus();
}

function closeSchedulerCreateModal() {
    closeSchedulerModal('scheduler-create-modal');
}

function closeSchedulerRunModal() {
    closeSchedulerModal('scheduler-run-modal');
}

function populateSchedulerModelSelect() {
    const select = $('scheduler-model');
    select.replaceChildren();
    if (!state.models.length) {
        const empty = mcpEl('option', '', 'Нет моделей');
        empty.value = '';
        empty.disabled = true;
        empty.selected = true;
        select.appendChild(empty);
        return;
    }
    state.models.forEach((model) => {
        const option = mcpEl('option', '', model.id);
        option.value = model.id;
        select.appendChild(option);
    });
    const preselected = state.models.some((m) => m.id === state.selectedModel)
        ? state.selectedModel
        : state.models[0].id;
    select.value = preselected;
}

function updateSchedulerTypeFields() {
    const type = $('scheduler-type').value;
    $('scheduler-once-fields').classList.toggle('hidden', type !== 'once');
    $('scheduler-interval-fields').classList.toggle('hidden', type !== 'interval');
    $('scheduler-cron-fields').classList.toggle('hidden', type !== 'cron');
    $('scheduler-max-runs-field').classList.toggle('hidden', type === 'once');
}

function updateSchedulerOnceMode() {
    const checked = document.querySelector('input[name="scheduler-once-mode"]:checked');
    const mode = checked ? checked.value : 'delay';
    $('scheduler-delay').disabled = mode !== 'delay';
    $('scheduler-run-at').disabled = mode !== 'at';
}

function openSchedulerCreateModal(opener) {
    hideSchedulerModal('scheduler-run-modal');
    state.schedulerModalOpener = opener || null;
    $('scheduler-create-form').reset();
    $('scheduler-create-error').textContent = '';
    populateSchedulerModelSelect();
    updateSchedulerTypeFields();
    updateSchedulerOnceMode();
    $('scheduler-create-modal').classList.remove('hidden');
    $('scheduler-title').focus();
}

function buildSchedulerScheduleFields(type) {
    const invalid = new Error('Укажите корректное расписание');
    if (type === 'once') {
        const checked = document.querySelector('input[name="scheduler-once-mode"]:checked');
        if (checked && checked.value === 'at') {
            const runAt = $('scheduler-run-at').value;
            if (!runAt) throw invalid;
            return { run_at: runAt };
        }
        const delay = parseInt($('scheduler-delay').value, 10);
        if (!(delay >= 1)) throw invalid;
        return { delay_seconds: delay };
    }
    if (type === 'interval') {
        const value = parseInt($('scheduler-interval-value').value, 10);
        if (!(value >= 1)) throw invalid;
        return { interval_seconds: value * SCHEDULER_INTERVAL_UNIT_SECONDS[$('scheduler-interval-unit').value] };
    }
    const cron = $('scheduler-cron').value.trim();
    if (!cron) throw invalid;
    return { cron };
}

function buildSchedulerCreateBody() {
    const title = $('scheduler-title').value.trim();
    const prompt = $('scheduler-prompt').value.trim();
    if (!title || !prompt) throw new Error('Заполните название и промпт');
    const type = $('scheduler-type').value;
    const model = $('scheduler-model').value;
    if (!model) throw new Error('Выберите модель');
    const body = { title, prompt, model, schedule_type: type, ...buildSchedulerScheduleFields(type) };
    const maxRuns = parseInt($('scheduler-max-runs').value, 10);
    if (type !== 'once' && maxRuns >= 1) body.max_runs = maxRuns;
    return body;
}

async function submitSchedulerCreate(event) {
    event.preventDefault();
    if (state.schedulerCreating) return;
    const errorEl = $('scheduler-create-error');
    const submitBtn = $('btn-submit-scheduler-create');
    errorEl.textContent = '';
    let body;
    try {
        body = buildSchedulerCreateBody();
    } catch (err) {
        errorEl.textContent = err.message;
        return;
    }
    state.schedulerCreating = true;
    submitBtn.disabled = true;
    try {
        try {
            await apiFetch('/api/v1/scheduler/tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
        } catch (err) {
            errorEl.textContent = err.message;
            return;
        }
        closeSchedulerCreateModal();
        await loadSchedulerTasks();
        showToast('Задание создано', 'success');
    } finally {
        state.schedulerCreating = false;
        submitBtn.disabled = false;
    }
}

function formatSchedulerTraceValue(value) {
    return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

function hasSchedulerTraceValue(value) {
    if (value === null || value === undefined || value === '') return false;
    if (typeof value === 'object') return Object.keys(value).length > 0;
    return true;
}

function renderSchedulerTraceEntry(entry) {
    const item = mcpEl('div', 'space-y-1');
    const head = mcpEl('div', 'flex items-center gap-2');
    head.appendChild(mcpEl('span', 'font-mono text-xs text-slate-300', String(entry.name || '')));
    if (entry.ok === false) head.appendChild(mcpEl('span', 'text-xs text-red-400', 'ошибка'));
    item.appendChild(head);
    [['Аргументы', entry.arguments], ['Результат', entry.result !== undefined ? entry.result : entry.content]].forEach(([label, value]) => {
        if (!hasSchedulerTraceValue(value)) return;
        item.appendChild(mcpEl('div', SCHEDULER_TRACE_LABEL_CLASSES, label));
        item.appendChild(mcpEl('pre', MCP_PRE_CLASSES, formatSchedulerTraceValue(value)));
    });
    return item;
}

function renderSchedulerTrace(trace) {
    const wrap = mcpEl('div', 'space-y-2');
    const toggle = mcpEl('button', `text-xs text-slate-400 hover:text-white rounded ${SCHEDULER_FOCUS_CLASSES}`, '▸ Показать ход выполнения');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', 'false');
    const list = mcpEl('div', 'hidden space-y-2');
    trace.forEach((entry) => list.appendChild(renderSchedulerTraceEntry(entry)));
    toggle.addEventListener('click', () => {
        const open = list.classList.contains('hidden');
        list.classList.toggle('hidden', !open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        toggle.textContent = open ? '▾ Скрыть ход выполнения' : '▸ Показать ход выполнения';
    });
    wrap.append(toggle, list);
    return wrap;
}

function renderSchedulerRunMeta(detail) {
    const meta = $('scheduler-run-meta');
    meta.replaceChildren();
    meta.appendChild(schedulerRunChip(detail.status));
    meta.appendChild(mcpEl('span', '', `начало ${formatSchedulerTimestamp(detail.started_at || detail.scheduled_for, true)}`));
    if (detail.status !== 'running' && detail.status !== 'skipped' && detail.duration_ms !== null && detail.duration_ms !== undefined) {
        meta.appendChild(mcpEl('span', '', formatSchedulerDuration(detail.duration_ms)));
    }
    if (detail.is_late) meta.appendChild(schedulerLateChip());
}

function renderSchedulerRunBody(detail) {
    const body = $('scheduler-run-body');
    body.replaceChildren();
    if (detail.status === 'running') {
        const line = mcpEl('div', 'flex items-center text-sm text-slate-300');
        line.appendChild(mcpEl('span', 'inline-block w-2 h-2 mr-2 rounded-full bg-sky-400 animate-pulse motion-reduce:animate-none'));
        line.appendChild(document.createTextNode('Задание выполняется…'));
        body.appendChild(line);
        return;
    }
    if (detail.status === 'skipped') {
        body.appendChild(mcpEl('p', 'text-sm text-slate-300', SCHEDULER_SKIPPED_TEXT));
        return;
    }
    if (detail.status === 'failed') {
        body.appendChild(mcpEl('div', SCHEDULER_ERROR_BOX_CLASSES, `Ошибка выполнения: ${detail.error || 'неизвестная ошибка'}`));
    }
    if (detail.status === 'success' || detail.result_text) {
        const section = mcpEl('section', 'space-y-1');
        section.appendChild(mcpEl('h3', 'text-xs font-semibold text-slate-300', 'Результат'));
        const content = mcpEl('div', 'message-content text-sm');
        content.innerHTML = renderMarkdown(detail.result_text || '');
        section.appendChild(content);
        body.appendChild(section);
    }
    const trace = Array.isArray(detail.tool_trace) ? detail.tool_trace : [];
    if (trace.length) body.appendChild(renderSchedulerTrace(trace));
}

function renderSchedulerRunModal(detail) {
    $('scheduler-run-title').textContent = detail.task_title || '';
    renderSchedulerRunMeta(detail);
    renderSchedulerRunBody(detail);
}

async function openSchedulerRunModal(runId, opener) {
    let detail;
    try {
        detail = await apiFetch(`/api/v1/scheduler/runs/${runId}`);
    } catch (err) {
        showToast(err.message || SCHEDULER_ACTION_ERROR, 'error');
        return;
    }
    if (!detail) return;
    hideSchedulerModal('scheduler-create-modal');
    state.schedulerModalOpener = opener || null;
    state.schedulerOpenRunId = runId;
    renderSchedulerRunModal(detail);
    $('scheduler-run-modal').classList.remove('hidden');
    $('btn-close-scheduler-run').focus();
}

async function refreshSchedulerRunModal(runId) {
    try {
        const detail = await apiFetch(`/api/v1/scheduler/runs/${runId}`);
        if (detail && state.schedulerOpenRunId === runId) renderSchedulerRunModal(detail);
    } catch (err) {
        console.error('Failed to refresh scheduler run:', err);
    }
}

function bindSchedulerModals() {
    $('btn-scheduler-new').addEventListener('click', (e) => openSchedulerCreateModal(e.currentTarget));
    const form = $('scheduler-create-form');
    form.noValidate = true;
    form.addEventListener('submit', (e) => {
        submitSchedulerCreate(e).catch((err) => showToast(err.message, 'error'));
    });
    $('scheduler-type').addEventListener('change', updateSchedulerTypeFields);
    document.querySelectorAll('input[name="scheduler-once-mode"]').forEach((radio) => {
        radio.addEventListener('change', updateSchedulerOnceMode);
    });
    $('btn-close-scheduler-create').addEventListener('click', closeSchedulerCreateModal);
    $('btn-cancel-scheduler-create').addEventListener('click', closeSchedulerCreateModal);
    $('btn-close-scheduler-run').addEventListener('click', closeSchedulerRunModal);
    $('scheduler-create-modal').addEventListener('click', (e) => {
        if (e.target === $('scheduler-create-modal')) closeSchedulerCreateModal();
    });
    $('scheduler-run-modal').addEventListener('click', (e) => {
        if (e.target === $('scheduler-run-modal')) closeSchedulerRunModal();
    });
}

// ---- end Scheduler ----

async function openSettingsModal() {
    const perChat = $('settings-per-chat').checked;
    const chatId = perChat && state.currentChatId ? state.currentChatId : null;
    const query = chatId ? `?chat_id=${chatId}` : '';
    const settings = await apiFetch(`/api/v1/settings${query}`);

    $('settings-strategy').value = settings.strategy;

    const contextLength = settings.context_length || 16384;
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
    loadMcpServers().catch((err) => showToast(err.message, 'error'));
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
            closeSchedulerCreateModal();
            closeSchedulerRunModal();
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
    $('btn-mcp-add').addEventListener('click', () => openMcpForm(null));
    $('btn-mcp-cancel').addEventListener('click', closeMcpForm);
    $('btn-mcp-save').addEventListener('click', () => {
        saveMcpServer().catch((err) => showToast(err.message, 'error'));
    });
    setupFoldablePanels();
    bindSchedulerModals();
    const schedulerFold = document.querySelector('[data-fold-toggle="scheduler-panel-body"]');
    if (schedulerFold) {
        schedulerFold.addEventListener('click', () => {
            if (!$('scheduler-panel-body').classList.contains('hidden')) loadSchedulerTasks();
        });
    }
}

async function init() {
    bindEvents();
    await checkAgentHealth();
    setInterval(checkAgentHealth, 5000);
    await loadModels();
    await loadProfile();
    await loadInvariants();
    await loadSchedulerTasks();
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
