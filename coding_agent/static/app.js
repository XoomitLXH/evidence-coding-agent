const state = {
  workspace: "",
  files: [],
  mode: "execute",
  plugins: [],
  selectedPlugins: [],
  activeTask: null,
  history: [],
  source: null,
  activeFile: null,
  savedContent: "",
  dirty: false,
  saving: false,
  executing: false,
  activeStreamMessage: null,
  pendingAgentFile: null,
};

const byId = (id) => document.getElementById(id);
const conversation = byId("conversation");
const taskInput = byId("task-input");
const sendTask = byId("send-task");
const pluginToggle = byId("plugin-toggle");
const pluginMenu = byId("plugin-menu");
const selectedPluginList = byId("selected-plugin-list");
const taskState = byId("task-state");
const toast = byId("toast");
const editorInput = byId("editor-input");
const editorHighlight = byId("editor-highlight");
const editorHighlightCode = editorHighlight.querySelector("code");
const saveFile = byId("save-file");
const runFile = byId("run-file");
const debugFile = byId("debug-file");
const editorNotice = byId("editor-notice");
const editorNoticeText = byId("editor-notice-text");
const editorNoticeAction = byId("editor-reload");
const sessionList = byId("session-list");

function isConversationNearBottom() {
  return conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 72;
}

function scrollConversationIfFollowing(force = false) {
  if (force || isConversationNearBottom()) conversation.scrollTop = conversation.scrollHeight;
}

function icon(name) {
  return `<i data-lucide="${name}"></i>`;
}

function pluginIconFallbackMarkup(plugin) {
  const label = String(plugin?.display_name || plugin?.name || "?").trim();
  const initials = label
    .split(/[\s_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0))
    .join("")
    .toUpperCase() || "?";
  const color = String(plugin?.brand_color || "").trim();
  const style = /^#[0-9a-f]{6}$/i.test(color) ? ` style="--plugin-color:${escapeHtml(color)}"` : "";
  return `<span class="plugin-icon-initials"${style} aria-hidden="true">${escapeHtml(initials)}</span>`;
}

function pluginIconMarkup(plugin) {
  const source = String(plugin?.icon_url || "").trim();
  const fallback = String(plugin?.icon_fallback_url || "").trim();
  if (!source) return `<span class="plugin-icon-fallback">${pluginIconFallbackMarkup(plugin)}</span>`;
  return `<img class="plugin-icon-image" src="${escapeHtml(source)}" data-fallback="${escapeHtml(fallback)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer"><span class="plugin-icon-fallback hidden">${pluginIconFallbackMarkup(plugin)}</span>`;
}

function wirePluginIcons() {
  document.querySelectorAll(".plugin-icon-image").forEach((image) => {
    if (image.dataset.iconWired) return;
    image.dataset.iconWired = "1";
    image.addEventListener("error", () => {
      const fallback = image.dataset.fallback || "";
      if (fallback && !image.dataset.fallbackTried) {
        image.dataset.fallbackTried = "1";
        image.src = fallback;
        return;
      }
      image.classList.add("hidden");
      const fallbackNode = image.nextElementSibling;
      if (fallbackNode) fallbackNode.classList.remove("hidden");
      refreshIcons();
    });
  });
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons();
  wirePluginIcons();
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[character]));
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => toast.classList.add("hidden"), 4200);
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error || "请求失败");
    error.payload = payload;
    throw error;
  }
  return payload;
}

function setTaskState(status, label) {
  taskState.className = `task-state ${status}`;
  taskState.textContent = label;
}

