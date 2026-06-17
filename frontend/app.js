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
var statsPanel = document.getElementById("statsPanel");
var statsContent = document.getElementById("statsContent");
var closeStatsBtn = document.getElementById("closeStatsBtn");

var sending = false;
var msgs = [];
var currentMode = "work";
var lastSettings = {};

var MODE_LABELS = { work: "Work", docs: "Docs", debug: "Debug", creative: "Creative" };

function fmtTime(iso) {
  if (!iso) return "";
  var d = new Date(iso);
  return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function nowISO() { return new Date().toISOString(); }

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

  html = html.replace(/<li><\/li>/g, "");
  html = html.replace(/(<li>.*?<\/li>)(?:\s*<li>.*?<\/li>)*/gs, function(m) {
    return "<ul>" + m + "</ul>";
  });

  html = html.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');

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
  if (cmd === "/reset") { await resetMemory(); return true; }
  if (cmd === "/help") { showCommands(); return true; }
  return false;
}

async function resetMemory() {
  try {
    var res = await fetch(getUrl("/reset"), { method: "POST" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    showSuccess("Mémoire réinitialisée.");
  } catch (err) {
    showError("Erreur reset: " + err.message);
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
    copyBtn.title = "Copier la r\u00e9ponse";
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
  shimmer.textContent = "L\u2019agent r\u00e9fl\u00e9chit\u2026";
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
              // update bubble progressively — replace typing with content
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
            // show tool call info
            var bubble = assistantEl ? assistantEl.querySelector(".bubble") : null;
            if (bubble) {
              var toolInfo = document.createElement("div");
              toolInfo.className = "tool-info";
              toolInfo.textContent = "\uD83D\uDD27 " + ev.name + "(...)";
              bubble.appendChild(toolInfo);
              scrollToBottom();
            }
          } else if (ev.type === "tool_result") {
            var bubble = assistantEl ? assistantEl.querySelector(".bubble") : null;
            if (bubble) {
              var toolRes = document.createElement("div");
              toolRes.className = "tool-result";
              toolRes.textContent = "\u2713 " + ev.name + " : " + (ev.result || "").slice(0, 80);
              bubble.appendChild(toolRes);
              scrollToBottom();
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
    html += '<div class="stat-card"><span class="stat-label">Durée session</span><span class="stat-value">' + esc(s.duration || '—') + '</span></div>';

    html += '</div>';
    statsContent.innerHTML = html;
  } catch (err) {
    statsContent.innerHTML = '<p class="stats-error">Erreur: ' + esc(err.message) + '</p>';
  }
}

function esc(str) {
  if (typeof str !== 'string') str = String(str);
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

statsBtn.addEventListener("click", function() {
  if (!statsPanel.classList.contains("hide")) {
    statsPanel.classList.add("hide");
    return;
  }
  loadStats();
  statsPanel.classList.remove("hide");
  scrollToBottom();
});
closeStatsBtn.addEventListener("click", function() { statsPanel.classList.add("hide"); });

updateEmpty();
setStatus("ready", "work");
loadSettings();
