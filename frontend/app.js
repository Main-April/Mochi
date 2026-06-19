var LS_KEY = "agent_backend_url";
var DEFAULT_URL = "http://localhost:8000";

function getUrl(path) {
  try { return (localStorage.getItem(LS_KEY) || DEFAULT_URL) + path; } catch { return DEFAULT_URL + path; }
}

var form = document.getElementById("form");
var input = document.getElementById("input");
var submitBtn = document.getElementById("submitBtn");
var messagesEl = document.getElementById("messages");
var emptyEl = document.getElementById("empty");
var errorBar = document.getElementById("errorBar");
var statusEl = document.getElementById("status");
var scrollBtn = document.getElementById("scrollBtn");
var clearBtn = document.getElementById("clearBtn");
var browseBtn = document.getElementById("browseBtn");
var conversation = document.getElementById("conversation");

var modeBtns = document.querySelectorAll(".mode-btn");
var settingsOverlay = document.getElementById("settingsOverlay");
var commandsOverlay = document.getElementById("commandsOverlay");
var urlInput = document.getElementById("url");
var settingsMode = document.getElementById("settingsMode");
var settingsModel = document.getElementById("settingsModel");
var settingsMaxTokens = document.getElementById("settingsMaxTokens");
var settingsMaxTokensVal = document.getElementById("settingsMaxTokensVal");
var settingsTemp = document.getElementById("settingsTemp");
var settingsTempVal = document.getElementById("settingsTempVal");
var settingsBtn = document.getElementById("settingsBtn");
var commandsBtn = document.getElementById("commandsBtn");
var closeCommandsBtn = document.getElementById("closeCommandsBtn");
var cancelBtn = document.getElementById("cancelBtn");
var saveBtn = document.getElementById("saveBtn");
var logBtn = document.getElementById("logBtn");
var statsBtn = document.getElementById("statsBtn");
var statsOverlay = document.getElementById("statsOverlay");
var statsContent = document.getElementById("statsContent");
var closeStatsBtn = document.getElementById("closeStatsBtn");

var sending = false;
var msgs = [];
var currentMode = "work";
var lastSettings = {};

var MODE_LABELS = { work: "Work", docs: "Docs", debug: "Debug", creative: "Creative" };

// Tool call tracking
var toolCalls = {};

var TOOL_ICONS = {
  edit_file: '<i class="fa-solid fa-pen-to-square"></i>',
  write_file: '<i class="fa-solid fa-file-circle-plus"></i>',
  read_file: '<i class="fa-solid fa-book-open"></i>',
  list_files: '<i class="fa-solid fa-folder-tree"></i>',
  run_command: '<i class="fa-solid fa-terminal"></i>',
  web_fetch: '<i class="fa-solid fa-globe"></i>',
};

var TOOL_LABELS = {
  edit_file: 'Edit File',
  write_file: 'Write File',
  read_file: 'Read File',
  list_files: 'List Directory',
  run_command: 'Run Command',
  web_fetch: 'Web Fetch',
};