function switchTab(name) {
  document.querySelectorAll(".tab-button").forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${name}`));
}

function setEditorStatus(status, label) {
  const target = byId("editor-status");
  target.className = `editor-status ${status}`;
  target.textContent = label;
}

function isRunnableFile(path) {
  return typeof path === "string" && path.toLowerCase().endsWith(".py");
}

function updateEditorState() {
  state.dirty = Boolean(state.activeFile) && editorInput.value !== state.savedContent;
  const locked = state.saving || state.executing;
  const runnable = isRunnableFile(state.activeFile);
  saveFile.disabled = !state.activeFile || locked;
  runFile.disabled = !state.activeFile || !runnable || locked;
  debugFile.disabled = !state.activeFile || !runnable || locked;
  if (!state.activeFile) {
    setEditorStatus("inactive", "只读");
  } else if (!runnable) {
    setEditorStatus("inactive", "仅支持 Python");
  } else if (state.executing) {
    setEditorStatus("saving", "执行中");
  } else if (state.saving) {
    setEditorStatus("saving", "保存中");
  } else if (state.dirty) {
    setEditorStatus("dirty", "未保存");
  } else {
    setEditorStatus("saved", "已保存");
  }
}

const pythonKeywords = new Set([
  "and", "as", "assert", "async", "await", "break", "class", "continue", "def",
  "del", "elif", "else", "except", "False", "finally", "for", "from", "global",
  "if", "import", "in", "is", "lambda", "None", "nonlocal", "not", "or", "pass",
  "raise", "return", "True", "try", "while", "with", "yield",
]);
const pythonTypes = new Set([
  "bool", "bytes", "dict", "float", "frozenset", "int", "list", "object", "set",
  "str", "tuple", "type",
]);

function highlightPython(source) {
  let html = "";
  let position = 0;
  let nextWordIsFunctionName = false;

  // This small tokenizer handles the common Python syntax shown in the editor.
  while (position < source.length) {
    const character = source[position];
    if (character === "#") {
      const end = source.indexOf("\n", position);
      const comment = source.slice(position, end === -1 ? source.length : end);
      html += `<span class="syntax-comment">${escapeHtml(comment)}</span>`;
      position += comment.length;
      continue;
    }

    if (character === "'" || character === '"') {
      const quote = character;
      const triple = source.slice(position, position + 3) === quote.repeat(3);
      const delimiter = triple ? quote.repeat(3) : quote;
      let end = position + delimiter.length;
      while (end < source.length) {
        if (triple && source.slice(end, end + 3) === delimiter) {
          end += 3;
          break;
        }
        if (!triple && source[end] === quote) {
          end += 1;
          break;
        }
        end += source[end] === "\\" ? 2 : 1;
      }
      html += `<span class="syntax-string">${escapeHtml(source.slice(position, end))}</span>`;
      position = end;
      continue;
    }

    if (/[A-Za-z_]/.test(character)) {
      const match = source.slice(position).match(/^[A-Za-z_][A-Za-z0-9_]*/);
      const word = match ? match[0] : character;
      let className = "";
      if (nextWordIsFunctionName) {
        className = "syntax-function";
        nextWordIsFunctionName = false;
      } else if (pythonKeywords.has(word)) {
        className = "syntax-keyword";
        nextWordIsFunctionName = word === "def";
      } else if (pythonTypes.has(word)) {
        className = "syntax-type";
      }
      html += className ? `<span class="${className}">${escapeHtml(word)}</span>` : escapeHtml(word);
      position += word.length;
      continue;
    }

    if (/[0-9]/.test(character)) {
      const match = source.slice(position).match(/^[0-9][0-9A-Za-z_.]*/);
      const number = match ? match[0] : character;
      html += `<span class="syntax-number">${escapeHtml(number)}</span>`;
      position += number.length;
      continue;
    }

    html += escapeHtml(character);
    position += 1;
  }
  return html;
}

function syncEditorHighlightScroll() {
  editorHighlightCode.style.transform = `translate(${-editorInput.scrollLeft}px, ${-editorInput.scrollTop}px)`;
}

function renderEditorHighlight() {
  const showHighlight = isRunnableFile(state.activeFile);
  editorHighlight.classList.toggle("hidden", !showHighlight);
  editorInput.classList.toggle("plain-editor", !showHighlight);
  if (!showHighlight) return;
  editorHighlightCode.innerHTML = highlightPython(editorInput.value) || " ";
  syncEditorHighlightScroll();
}

function clearEditorNotice() {
  state.pendingAgentFile = null;
  editorNoticeText.textContent = "智能体修改了此文件，未保存草稿仍保留。";
  editorNoticeAction.textContent = "重新加载";
  editorNotice.classList.add("hidden");
}

function showAgentFileConflict(changedPath) {
  state.pendingAgentFile = changedPath;
  if (changedPath === state.activeFile) {
    editorNoticeText.textContent = `智能体已更新 ${changedPath}，你的未保存编辑仍保留。`;
    editorNoticeAction.textContent = "重新加载";
  } else {
    editorNoticeText.textContent = `智能体已写入 ${changedPath}，先保存 ${state.activeFile} 后可打开它。`;
    editorNoticeAction.textContent = "保存并打开";
  }
  editorNotice.classList.remove("hidden");
}

async function loadEditorFile(path, { focus = false } = {}) {
  const file = await fetchJson(`/api/file?path=${encodeURIComponent(path)}&raw=1`);
  state.activeFile = file.path;
  state.savedContent = file.content;
  editorInput.value = file.content;
  editorInput.disabled = false;
  editorInput.classList.remove("hidden");
  byId("file-empty").classList.add("hidden");
  byId("editor-path").textContent = file.path;
  clearEditorNotice();
  renderEditorHighlight();
  updateEditorState();
  if (focus) editorInput.focus();
}

function renderTree(entries, parent, depth = 0) {
  entries.forEach((entry) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `file-row indent-${Math.min(depth, 2)}`;
    row.innerHTML = `${icon(entry.type === "dir" ? "folder" : "file-code-2")}<span class="file-label">${escapeHtml(entry.name)}</span>`;
    parent.appendChild(row);
    const path = entry.path || entry.name;
    if (entry.type === "dir") {
      let expanded = false;
      const children = document.createElement("div");
      children.className = "tree-children";
      row.addEventListener("click", async () => {
        if (expanded) {
          children.replaceChildren();
          children.remove();
          expanded = false;
          return;
        }
        try {
          const payload = await fetchJson(`/api/tree?path=${encodeURIComponent(path)}`);
          renderTree(payload.entries.map((item) => ({ ...item, path: path === "." ? item.name : `${path}/${item.name}` })), children, depth + 1);
          row.after(children);
          expanded = true;
          refreshIcons();
        } catch (error) { showToast(error.message); }
      });
    } else {
      state.files.push(path);
      row.addEventListener("click", () => openFile(path));
    }
  });
}

async function loadTree() {
  const [payload, references] = await Promise.all([
    fetchJson("/api/bootstrap"),
    fetchJson("/api/references"),
  ]);
  state.workspace = payload.workspace;
  state.files = references.files;
  state.plugins = Array.isArray(payload.plugins?.plugins) ? payload.plugins.plugins : [];
  byId("workspace-path").textContent = payload.workspace;
  byId("file-tree").replaceChildren();
  renderTree(payload.tree.map((item) => ({ ...item, path: item.name })), byId("file-tree"));
  renderPluginMenu();
  renderSelectedPlugins();
  const modelStatus = byId("model-status");
  modelStatus.textContent = payload.model.model_ready ? `${payload.model.name} 已配置` : "模型未配置";
  modelStatus.className = `status-dot ${payload.model.model_ready ? "ready" : "error"}`;
  refreshIcons();
}

function closePluginMenu() {
  pluginMenu.classList.add("hidden");
  pluginToggle.setAttribute("aria-expanded", "false");
}

function renderSelectedPlugins() {
  selectedPluginList.replaceChildren();
  const selected = state.selectedPlugins
    .map((name) => state.plugins.find((plugin) => plugin.name === name))
    .filter(Boolean);
  state.selectedPlugins = selected.map((plugin) => plugin.name);
  selectedPluginList.classList.toggle("hidden", selected.length === 0);
  selected.forEach((plugin) => {
    const chip = document.createElement("span");
    chip.className = "selected-plugin-chip";
    if (plugin.brand_color) chip.style.setProperty("--plugin-color", plugin.brand_color);
    chip.innerHTML = `${pluginIconMarkup(plugin)}<span>${escapeHtml(plugin.display_name || plugin.name)}</span>`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "selected-plugin-remove";
    remove.title = `移除插件：${plugin.display_name || plugin.name}`;
    remove.setAttribute("aria-label", remove.title);
    remove.innerHTML = icon("x");
    remove.addEventListener("click", () => {
      state.selectedPlugins = state.selectedPlugins.filter((name) => name !== plugin.name);
      renderPluginMenu();
      renderSelectedPlugins();
    });
    chip.appendChild(remove);
    selectedPluginList.appendChild(chip);
  });
  refreshIcons();
}

function renderPluginMenu() {
  pluginMenu.replaceChildren();
  const heading = document.createElement("div");
  heading.className = "plugin-menu-heading";
  heading.textContent = "插件";
  pluginMenu.appendChild(heading);
  if (!state.plugins.length) {
    const empty = document.createElement("div");
    empty.className = "plugin-menu-empty";
    empty.textContent = "暂无可用插件";
    pluginMenu.appendChild(empty);
    return;
  }
  state.plugins.forEach((plugin) => {
    const selected = state.selectedPlugins.includes(plugin.name);
    const option = document.createElement("button");
    option.type = "button";
    option.className = `plugin-option${selected ? " selected" : ""}`;
    option.setAttribute("role", "menuitemcheckbox");
    option.setAttribute("aria-checked", String(selected));
    option.dataset.plugin = plugin.name;
    if (plugin.brand_color) option.style.setProperty("--plugin-color", plugin.brand_color);
    const count = Number(plugin.skill_count || 0);
    option.innerHTML = `<span class="plugin-option-icon">${pluginIconMarkup(plugin)}</span><span class="plugin-option-copy"><strong>${escapeHtml(plugin.display_name || plugin.name)}</strong><small>${escapeHtml(plugin.short_description || plugin.description || `${count} 个技能`)}</small></span><span class="plugin-option-check">${icon(selected ? "check" : "plus")}</span>`;
    option.addEventListener("click", (event) => {
      event.stopPropagation();
      if (state.selectedPlugins.includes(plugin.name)) {
        state.selectedPlugins = state.selectedPlugins.filter((name) => name !== plugin.name);
      } else {
        state.selectedPlugins = [...state.selectedPlugins, plugin.name];
      }
      renderPluginMenu();
      renderSelectedPlugins();
    });
    pluginMenu.appendChild(option);
  });
  refreshIcons();
}

function togglePluginMenu() {
  const opening = pluginMenu.classList.contains("hidden");
  if (opening) {
    renderPluginMenu();
    pluginMenu.classList.remove("hidden");
    pluginToggle.setAttribute("aria-expanded", "true");
  } else {
    closePluginMenu();
  }
}

function taskTitle(task) {
  const prompt = String(task && task.task || "").replace(/\s+/g, " ").trim();
  return prompt.length > 46 ? `${prompt.slice(0, 46)}…` : (prompt || "未命名任务");
}

function taskStatusLabel(status) {
  return {
    queued: "排队中",
    pending: "等待中",
    running: "执行中",
    complete: "已完成",
    error: "执行失败",
    incomplete: "未完成",
    interrupted: "已中断",
    awaiting_approval: "等待命令审批",
    review_required: "等待草稿审阅",
  }[status] || "未知状态";
}

function taskTime(task) {
  const raw = task && (task.finished_at || task.created_at);
  if (!raw) return "时间未知";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function isResumableTask(task) {
  return ["interrupted", "error", "incomplete"].includes(task && task.status);
}

function renderTaskHistory() {
  if (!sessionList) return;
  sessionList.replaceChildren();
  if (!state.history.length) {
    const empty = document.createElement("div");
    empty.className = "session-empty";
    empty.textContent = "暂无历史任务";
    sessionList.appendChild(empty);
    return;
  }
  state.history.forEach((task) => {
    const item = document.createElement("div");
    item.className = `session-item${state.activeTask === task.id ? " active" : ""}`;
    item.dataset.taskId = task.id;
    item.tabIndex = 0;
    item.setAttribute("role", "button");
    item.setAttribute("aria-label", `打开任务：${taskTitle(task)}`);
    item.innerHTML = `<div class="session-item-main">${icon("message-square")}<div class="session-item-copy"><div class="session-item-title">${escapeHtml(taskTitle(task))}</div><div class="session-item-meta"><span class="session-item-status status-${escapeHtml(task.status || "unknown")}">${escapeHtml(taskStatusLabel(task.status))}</span><span>${escapeHtml(taskTime(task))}</span><span>${Number(task.event_count || 0)} 个事件</span></div></div></div>`;
    item.addEventListener("click", () => openHistoricalTask(task.id));
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openHistoricalTask(task.id);
      }
    });
    if (isResumableTask(task)) {
      const actions = document.createElement("div");
      actions.className = "session-item-actions";
      const resume = document.createElement("button");
      resume.className = "resume-task";
      resume.type = "button";
      resume.title = "恢复任务";
      resume.textContent = "恢复";
      resume.addEventListener("click", (event) => {
        event.stopPropagation();
        resumeHistoricalTask(task.id);
      });
      actions.appendChild(resume);
      item.appendChild(actions);
    }
    sessionList.appendChild(item);
  });
  refreshIcons();
}

async function loadTaskHistory() {
  try {
    const payload = await fetchJson("/api/tasks");
    state.history = Array.isArray(payload.tasks) ? payload.tasks : [];
    renderTaskHistory();
    return state.history;
  } catch (error) {
    showToast(`任务历史加载失败：${error.message}`);
    return [];
  }
}

function resetTaskSurface() {
  conversation.replaceChildren();
  byId("terminal-output").innerHTML = '<span class="terminal-muted">命令输出会出现在这里</span>';
  byId("diff-output").textContent = "";
  byId("diff-output").classList.add("hidden");
  byId("diff-empty").classList.remove("hidden");
  state.activeStreamMessage = null;
  sendTask.disabled = false;
  setTaskState("", "空闲");
}

async function openHistoricalTask(taskId) {
  if (!taskId) return;
  if (state.source) {
    state.source.close();
    state.source = null;
  }
  try {
    const task = await fetchJson(`/api/tasks/${encodeURIComponent(taskId)}`);
    state.activeTask = task.id;
    resetTaskSurface();
    appendMessage("user", task.task, { forceScroll: true });
    const activityLine = ensureActivityLine();
    setActivity(activityLine, "正在加载任务");
    renderTaskHistory();
    const streamState = makeStreamState(0, task.id);
    if (["awaiting_approval", "review_required"].includes(task.status)) {
      await renderPausePrompt(task.id, task, activityLine, streamState);
      return;
    }
    startEventStream(task.id, activityLine, 0, streamState);
  } catch (error) {
    showToast(`打开历史任务失败：${error.message}`);
  }
}

async function resumeHistoricalTask(taskId) {
  try {
    const resumed = await fetchJson(`/api/tasks/${encodeURIComponent(taskId)}/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    showToast("已创建恢复任务");
    await loadTaskHistory();
    await openHistoricalTask(resumed.id);
  } catch (error) {
    showToast(`恢复任务失败：${error.message}`);
  }
}

async function openFile(path) {
  try {
    if (state.dirty && state.activeFile !== path) {
      showToast("请先保存当前文件，再打开其他文件。");
      editorInput.focus();
      return;
    }
    await loadEditorFile(path, { focus: true });
  } catch (error) { showToast(error.message); }
}

async function saveActiveFile() {
  if (!state.activeFile || !state.dirty) return true;
  if (state.saving) return false;
  const path = state.activeFile;
  const content = editorInput.value;
  state.saving = true;
  updateEditorState();
  try {
    await fetchJson("/api/file", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, content }),
    });
    state.savedContent = content;
    clearEditorNotice();
    showToast(`${path} 已保存`);
    return true;
  } catch (error) {
    showToast(`保存失败：${error.message}`);
    return false;
  } finally {
    state.saving = false;
    updateEditorState();
  }
}

async function reloadActiveFile() {
  if (!state.activeFile) return;
  try {
    await loadEditorFile(state.activeFile, { focus: true });
    showToast("已重新加载文件内容");
  } catch (error) { showToast(error.message); }
}

async function resolveEditorNotice() {
  const pendingPath = state.pendingAgentFile;
  if (!pendingPath || pendingPath === state.activeFile) {
    await reloadActiveFile();
    return;
  }
  const saved = await saveActiveFile();
  if (!saved || state.dirty) return;
  try {
    await loadEditorFile(pendingPath, { focus: true });
    showToast(`${pendingPath} 已打开`);
  } catch (error) { showToast(error.message); }
}