function fmtTime(iso) {
  if (!iso) return "";
  var d = new Date(iso);
  return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function nowISO() { return new Date().toISOString(); }

function esc(str) {
  if (typeof str !== 'string') str = String(str);
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function markdownToHtml(text) {
  if (!text) return "";
  var html = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(_, lang, code) {
    return "<pre><code>" + code.replace(/</g, "&lt;").replace(/>/g, "&gt;") + "</code></pre>";
  });
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
  html = html.replace(/### (.+)/g, "<h3>$1</h3>");
  html = html.replace(/## (.+)/g, "<h2>$1</h2>");
  html = html.replace(/# (.+)/g, "<h1>$1</h1>");
  html = html.replace(/(<li>.*?<\/li>)(?:\s*<li>.*?<\/li>)*/gs, function(m) {
    return "<ul>" + m + "</ul>";
  });
  html = html.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  html = html.replace(/<li><\/li>/g, "");
  html = html.replace(/\n\n+/g, "<br>");
  html = html.replace(/\n/g, "<br>");
  html = html.replace(/(<br>\s*){3,}/g, "<br><br>");
  return html;
}

function setStatus(state, mode) {
  var dot = statusEl.querySelector(".dot");
  dot.className = "dot";
  var label = MODE_LABELS[mode || currentMode] || "Working";
  if (state === "ready") { statusEl.textContent = ""; statusEl.append(dot, " " + label); }
  else if (state === "busy") { statusEl.textContent = ""; dot.classList.add("busy"); statusEl.append(dot, " En cours\u2026"); }
  else { statusEl.textContent = ""; dot.classList.add("err"); statusEl.append(dot, " Erreur"); }
}

function setActiveModeBtn(mode) {
  modeBtns.forEach(function(b) { b.classList.toggle("active", b.dataset.mode === mode); });
}

async function switchMode(mode) {
  try {
    var res = await fetch(getUrl("/mode"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: mode }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    var data = await res.json();
    currentMode = data.mode;
    setActiveModeBtn(currentMode);
    setStatus("ready", currentMode);
  } catch (err) {
    showError("Impossible de changer de mode (" + err.message + ")");
  }
}

modeBtns.forEach(function(btn) {
  btn.addEventListener("click", function() { switchMode(btn.dataset.mode); });
});

async function handleCommand(text) {
  var parts = text.toLowerCase().trim().split(/\s+/);
  var cmd = parts[0];
  var arg = parts.slice(1).join(" ");
  if (cmd === "/work") { switchMode("work"); return true; }
  if (cmd === "/docs") { switchMode("docs"); return true; }
  if (cmd === "/debug") { switchMode("debug"); return true; }
  if (cmd === "/creative") { switchMode("creative"); return true; }
  if (cmd === "/clear") { clearChat(); return true; }
  if (cmd === "/undo") { await undoLastAction(); return true; }
  if (cmd === "/redo") { await redoLastAction(); return true; }
  if (cmd === "/reset") { await resetMemory(); return true; }
  if (cmd === "/help") { showCommands(); return true; }
  return false;
}

async function resetMemory() {
  try {
    var res = await fetch(getUrl("/reset"), { method: "POST" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    showSuccess("M\u00E9moire r\u00E9initialis\u00E9e.");
  } catch (err) {
    showError("Erreur reset: " + err.message);
  }
}

async function undoLastAction() {
  try {
    var res = await fetch(getUrl("/undo"), { method: "POST" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    var data = await res.json();
    showSuccess(data.message || "Action annul\u00E9e.");
  } catch (err) {
    showError("Erreur undo: " + err.message);
  }
}

async function redoLastAction() {
  try {
    var res = await fetch(getUrl("/redo"), { method: "POST" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    var data = await res.json();
    showSuccess(data.message || "Action refaite.");
  } catch (err) {
    showError("Erreur redo: " + err.message);
  }
}

function showCommands() { commandsOverlay.classList.remove("hide"); }
function showSuccess(msg) {
  errorBar.textContent = msg;
  errorBar.style.borderColor = "oklch(0.60 0.20 250 / 0.5)";
  errorBar.style.background = "oklch(0.60 0.20 250 / 0.15)";
  errorBar.style.color = "var(--primary)";
  errorBar.classList.remove("hide");
  setTimeout(function() { errorBar.classList.add("hide"); }, 3000);
}

function addMsg(id, role, content, meta) {
  var div = document.createElement("div");
  div.className = "msg " + role;
  div.id = "msg-" + id;

  if (role === "assistant") {
    var header = document.createElement("div");
    header.className = "msg-header";
    var icon = document.createElement("span");
    icon.className = "msg-icon";
    var img = document.createElement("img");
    img.src = "agent-logo.png";
    img.alt = "";
    icon.appendChild(img);
    header.appendChild(icon);
    var label = document.createElement("span");
    label.className = "msg-model";
    label.textContent = MODE_LABELS[(meta && meta.mode) || currentMode] || "";
    header.appendChild(label);
    if (meta && meta.ts) {
      var ts = document.createElement("span");
      ts.className = "msg-ts";
      ts.textContent = fmtTime(meta.ts);
      header.appendChild(ts);
    }
    div.appendChild(header);
  }

  var bubble = document.createElement("div");
  bubble.className = "bubble";
  var p = document.createElement("p");
  if (role === "assistant") {
    p.innerHTML = markdownToHtml(content);
  } else {
    p.textContent = content;
  }
  bubble.appendChild(p);
  div.appendChild(bubble);

  if (role === "user") {
    if (meta && meta.ts) {
      var tsDiv = document.createElement("div");
      tsDiv.className = "msg-ts user-ts";
      tsDiv.textContent = fmtTime(meta.ts);
      div.appendChild(tsDiv);
    }
    var actions = document.createElement("div");
    actions.className = "msg-actions";
    var copyBtn = document.createElement("button");
    copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
    copyBtn.title = "Copier le message";
    copyBtn.addEventListener("click", function() {
      navigator.clipboard.writeText(content).then(function() {
        copyBtn.classList.add("copied");
        setTimeout(function() { copyBtn.classList.remove("copied"); }, 1500);
      });
    });
    actions.appendChild(copyBtn);
    var editBtn = document.createElement("button");
    editBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>';
    editBtn.title = "Modifier le message";
    editBtn.addEventListener("click", function() { editUserMsg(id); });
    actions.appendChild(editBtn);
    div.appendChild(actions);
  }

  if (role === "assistant" && meta) {
    var actions = document.createElement("div");
    actions.className = "msg-actions";
    var copyBtn = document.createElement("button");
    copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
    copyBtn.title = "Copier la r\u00E9ponse";
    copyBtn.addEventListener("click", function() {
      navigator.clipboard.writeText(content).then(function() {
        copyBtn.classList.add("copied");
        setTimeout(function() { copyBtn.classList.remove("copied"); }, 1500);
      });
    });
    actions.appendChild(copyBtn);
    var editBtn = document.createElement("button");
    editBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>';
    editBtn.title = "Modifier le prompt";
    editBtn.addEventListener("click", function() { editPrompt(id); });
    actions.appendChild(editBtn);
    div.appendChild(actions);
  }

  messagesEl.appendChild(div);
  scrollToBottom();
}

function updateBubbleContent(id, content) {
  var el = document.getElementById("msg-" + id);
  if (!el) return;
  var p = el.querySelector(".bubble p");
  if (p) p.innerHTML = markdownToHtml(content);
  scrollToBottom();
}

function addTyping(id) {
  var div = document.createElement("div");
  div.className = "msg assistant";
  div.id = "msg-" + id;
  var header = document.createElement("div");
  header.className = "msg-header";
  var icon = document.createElement("span");
  icon.className = "msg-icon";
  var img = document.createElement("img");
  img.src = "agent-logo.png";
  img.alt = "";
  icon.appendChild(img);
  header.appendChild(icon);
  var label = document.createElement("span");
  label.className = "msg-model";
  label.textContent = MODE_LABELS[currentMode] || currentMode;
  header.appendChild(label);
  div.appendChild(header);
  var bubble = document.createElement("div");
  bubble.className = "bubble";
  var wrap = document.createElement("div");
  wrap.className = "typing-indicator";
  var dots = document.createElement("span");
  dots.className = "typing";
  for (var i = 0; i < 3; i++) dots.appendChild(document.createElement("span"));
  wrap.appendChild(dots);
  var shimmer = document.createElement("span");
  shimmer.className = "typing-text";
  shimmer.textContent = "L\u2019agent r\u00E9fl\u00E9chit\u2026";
  wrap.appendChild(shimmer);
  bubble.appendChild(wrap);
  div.appendChild(bubble);
  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

function removeMsg(id) { var el = document.getElementById("msg-" + id); if (el) el.remove(); }

function scrollToBottom() {
  requestAnimationFrame(function() { conversation.scrollTop = conversation.scrollHeight; });
}

function updateEmpty() {
  if (msgs.length === 0) { emptyEl.classList.remove("hide"); } else { emptyEl.classList.add("hide"); }
  clearBtn.disabled = msgs.length === 0;
}

function showError(msg) {
  errorBar.style.borderColor = "oklch(0.65 0.22 25 / 0.5)";
  errorBar.style.background = "oklch(0.65 0.22 25 / 0.15)";
  errorBar.style.color = "var(--destructive)";
  errorBar.textContent = msg;
  errorBar.classList.remove("hide");
  setTimeout(function() { errorBar.classList.add("hide"); }, 8000);
}

function clearChat() {
  msgs = [];
  toolCalls = {};
  document.querySelectorAll(".msg").forEach(function(el) { el.remove(); });
  updateEmpty();
  setStatus("ready", currentMode);
  errorBar.classList.add("hide");
}

function editPrompt(assistantId) {
  var idx = msgs.findIndex(function(m) { return m.id === assistantId; });
  if (idx < 1 || msgs[idx - 1].role !== "user") return;
  var userMsg = msgs[idx - 1];
  var removeIds = [msgs[idx - 1].id, msgs[idx].id];
  msgs.splice(idx - 1, 2);
  removeIds.forEach(function(id) { var el = document.getElementById("msg-" + id); if (el) el.remove(); });
  updateEmpty();
  input.value = userMsg.content;
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 120) + "px";
  submitBtn.disabled = false;
  input.focus();
}

function editUserMsg(userId) {
  var idx = msgs.findIndex(function(m) { return m.id === userId; });
  if (idx < 0) return;
  var userMsg = msgs[idx];
  var removeIds = [userMsg.id];
  if (idx + 1 < msgs.length && msgs[idx + 1].role === "assistant") {
    removeIds.push(msgs[idx + 1].id);
    msgs.splice(idx, 2);
  } else {
    msgs.splice(idx, 1);
  }
  removeIds.forEach(function(id) { var el = document.getElementById("msg-" + id); if (el) el.remove(); });
  updateEmpty();
  input.value = userMsg.content;
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 120) + "px";
  submitBtn.disabled = false;
  input.focus();
}

// --- Tool card rendering ---

function getToolTitle(name, args) {
  switch (name) {
    case "edit_file": return esc(args.path || "");
    case "write_file": return esc(args.path || "");
    case "read_file": return esc(typeof args.path === 'string' ? args.path : (args.path || []).join(", "));
    case "list_files": return esc(args.path || ".");
    case "run_command": return esc((args.command || "").substring(0, 80)) + ((args.command || "").length > 80 ? "..." : "");
    case "web_fetch": return esc(args.url || "");
    default: return "";
  }
}

function renderToolCard(name, args, result) {
  var card = document.createElement("div");
  card.className = "tool-card";

  var header = document.createElement("div");
  header.className = "tool-card-header";

  var toggle = document.createElement("span");
  toggle.className = "tool-card-toggle";
  toggle.innerHTML = '<i class="fa-solid fa-chevron-down"></i>';
  header.appendChild(toggle);

  var icon = document.createElement("span");
  icon.className = "tool-card-icon";
  icon.innerHTML = TOOL_ICONS[name] || '<i class="fa-solid fa-wrench"></i>';
  header.appendChild(icon);

  var label = document.createElement("span");
  label.className = "tool-card-name";
  label.textContent = TOOL_LABELS[name] || name;
  header.appendChild(label);

  var title = document.createElement("span");
  title.className = "tool-card-title";
  title.textContent = getToolTitle(name, args);
  header.appendChild(title);

  var duration = document.createElement("span");
  duration.className = "tool-card-duration";

  var isError = result && result.data && result.data.error;
  var durationMs = result && result.data && result.data.duration_ms;

  if (durationMs !== undefined && durationMs !== null) {
    duration.textContent = durationMs > 1000 ? (durationMs / 1000).toFixed(1) + "s" : durationMs + "ms";
  } else if (!result) {
    duration.textContent = "...";
  }
  header.appendChild(duration);

  var statusIcon = document.createElement("span");
  statusIcon.className = "tool-card-status";
  if (!result) {
    statusIcon.innerHTML = '<span class="spinner"></span>';
  } else if (isError) {
    statusIcon.innerHTML = '<i class="fa-solid fa-xmark"></i>';
    statusIcon.style.color = "#ef4444";
  } else {
    statusIcon.innerHTML = '<i class="fa-solid fa-check"></i>';
    statusIcon.style.color = "#34d399";
  }
  header.appendChild(statusIcon);

  header.addEventListener("click", function() {
    var expanded = card.classList.toggle("collapsed");
    toggle.innerHTML = expanded ? '<i class="fa-solid fa-chevron-right"></i>' : '<i class="fa-solid fa-chevron-down"></i>';
  });

  card.appendChild(header);

  var body = document.createElement("div");
  body.className = "tool-card-body";

  if (result && result.data && result.data.error) {
    var errBlock = document.createElement("div");
    errBlock.className = "tool-card-error";
    errBlock.textContent = result.data.error;
    body.appendChild(errBlock);
  } else if (name === "edit_file" && result && result.data) {
    body.appendChild(renderDiff(result.data));
  } else if (name === "write_file" && result && result.data) {
    body.appendChild(renderFileContent(result.data.content || "", true));
  } else if (name === "read_file" && result && result.data) {
    if (result.data.files) {
      result.data.files.forEach(function(f) {
        var h = document.createElement("div");
        h.className = "tool-card-subheader";
        h.textContent = f.path + " (" + f.total_lines + " lines)";
        body.appendChild(h);
        body.appendChild(renderFileContent(f.content || "", false));
      });
    } else {
      body.appendChild(renderFileContent(result.data.content || "", false));
    }
  } else if (name === "list_files" && result && result.data) {
    body.appendChild(renderFileTree(result.data.items || []));
  } else if (name === "run_command" && result && result.data) {
    body.appendChild(renderCommandOutput(result.data));
  } else if (name === "web_fetch" && result && result.data) {
    body.appendChild(renderWebFetch(result.data));
  } else if (result) {
    // fallback: show summary
    var pre = document.createElement("pre");
    pre.className = "tool-card-fallback";
    pre.textContent = result.summary || "";
    body.appendChild(pre);
  } else {
    var pre = document.createElement("pre");
    pre.className = "tool-card-fallback";
    pre.textContent = "En attente du résultat...";
    body.appendChild(pre);
  }

  card.appendChild(body);
  return card;
}

function renderDiff(data) {
  var container = document.createElement("div");
  container.className = "diff-container";

  // Changes summary
  var summary = document.createElement("div");
  summary.className = "diff-summary";
  var adds = (data.diff || []).filter(function(l) { return l.type === "add"; }).length;
  var dels = (data.diff || []).filter(function(l) { return l.type === "del"; }).length;
  summary.innerHTML = '<span class="diff-file">' + esc(data.path) + '</span> ' +
    '<span class="diff-range">L' + data.start_line + '-' + data.end_line + '</span>' +
    '<span class="diff-stats"><span class="diff-adds">+' + adds + '</span> <span class="diff-dels">-' + dels + '</span></span>';
  container.appendChild(summary);

  // Diff lines
  var code = document.createElement("div");
  code.className = "diff-code";
  var lineNum = data.start_line || 1;
  (data.diff || []).forEach(function(line) {
    var div = document.createElement("div");
    div.className = "diff-line diff-" + line.type;
    if (line.type === "hunk") {
      div.className = "diff-line diff-hunk";
      div.textContent = line.content;
      code.appendChild(div);
      return;
    }
    var num = document.createElement("span");
    num.className = "diff-ln";
    if (line.type !== "add") {
      var oldNum = document.createElement("span");
      oldNum.className = "diff-ln-num";
      oldNum.textContent = lineNum;
      num.appendChild(oldNum);
    }
    if (line.type === "add") {
      var newNum = document.createElement("span");
      newNum.className = "diff-ln-num diff-ln-new";
      newNum.textContent = lineNum;
      num.appendChild(newNum);
    }
    var cn = document.createElement("span");
    cn.className = "diff-cn";
    cn.textContent = line.content || "";
    div.appendChild(num);
    div.appendChild(cn);
    code.appendChild(div);
    if (line.type !== "del") { lineNum++; }
  });
  container.appendChild(code);
  return container;
}

function renderFileContent(content, isNew) {
  var container = document.createElement("div");
  container.className = "file-container";
  var lines = content.split("\n");
  var code = document.createElement("div");
  code.className = "file-code" + (isNew ? " file-new" : "");
  lines.forEach(function(line, i) {
    var div = document.createElement("div");
    div.className = "file-line";
    var num = document.createElement("span");
    num.className = "file-ln";
    num.textContent = i + 1;
    div.appendChild(num);
    var cn = document.createElement("span");
    cn.className = "file-cn";
    cn.textContent = line;
    div.appendChild(cn);
    code.appendChild(div);
  });
  container.appendChild(code);
  return container;
}

function renderFileTree(items) {
  var container = document.createElement("div");
  container.className = "tree-container";
  items.forEach(function(item) {
    var el = document.createElement("div");
    el.className = "tree-item";
    var icon = document.createElement("span");
    icon.className = "tree-icon";
    icon.innerHTML = item.type === "dir" ? '<i class="fa-regular fa-folder"></i>' : '<i class="fa-regular fa-file"></i>';
    el.appendChild(icon);
    var name = document.createElement("span");
    name.className = "tree-name";
    name.textContent = item.name;
    el.appendChild(name);
    if (item.type === "file") {
      var size = document.createElement("span");
      size.className = "tree-size";
      size.textContent = formatFileSize(item.size);
      el.appendChild(size);
    }
    container.appendChild(el);
  });
  return container;
}

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1048576).toFixed(1) + " MB";
}

function renderCommandOutput(data) {
  var container = document.createElement("div");
  container.className = "cmd-container";

  var cmdLine = document.createElement("div");
  cmdLine.className = "cmd-line";
  cmdLine.innerHTML = '<span class="cmd-prompt">$</span> <span class="cmd-text">' + esc(data.command || "") + '</span>';
  if (data.duration_ms !== undefined && data.duration_ms !== null) {
    var dur = data.duration_ms > 1000 ? (data.duration_ms / 1000).toFixed(1) + "s" : data.duration_ms + "ms";
    var durEl = document.createElement("span");
    durEl.style.cssText = "margin-left:auto;font-size:11px;color:var(--muted-fg);font-family:monospace";
    durEl.textContent = dur;
    cmdLine.appendChild(durEl);
  }
  container.appendChild(cmdLine);

  function _makeOutputBlock(text, className, maxLines) {
    var lines = text.split("\n");
    var isTruncated = lines.length > maxLines;
    var displayLines = isTruncated ? lines.slice(0, maxLines) : lines;

    var pre = document.createElement("pre");
    pre.className = className;
    pre.textContent = displayLines.join("\n");
    container.appendChild(pre);

    if (isTruncated) {
      var toggle = document.createElement("div");
      toggle.style.cssText = "cursor:pointer;font-size:11px;color:var(--primary);text-align:center;padding:4px;user-select:none";
      toggle.textContent = "Afficher tout (" + lines.length + " lignes) \u25BC";
      toggle.addEventListener("click", function() {
        var expanded = pre.textContent.length < text.length;
        pre.textContent = expanded ? text : displayLines.join("\n");
        toggle.textContent = expanded ? "R\u00E9duire \u25B2" : "Afficher tout (" + lines.length + " lignes) \u25BC";
      });
      container.appendChild(toggle);
    }
  }

  if (data.stdout) {
    _makeOutputBlock(data.stdout, "cmd-output", 30);
  }
  if (data.stderr) {
    _makeOutputBlock(data.stderr, "cmd-error", 15);
  }
  if (!data.stdout && !data.stderr && data.error) {
    _makeOutputBlock(data.error, "cmd-error", 15);
  }
  if (data.returncode !== undefined && data.returncode !== 0) {
    var rc = document.createElement("div");
    rc.className = "cmd-rc";
    rc.textContent = "Exit code: " + data.returncode;
    container.appendChild(rc);
  }
  return container;
}

function renderWebFetch(data) {
  var container = document.createElement("div");
  container.className = "fetch-container";

  var meta = document.createElement("div");
  meta.className = "fetch-meta";
  meta.innerHTML = '<span class="fetch-url">' + esc(data.url || "") + '</span>' +
    (data.content_length ? '<span class="fetch-size">' + data.content_length + ' chars</span>' : '') +
    (data.status_code ? '<span class="fetch-status">HTTP ' + data.status_code + '</span>' : '') +
    (data.truncated ? '<span class="fetch-truncated">(tronqué)</span>' : '');
  container.appendChild(meta);

  if (data.content) {
    var pre = document.createElement("pre");
    pre.className = "fetch-content";
    pre.textContent = data.content;
    container.appendChild(pre);
  } else if (data.error) {
    var err = document.createElement("pre");
    err.className = "cmd-error";
    err.textContent = data.error;
    container.appendChild(err);
  }
  return container;
}

// --- Conversation log download ---
logBtn.addEventListener("click", function() {
  var a = document.createElement("a");
  a.href = getUrl("/conversation/log");
  a.download = "conversation_" + new Date().toISOString().slice(0, 19).replace(/[:]/g, "-") + ".txt";
  a.click();
});

// --- Streaming par SSE ---
form.addEventListener("submit", async function(e) {
  e.preventDefault();
  var text = input.value.trim();
  if (!text || sending) return;
  if (text.startsWith("/")) {
    if (await handleCommand(text)) {
      input.value = "";
      input.style.height = "auto";
      submitBtn.disabled = true;
      return;
    }
  }
  input.value = "";
  input.style.height = "auto";
  sending = true;
  setStatus("busy");
  submitBtn.disabled = true;

  var ts = nowISO();
  var userMsg = { id: crypto.randomUUID(), role: "user", content: text, ts: ts };
  msgs.push(userMsg);
  addMsg(userMsg.id, "user", userMsg.content, { ts: ts });
  updateEmpty();

  var assistantId = crypto.randomUUID();
  var assistantEl = addTyping(assistantId);

  // Track current tools for this assistant message
  var currentToolId = null;

  try {
    var res = await fetch(getUrl("/chat/stream"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);

    var reader = res.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    var fullReply = "";
    var done = false;

    while (!done) {
      var result = await reader.read();
      done = result.done;
      buffer += decoder.decode(result.value || new Uint8Array(), { stream: !done });
      var lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        if (!line.startsWith("data: ")) continue;
        var raw = line.slice(6).trim();
        if (raw === "[DONE]") { done = true; break; }
        try {
          var ev = JSON.parse(raw);
          if (ev.type === "content") {
            fullReply += ev.data;
            if (assistantEl) {
              var bubble = assistantEl.querySelector(".bubble");
              if (bubble) {
                var p = bubble.querySelector("p");
                if (!p) {
                  bubble.innerHTML = "";
                  p = document.createElement("p");
                  bubble.appendChild(p);
                }
                p.innerHTML = markdownToHtml(fullReply);
                scrollToBottom();
              }
            }
          } else if (ev.type === "error") {
            showError(ev.data);
            if (assistantEl) assistantEl.remove();
          } else if (ev.type === "tool_call") {
            // Create tool card
            currentToolId = crypto.randomUUID();
            toolCalls[currentToolId] = { name: ev.name, args: ev.args, startTime: performance.now() };

            var bubble = assistantEl ? assistantEl.querySelector(".bubble") : null;
            if (bubble) {
              var card = renderToolCard(ev.name, ev.args, null);
              card.id = "tool-card-" + currentToolId;
              bubble.appendChild(card);
              scrollToBottom();
            }
          } else if (ev.type === "tool_result") {
            var toolId = Object.keys(toolCalls).pop();
            if (toolId) {
              toolCalls[toolId].endTime = performance.now();
              var cardEl = document.getElementById("tool-card-" + toolId);
              if (cardEl) {
                var newCard = renderToolCard(ev.name, toolCalls[toolId].args, ev.result);
                newCard.id = "tool-card-" + toolId;
                cardEl.replaceWith(newCard);
                scrollToBottom();
              }
            }
          }
        } catch (e) { /* ignore parse errors */ }
      }
    }

    removeMsg(assistantId);
    var assistantTs = nowISO();
    var assistantMsg = { id: assistantId, role: "assistant", content: fullReply, ts: assistantTs };
    msgs.push(assistantMsg);
    addMsg(assistantId, "assistant", fullReply, { ts: assistantTs });
    updateEmpty();
    setStatus("ready", currentMode);
  } catch (err) {
    if (assistantEl) removeMsg(assistantId);
    setStatus("error");
    showError("Impossible de joindre l'agent (" + err.message + ").");
  }
  sending = false;
  submitBtn.disabled = false;
});

input.addEventListener("keydown", function(e) {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
});
input.addEventListener("input", function() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 120) + "px";
  submitBtn.disabled = input.value.trim().length === 0;
});

clearBtn.addEventListener("click", clearChat);
conversation.addEventListener("scroll", function() {
  var nearBottom = conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 100;
  scrollBtn.classList.toggle("hide", nearBottom);
});
scrollBtn.addEventListener("click", scrollToBottom);

settingsBtn.addEventListener("click", function() {
  urlInput.value = localStorage.getItem(LS_KEY) || DEFAULT_URL;
  loadSettings();
  settingsOverlay.classList.remove("hide");
});
cancelBtn.addEventListener("click", function() { settingsOverlay.classList.add("hide"); });
saveBtn.addEventListener("click", saveSettings);
settingsOverlay.addEventListener("click", function(e) {
  if (e.target === settingsOverlay) settingsOverlay.classList.add("hide");
});

settingsMaxTokens.addEventListener("input", function() {
  settingsMaxTokensVal.textContent = settingsMaxTokens.value;
});
settingsTemp.addEventListener("input", function() {
  settingsTempVal.textContent = settingsTemp.value;
});

commandsBtn.addEventListener("click", showCommands);
closeCommandsBtn.addEventListener("click", function() { commandsOverlay.classList.add("hide"); });
commandsOverlay.addEventListener("click", function(e) {
  if (e.target === commandsOverlay) commandsOverlay.classList.add("hide");
});

// --- Stats ---
async function loadStats() {
  statsContent.innerHTML = '<p class="stats-loading">Chargement...</p>';
  try {
    var res = await fetch(getUrl("/stats"));
    if (!res.ok) throw new Error("HTTP " + res.status);
    var s = await res.json();
    var html = '<div class="stats-grid">';

    html += '<div class="stat-card"><span class="stat-label">Agent</span><span class="stat-value">' + esc(s.agent_name) + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Mode</span><span class="stat-value">' + esc(s.mode) + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Modèle</span><span class="stat-value stat-mono">' + esc(s.model) + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Fallback</span><span class="stat-value stat-mono">' + esc(s.fallback) + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Max tokens</span><span class="stat-value">' + esc(s.max_tokens) + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Température</span><span class="stat-value">' + esc(s.temperature) + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Outils</span><span class="stat-value">' + (s.tools ? 'Activés' : 'Désactivés') + '</span></div>';

    html += '<div class="stat-divider"></div>';

    html += '<div class="stat-card"><span class="stat-label">Messages</span><span class="stat-value">' + esc(s.messages.total) + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Utilisateur</span><span class="stat-value">' + esc(s.messages.user) + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Assistant</span><span class="stat-value">' + esc(s.messages.assistant) + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Système</span><span class="stat-value">' + esc(s.messages.system) + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Caractères totaux</span><span class="stat-value">' + esc(s.messages.total_chars.toLocaleString()) + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Moy. car./msg</span><span class="stat-value">' + esc(s.messages.avg_chars_per_msg.toLocaleString()) + '</span></div>';

    html += '<div class="stat-divider"></div>';

    html += '<div class="stat-card"><span class="stat-label">Tokens prompt</span><span class="stat-value">' + esc((s.tokens.prompt || 0).toLocaleString()) + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Tokens complétion</span><span class="stat-value">' + esc((s.tokens.completion || 0).toLocaleString()) + '</span></div>';
    html += '<div class="stat-card stat-highlight"><span class="stat-label">Tokens totaux</span><span class="stat-value">' + esc((s.tokens.total || 0).toLocaleString()) + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">RPM max</span><span class="stat-value">' + esc(s.rate_limits.max_rpm) + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Max tokens/req</span><span class="stat-value">' + esc(s.rate_limits.max_tokens_req) + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Max retries</span><span class="stat-value">' + esc(s.rate_limits.max_retries) + '</span></div>';

    html += '<div class="stat-divider"></div>';

    var memPct = s.context.usage_pct;
    var memBar = '<div class="stat-bar"><div class="stat-bar-fill" style="width:' + memPct + '%"></div></div>';
    html += '<div class="stat-card"><span class="stat-label">Contexte max</span><span class="stat-value">' + esc(s.context.max_chars.toLocaleString()) + ' chars</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Contexte utilisé</span><span class="stat-value">' + esc(memPct) + '%' + memBar + '</span></div>';
    html += '<div class="stat-card"><span class="stat-label">Durée session</span><span class="stat-value">' + esc(s.duration || '\u2014') + '</span></div>';

    html += '</div>';
    statsContent.innerHTML = html;
  } catch (err) {
    statsContent.innerHTML = '<p class="stats-error">Erreur: ' + esc(err.message) + '</p>';
  }
}

statsBtn.addEventListener("click", function() {
  if (!statsOverlay.classList.contains("hide")) {
    statsOverlay.classList.add("hide");
    return;
  }
  loadStats();
  statsOverlay.classList.remove("hide");
});
closeStatsBtn.addEventListener("click", function() { statsOverlay.classList.add("hide"); });
statsOverlay.addEventListener("click", function(e) {
  if (e.target === statsOverlay) statsOverlay.classList.add("hide");
});

async function browseFolder() {
  try {
    // Try pywebview native API (launcher)
    if (window.pywebview && window.pywebview.api && window.pywebview.api.select_folder) {
      var folder = await window.pywebview.api.select_folder();
      if (folder) {
        document.getElementById("settingsWorkspace").value = folder;
      }
      return;
    }
    // Try File System Access API (Chromium 86+)
    if ('showDirectoryPicker' in window) {
      var handle = await window.showDirectoryPicker();
      var wsEl = document.getElementById("settingsWorkspace");
      if ('relativePath' in handle) {
        wsEl.value = handle.relativePath;
      } else {
        // showDirectoryPicker doesn't give full path for security
        // But we can send the name + let backend resolve it
        // For now fallback to the file input method
        fallbackBrowse();
      }
      return;
    }
    // Fallback: create a hidden file input with webkitdirectory
    fallbackBrowse();
  } catch (e) {
    if (e.name === 'AbortError' || e.name === 'SecurityError') return;
    fallbackBrowse();
  }
}

function fallbackBrowse() {
  // Create a temporary input[type=file webkitdirectory]
  var input = document.createElement('input');
  input.type = 'file';
  input.setAttribute('webkitdirectory', '');
  input.setAttribute('directory', '');
  input.style.display = 'none';
  input.addEventListener('change', function(e) {
    var files = e.target.files;
    if (files && files.length > 0) {
      var path = files[0].webkitRelativePath;
      // Extract the root directory name from the first file's path
      var root = path.split('/')[0];
      // We don't have the full absolute path in browser :(
      // The user can still type it manually or use the pywebview bridge
      document.getElementById("settingsWorkspace").value = root;
    }
  });
  document.body.appendChild(input);
  input.click();
  document.body.removeChild(input);
}

browseBtn.addEventListener("click", browseFolder);

async function loadSettings() {
  try {
    var res = await fetch(getUrl("/settings"));
    if (!res.ok) return;
    var data = await res.json();
    lastSettings = data;
    settingsMode.value = data.mode;
    settingsModel.value = data.model || "";
    var mt = data.max_tokens || 4096;
    settingsMaxTokens.value = mt;
    settingsMaxTokensVal.textContent = mt;
    var t = data.temperature || 0.7;
    settingsTemp.value = t;
    settingsTempVal.textContent = t;
    var wsEl = document.getElementById("settingsWorkspace");
    if (wsEl) wsEl.value = data.workspace || "";
  } catch (e) { /* silent */ }
}

async function saveSettings() {
  var body = {};
  var newModel = settingsModel.value.trim();
  var newMt = parseInt(settingsMaxTokens.value, 10);
  var newTemp = parseFloat(settingsTemp.value);
  if (newModel && newModel !== lastSettings.model) body.model = newModel;
  if (newMt !== lastSettings.max_tokens) body.max_tokens = newMt;
  if (newTemp !== lastSettings.temperature) body.temperature = newTemp;
  var wsEl = document.getElementById("settingsWorkspace");
  if (wsEl) {
    var ws = wsEl.value.trim();
    if (ws && ws !== lastSettings.workspace) body.workspace = ws;
  }
  body.mode = settingsMode.value;
  try {
    var res = await fetch(getUrl("/settings"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    var data = await res.json();
    lastSettings = data;
    if (body.mode && body.mode !== currentMode) {
      currentMode = body.mode;
      setActiveModeBtn(currentMode);
      setStatus("ready", currentMode);
    }
    settingsOverlay.classList.add("hide");
  } catch (err) {
    showError("Erreur sauvegarde: " + err.message);
  }
}

updateEmpty();
setStatus("ready", "work");
loadSettings();