async function handleAgentFileChange(event) {
  if (!["write_file", "apply_patch"].includes(event.name)) return;
  const result = event.result || {};
  const changedPath = result.path || (event.arguments || {}).path;
  if (!changedPath) return;
  loadTree().catch((error) => showToast(error.message));
  if (state.dirty) {
    showAgentFileConflict(changedPath);
    return;
  }
  await loadEditorFile(changedPath);
  showToast(`${changedPath} 已同步智能体的改动`);
}

function appendMessage(role, content, { forceScroll = false } = {}) {
  const shouldFollow = forceScroll || isConversationNearBottom();
  const message = document.createElement("article");
  message.className = `message ${role}-message`;
  const metaName = role === "assistant" ? "Evidence Agent" : "你";
  const avatar = role === "assistant" ? "EA" : "我";
  message.innerHTML = `<div class="avatar ${role === "assistant" ? "agent-avatar" : "user-avatar"}">${avatar}</div><div class="message-body"><div class="message-meta"><span>${metaName}</span><time>现在</time></div><p>${escapeHtml(content)}</p></div>`;
  conversation.appendChild(message);
  if (shouldFollow) scrollConversationIfFollowing(true);
  return message;
}

function turnFor(streamState, step) {
  const parsed = Number(step);
  const normalized = Number.isFinite(parsed) ? parsed : (streamState.activeStep || 0);
  let turn = streamState.turns.get(normalized);
  if (!turn) {
    turn = {
      step: normalized,
      liveMessage: null,
      content: "",
      pendingContent: "",
      frame: null,
      finished: false,
    };
    streamState.turns.set(normalized, turn);
  }
  streamState.activeStep = normalized;
  return turn;
}

function ensureLiveMessage(turn) {
  if (turn.liveMessage) return turn.liveMessage;
  const message = appendMessage("assistant", "");
  message.classList.add("streaming-message");
  const contentNode = message.querySelector("p");
  contentNode.className = "stream-content";
  turn.liveMessage = {
    element: message,
    contentNode,
    content: "",
  };
  state.activeStreamMessage = turn.liveMessage;
  return turn.liveMessage;
}

function flushTurn(turn) {
  if (!turn.pendingContent) return;
  const live = ensureLiveMessage(turn);
  if (turn.pendingContent) {
    turn.content += turn.pendingContent;
    live.content += turn.pendingContent;
    live.contentNode.textContent = live.content;
    turn.pendingContent = "";
  }
  scrollConversationIfFollowing();
}

function scheduleTurnRender(turn) {
  if (turn.frame) return;
  turn.frame = window.requestAnimationFrame(() => {
    turn.frame = null;
    flushTurn(turn);
  });
}

function appendStreamDelta(turn, event) {
  const content = event.content || "";
  turn.pendingContent += content;
  if (content) scheduleTurnRender(turn);
}

function finalizeTurn(turn) {
  if (turn.frame) {
    window.cancelAnimationFrame(turn.frame);
    turn.frame = null;
  }
  flushTurn(turn);
  if (!turn.liveMessage) return;
  turn.liveMessage.element.classList.remove("streaming-message");
  turn.finished = true;
  if (state.activeStreamMessage === turn.liveMessage) state.activeStreamMessage = null;
}

function appendFinalSummary(content) {
  if (!content) return null;
  const message = appendMessage("assistant", content);
  message.classList.add("final-summary");
  const label = message.querySelector(".message-meta span");
  if (label) label.textContent = "最终回答";
  return message;
}

function appendPauseCard(taskId, status, draft, activityLine, streamState) {
  const card = document.createElement("article");
  card.className = "pause-card";
  card.dataset.pauseTask = taskId;
  const isReview = status === "review_required";
  const title = isReview ? "等待草稿审阅" : "等待命令审批";
  const description = isReview ? "智能体已准备好草稿改动，请确认后继续。" : "智能体准备执行命令，请确认是否继续。";
  card.innerHTML = `<div class="pause-card-title">${icon(isReview ? "file-check-2" : "shield-alert")}<strong>${title}</strong></div><p>${description}</p>`;
  if (isReview && draft && draft.diff) {
    const details = document.createElement("pre");
    details.className = "pause-card-diff";
    details.textContent = draft.diff;
    card.appendChild(details);
  }
  const actions = document.createElement("div");
  actions.className = "pause-card-actions";
  const accept = document.createElement("button");
  accept.type = "button";
  accept.className = "pause-card-primary";
  accept.textContent = isReview ? "接受草稿" : "允许执行";
  const reject = document.createElement("button");
  reject.type = "button";
  reject.className = "pause-card-secondary";
  reject.textContent = isReview ? "拒绝草稿" : "拒绝执行";
  actions.append(accept, reject);
  card.appendChild(actions);
  const resolve = async (accepted) => {
    accept.disabled = true;
    reject.disabled = true;
    setTaskState("running", "正在恢复");
    setActivity(activityLine, "正在恢复任务");
    try {
      const endpoint = isReview
        ? `/api/tasks/${encodeURIComponent(taskId)}/review`
        : `/api/tasks/${encodeURIComponent(taskId)}/approval`;
      const payload = isReview ? { accepted } : { approved: accepted };
      await fetchJson(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      card.remove();
      await loadTaskHistory();
      // A task can pause more than once; allow the next pause event to render.
      streamState.pauseRendered = false;
      startEventStream(taskId, activityLine, streamState.cursor, streamState);
    } catch (error) {
      accept.disabled = false;
      reject.disabled = false;
      setTaskState(status, title);
      showToast(`操作失败：${error.message}`);
    }
  };
  accept.addEventListener("click", () => resolve(true));
  reject.addEventListener("click", () => resolve(false));
  conversation.appendChild(card);
  refreshIcons();
  scrollConversationIfFollowing(true);
}

async function renderPausePrompt(taskId, event, activityLine, streamState) {
  if (streamState.pauseRendered) return;
  const status = event.status || event.report?.status;
  if (!(status === "awaiting_approval" || status === "review_required")) return;
  streamState.pauseRendered = true;
  setTaskState(status, status === "review_required" ? "等待草稿审阅" : "等待命令审批");
  setActivity(activityLine, status === "review_required" ? "等待草稿审阅" : "等待命令审批");
  sendTask.disabled = true;
  let draft = null;
  if (status === "review_required") {
    try {
      draft = await fetchJson(`/api/tasks/${encodeURIComponent(taskId)}/draft`);
      if (draft.diff) renderDiff(draft.diff);
    } catch (error) {
      showToast(`草稿加载失败：${error.message}`);
    }
  }
  const hasPauseCard = Array.from(conversation.querySelectorAll("[data-pause-task]")).some(
    (card) => card.dataset.pauseTask === taskId,
  );
  if (state.activeTask === taskId && !hasPauseCard) {
    appendPauseCard(taskId, status, draft, activityLine, streamState);
  }
}

function ensureActivityLine() {
  const line = document.createElement("div");
  line.className = "activity-line";
  line.textContent = "正在分析任务";
  conversation.appendChild(line);
  scrollConversationIfFollowing();
  return line;
}

function setActivity(line, label, kind = "") {
  if (!line) return;
  line.textContent = label;
  line.className = `activity-line${kind ? ` ${kind}` : ""}`;
  line.classList.remove("is-changing");
  window.requestAnimationFrame(() => line.classList.add("is-changing"));
  scrollConversationIfFollowing();
}

function formatExecutionOutput(result, emptyLabel) {
  const output = result.output || result.error || result.failure?.reason || emptyLabel;
  const recovery = result.failure?.recovery;
  return recovery ? `${output}\n建议：${recovery}` : output;
}

function appendTerminal(result) {
  const terminal = byId("terminal-output");
  if (terminal.querySelector(".terminal-muted")) terminal.replaceChildren();
  const entry = document.createElement("div");
  const failed = result.ok === false || (Number.isFinite(Number(result.exit_code)) && Number(result.exit_code) !== 0) || result.timed_out;
  entry.className = `terminal-entry ${failed ? "error" : "success"}`;
  const status = result.timed_out ? "超时" : result.failure?.label || (result.exit_code === null || result.exit_code === undefined ? "未启动" : `退出码 ${result.exit_code}`);
  const output = formatExecutionOutput(result, "(无输出)");
  entry.innerHTML = `<span class="terminal-command">${escapeHtml(result.command || "")}</span><span class="execution-summary">${escapeHtml(status)} · ${escapeHtml(String(result.duration_ms ?? 0))} ms</span><pre>${escapeHtml(output)}</pre>`;
  terminal.appendChild(entry);
  terminal.scrollTop = terminal.scrollHeight;
}

function appendExecutionMessage(result, mode) {
  const failed = result.ok === false || result.timed_out || (Number.isFinite(Number(result.exit_code)) && Number(result.exit_code) !== 0);
  const label = mode === "debug" ? "调试结果" : "运行结果";
  const status = result.timed_out ? "执行超时" : result.failure?.label || (failed ? `执行失败（退出码 ${result.exit_code ?? "未知"}）` : "执行成功");
  const output = formatExecutionOutput(result, "无输出");
  const message = appendMessage("assistant", `${label}：${status}\n耗时：${result.duration_ms ?? 0} ms\n\n${output}`, { forceScroll: true });
  message.classList.add(failed ? "execution-failure" : "execution-success");
  const labelNode = message.querySelector(".message-meta span");
  if (labelNode) labelNode.textContent = label;
  return message;
}

async function executeActiveFile(mode) {
  if (!state.activeFile || state.executing) {
    if (!state.activeFile) showToast("请先从左侧打开一个 Python 文件。");
    return;
  }
  if (!isRunnableFile(state.activeFile)) {
    showToast("运行和调试目前只支持 Python 文件（.py）。");
    return;
  }
  state.executing = true;
  updateEditorState();
  setTaskState("running", mode === "debug" ? "调试中" : "运行中");
  try {
    if (state.dirty) {
      const saved = await saveActiveFile();
      if (!saved || state.dirty) {
        showToast("请先完成文件保存，再执行。");
        return;
      }
    }
    switchTab("terminal");
    const endpoint = mode === "debug" ? "/api/debug" : "/api/run";
    const result = await fetchJson(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: state.activeFile, content: editorInput.value }),
    });
    appendTerminal(result);
    appendExecutionMessage(result, mode);
    setTaskState(result.ok ? "complete" : "error", result.ok ? "执行成功" : "执行失败");
  } catch (error) {
    const result = error.payload;
    if (result && (result.command || result.output || result.timed_out)) {
      appendTerminal(result);
      appendExecutionMessage(result, mode);
    } else {
      appendMessage("assistant", `${mode === "debug" ? "调试" : "运行"}失败：${error.message}`, { forceScroll: true });
    }
    setTaskState("error", "执行失败");
  } finally {
    state.executing = false;
    updateEditorState();
  }
}

function runActiveFile() { return executeActiveFile("run"); }
function debugActiveFile() { return executeActiveFile("debug"); }

function renderDiff(diff) {
  const target = byId("diff-output");
  target.innerHTML = diff.split("\n").map((line) => {
    const className = line.startsWith("+") && !line.startsWith("+++") ? "diff-add" : line.startsWith("-") && !line.startsWith("---") ? "diff-remove" : line.startsWith("@@") || line.startsWith("+++") || line.startsWith("---") ? "diff-meta" : "";
    return `<span class="${className}">${escapeHtml(line)}</span>`;
  }).join("\n");
  target.classList.remove("hidden");
  byId("diff-empty").classList.add("hidden");
}

async function refreshDiff(taskId) {
  const diff = await fetchJson(`/api/diff?task_id=${encodeURIComponent(taskId)}`);
  if (diff.diff) renderDiff(diff.diff);
}

function labelForTool(name) {
  if (["read_file", "list_files", "search_files"].includes(name)) return "正在查看文件";
  if (["write_file", "apply_patch"].includes(name)) return "正在修改文件";
  if (name === "run_command") return "正在运行验证命令";
  return "正在处理任务";
}

function processEvent(event, taskId, activityLine, streamState) {
  const result = event.result || {};
  if (event.type === "run_started") {
    setTaskState("running", "正在分析");
    setActivity(activityLine, "正在分析任务");
  }
  if (event.type === "model_message_start") {
    const turn = turnFor(streamState, event.step);
    setTaskState("running", `正在生成 · 第 ${turn.step} 轮`);
    setActivity(activityLine, "正在生成回复");
  }
  if (event.type === "model_delta") {
    const turn = turnFor(streamState, event.step);
    if (event.content) {
      setTaskState("running", `正在生成 · 第 ${turn.step} 轮`);
      setActivity(activityLine, "正在生成回复");
    }
    appendStreamDelta(turn, event);
  }
  if (event.type === "model_message_end") {
    const turn = turnFor(streamState, event.step);
    finalizeTurn(turn);
  }
  if (event.type === "model_response") {
    const turn = turnFor(streamState, event.step);
    const text = event.message && event.message.content;
    if (text && !turn.content && !turn.pendingContent) turn.pendingContent += text;
    if (text) {
      flushTurn(turn);
      finalizeTurn(turn);
    }
  }
  if (event.type === "tool_call") {
    setTaskState("running", "正在执行");
    setActivity(activityLine, labelForTool(event.name));
  }
  if (event.type === "tool_result") {
    setTaskState("running", event.name === "run_command" ? "正在验证" : "正在执行");
    setActivity(activityLine, event.name === "run_command" ? "正在检查运行结果" : labelForTool(event.name));
    if (event.name === "run_command") appendTerminal(result);
    handleAgentFileChange(event).catch((error) => showToast(error.message));
  }
  if (event.type === "model_error") {
    setTaskState("error", "模型请求失败");
    setActivity(activityLine, "模型请求失败", "error");
  }
  if (event.type === "run_paused") {
    renderPausePrompt(taskId, event, activityLine, streamState).catch((error) => showToast(error.message));
  }
  if (event.type === "run_finished") {
    setTaskState("running", "正在整理结果");
    setActivity(activityLine, "正在整理最终回答");
  }
}

function completeTask(report, taskId, activityLine, streamState) {
  if (streamState && streamState.finished) return;
  if (streamState) streamState.finished = true;
  if (streamState) streamState.turns.forEach((turn) => finalizeTurn(turn));
  sendTask.disabled = false;
  const complete = report && report.status === "complete";
  setTaskState(complete ? "complete" : "error", complete ? "已验证" : "未完成");
  setActivity(activityLine, complete ? "已完成" : "执行失败", complete ? "success" : "error");
  if (report) {
    let summary = report.summary || (complete ? "任务已完成。" : "任务没有完成验证。");
    if (!complete && !summary.startsWith("任务执行失败") && report.status === "error") {
      summary = `任务执行失败：${summary}`;
    }
    if (!streamState || !streamState.finalSummaryAppended) {
      appendFinalSummary(summary);
      if (streamState) streamState.finalSummaryAppended = true;
    }
  }
  refreshDiff(taskId).catch((error) => showToast(error.message));
  loadTaskHistory().catch(() => {});
}

async function reconnectOrComplete(taskId, activityLine, streamState) {
  if (state.activeTask !== taskId || streamState.finished) return;
  try {
    const task = await fetchJson(`/api/tasks/${encodeURIComponent(taskId)}`);
    if (state.activeTask !== taskId || streamState.finished) return;
    if (["awaiting_approval", "review_required"].includes(task.status)) {
      await renderPausePrompt(taskId, task, activityLine, streamState);
      return;
    }
    if (["complete", "error", "incomplete", "interrupted"].includes(task.status)) {
      completeTask(task.report, taskId, activityLine, streamState);
      return;
    }
    window.setTimeout(() => startEventStream(taskId, activityLine, streamState.cursor, streamState), 350);
  } catch (error) {
    showToast(error.message);
    sendTask.disabled = false;
  }
}

function makeStreamState(cursor = 0, taskId = null) {
  return { cursor, taskId, finished: false, turns: new Map(), activeStep: null, finalSummaryAppended: false, pauseRendered: false };
}

function startEventStream(taskId, activityLine, cursor = 0, streamState = makeStreamState(cursor)) {
  if (streamState.taskId === null) streamState.taskId = taskId;
  if (streamState.finished || streamState.taskId !== taskId || state.activeTask !== taskId) return;
  streamState.cursor = cursor;
  const source = new EventSource(`/api/tasks/${encodeURIComponent(taskId)}/events?after=${cursor}`);
  state.source = source;
  ["run_started", "model_message_start", "model_delta", "model_message_end", "model_response", "tool_call", "tool_result", "model_error", "run_paused", "run_finished"].forEach((name) => {
    source.addEventListener(name, (message) => {
      if (state.activeTask !== taskId || streamState.finished) return;
      const eventCursor = Number(message.lastEventId);
      if (Number.isInteger(eventCursor) && eventCursor > streamState.cursor) streamState.cursor = eventCursor;
      processEvent(JSON.parse(message.data), taskId, activityLine, streamState);
    });
  });
    source.addEventListener("end", () => {
    source.close();
    if (state.source === source) state.source = null;
    reconnectOrComplete(taskId, activityLine, streamState);
  });
  source.onerror = () => {
    source.close();
    if (state.source === source) state.source = null;
    if (!streamState.finished && state.activeTask === taskId) reconnectOrComplete(taskId, activityLine, streamState);
  };
}

function setInspectorWidth(width, { persist = true } = {}) {
  const minWidth = 520;
  const maxWidth = Math.min(760, Math.max(minWidth, window.innerWidth - 244 - 320));
  const next = Math.round(Math.min(maxWidth, Math.max(minWidth, Number(width) || 560)));
  document.querySelector(".app-shell").style.gridTemplateColumns = `244px minmax(0, 1fr) ${next}px`;
  document.documentElement.style.setProperty("--inspector-width", `${next}px`);
  if (persist) {
    try { localStorage.setItem("evidence-agent-inspector-width", String(next)); } catch (error) { /* storage may be unavailable */ }
  }
}

function setupInspectorResizer() {
  const resizer = byId("inspector-resizer");
  if (!resizer) return;
  try {
    const stored = Number(localStorage.getItem("evidence-agent-inspector-width"));
    if (window.innerWidth > 1050) setInspectorWidth(stored >= 520 ? stored : 560, { persist: false });
  } catch (error) {
    if (window.innerWidth > 1050) setInspectorWidth(560, { persist: false });
  }
  let resizing = false;
  const finish = () => {
    if (!resizing) return;
    resizing = false;
    document.querySelector(".app-shell").classList.remove("resizing");
  };
  resizer.addEventListener("pointerdown", (event) => {
    if (window.innerWidth <= 840 || byId("inspector").classList.contains("hidden")) return;
    resizing = true;
    resizer.setPointerCapture?.(event.pointerId);
    document.querySelector(".app-shell").classList.add("resizing");
    event.preventDefault();
  });
  resizer.addEventListener("pointermove", (event) => {
    if (resizing) setInspectorWidth(window.innerWidth - event.clientX);
  });
  resizer.addEventListener("pointerup", finish);
  resizer.addEventListener("pointercancel", finish);
  resizer.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const current = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--inspector-width"), 10) || 560;
    const next = event.key === "ArrowLeft" ? current + 20 : event.key === "ArrowRight" ? current - 20 : event.key === "Home" ? 760 : 520;
    setInspectorWidth(next);
    event.preventDefault();
  });
  window.addEventListener("resize", () => {
    if (window.innerWidth > 1050) {
      const current = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--inspector-width"), 10) || 560;
      setInspectorWidth(current, { persist: false });
    } else {
      document.querySelector(".app-shell").style.removeProperty("grid-template-columns");
    }
  });
}

async function submitTask() {
  const prompt = taskInput.value.trim();
  if (!prompt || sendTask.disabled) return;
  if (state.dirty) {
    sendTask.disabled = true;
    const saved = await saveActiveFile();
    if (!saved || state.dirty) {
      sendTask.disabled = false;
      if (state.dirty) showToast("请完成文件保存后再运行任务。");
      return;
    }
  }
  if (state.source) state.source.close();
  appendMessage("user", prompt, { forceScroll: true });
  const activityLine = ensureActivityLine();
  setActivity(activityLine, "正在分析任务");
  taskInput.value = "";
  byId("reference-menu").classList.add("hidden");
  closePluginMenu();
  sendTask.disabled = true;
  setTaskState("running", "运行中");
  try {
    const created = await fetchJson("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task: prompt, mode: state.mode, skills: state.selectedPlugins }),
    });
    state.activeTask = created.id;
    loadTaskHistory().catch(() => {});
    startEventStream(created.id, activityLine);
  } catch (error) {
    setActivity(activityLine, "启动失败", "error");
    appendFinalSummary(`任务启动失败：${error.message}`);
    setTaskState("error", "启动失败");
    sendTask.disabled = false;
  }
}

function showReferenceMenu() {
  const match = taskInput.value.match(/@([^\s@]*)$/);
  const menu = byId("reference-menu");
  if (!match || !state.files.length) { menu.classList.add("hidden"); return; }
  const query = match[1].toLowerCase();
  const matches = state.files.filter((file) => file.toLowerCase().includes(query)).slice(0, 8);
  if (!matches.length) { menu.classList.add("hidden"); return; }
  menu.innerHTML = matches.map((file) => `<button class="reference-option" type="button" data-path="${escapeHtml(file)}">${icon("file-code-2")}<span>${escapeHtml(file)}</span></button>`).join("");
  menu.classList.remove("hidden");
  menu.querySelectorAll("button").forEach((button) => button.addEventListener("click", () => {
    taskInput.value = taskInput.value.replace(/@([^\s@]*)$/, `@${button.dataset.path} `);
    menu.classList.add("hidden");
    taskInput.focus();
  }));
  refreshIcons();
}

function selectMode(mode) {
  state.mode = mode;
  byId("plan-mode").classList.toggle("active", mode === "plan");
  byId("execute-mode").classList.toggle("active", mode === "execute");
  byId("plan-mode").setAttribute("aria-pressed", String(mode === "plan"));
  byId("execute-mode").setAttribute("aria-pressed", String(mode === "execute"));
}

document.querySelectorAll(".tab-button").forEach((button) => button.addEventListener("click", () => switchTab(button.dataset.tab)));
byId("plan-mode").addEventListener("click", () => selectMode("plan"));
byId("execute-mode").addEventListener("click", () => selectMode("execute"));
byId("refresh-tree").addEventListener("click", () => loadTree().catch((error) => showToast(error.message)));
byId("new-task").addEventListener("click", () => { taskInput.value = ""; taskInput.focus(); });
byId("add-session").addEventListener("click", () => { taskInput.value = ""; taskInput.focus(); });
byId("toggle-inspector").addEventListener("click", () => {
  const inspector = byId("inspector");
  const shell = document.querySelector(".app-shell");
  inspector.classList.toggle("hidden");
  if (inspector.classList.contains("hidden") || window.innerWidth <= 1050) {
    shell.style.removeProperty("grid-template-columns");
  } else {
    const current = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--inspector-width"), 10) || 560;
    setInspectorWidth(current, { persist: false });
  }
});
saveFile.addEventListener("click", () => { saveActiveFile(); });
runFile.addEventListener("click", runActiveFile);
debugFile.addEventListener("click", debugActiveFile);
editorNoticeAction.addEventListener("click", resolveEditorNotice);
sendTask.addEventListener("click", submitTask);
editorInput.addEventListener("input", () => {
  updateEditorState();
  renderEditorHighlight();
});
editorInput.addEventListener("scroll", syncEditorHighlightScroll);
editorInput.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    saveActiveFile();
  }
});
taskInput.addEventListener("input", showReferenceMenu);
taskInput.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    submitTask();
    return;
  }
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submitTask();
    return;
  }
  if (event.key === "Escape") {
    byId("reference-menu").classList.add("hidden");
    closePluginMenu();
  }
});

document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (target?.closest("#plugin-toggle")) {
    togglePluginMenu();
    return;
  }
  if (!target?.closest("#plugin-menu")) closePluginMenu();
});

loadTree().catch((error) => showToast(error.message));
loadTaskHistory();
setupInspectorResizer();
refreshIcons();
