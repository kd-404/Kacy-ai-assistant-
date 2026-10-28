// Kacy UI. Plain ES module, no build step: the backend serves this file as-is.
//
// Layout of this file:
//   1. helpers        small DOM + fetch utilities
//   2. markdown       a tiny, XSS-safe renderer for the model's answers
//   3. state          what the page knows (who you are, chats, the open chat)
//   4. rendering      sidebar, thread, approval cards, activity pane
//   5. actions        send, stop, regenerate, send/discard a draft
//   6. boot           sign-in check and event wiring

// ---------- 1. helpers ----------

const $ = (id) => document.getElementById(id);
const H = { "Content-Type": "application/json", "X-Kacy": "1" }; // X-Kacy: cross-site request guard

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text; // textContent, never innerHTML, for anything user/model supplied
  return e;
}

function icon(name, cls = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "ic " + cls);
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { ...H, ...(opts.headers || {}) } });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  return { ok: res.ok, status: res.status, data };
}

function store(key, value) {
  // localStorage can throw (private windows, blocked storage); the UI works without it.
  try { if (value !== undefined) localStorage.setItem(key, value); return localStorage.getItem(key); } catch { return null; }
}

let toastTimer;
function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 2600);
}

// ---------- 2. markdown ----------
// Escape first, then add tags. Because every character from the model is escaped
// before any markup is added, a reply can never inject HTML or script.

const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function inline(raw) {
  // Split out `code` first so ** or * inside code is left alone.
  return raw.split(/(`[^`]+`)/).map((part) => {
    if (part.length > 1 && part.startsWith("`") && part.endsWith("`")) return `<code>${esc(part.slice(1, -1))}</code>`;
    return esc(part)
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  }).join("");
}

function markdown(src) {
  const lines = src.replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) { // fenced code block (an unclosed one runs to the end, which suits streaming)
      const code = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]);
      i++;
      out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
    } else if (/^#{1,6}\s/.test(line)) {
      out.push(`<h3>${inline(line.replace(/^#{1,6}\s+/, ""))}</h3>`);
      i++;
    } else if (/^\s*[-*•]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) items.push(`<li>${inline(lines[i++].replace(/^\s*[-*•]\s+/, ""))}</li>`);
      out.push(`<ul>${items.join("")}</ul>`);
    } else if (/^\s*\d+[.)]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) items.push(`<li>${inline(lines[i++].replace(/^\s*\d+[.)]\s+/, ""))}</li>`);
      out.push(`<ol>${items.join("")}</ol>`);
    } else if (line.startsWith(">")) {
      const quote = [];
      while (i < lines.length && lines[i].startsWith(">")) quote.push(lines[i++].replace(/^>\s?/, ""));
      out.push(`<blockquote>${inline(quote.join(" "))}</blockquote>`);
    } else if (!line.trim()) {
      i++;
    } else {
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^(```|#{1,6}\s|\s*[-*•]\s|\s*\d+[.)]\s|>)/.test(lines[i])) para.push(lines[i++]);
      out.push(`<p>${para.map(inline).join("<br>")}</p>`);
    }
  }
  return out.join("");
}

// ---------- 3. state ----------

const state = {
  me: null,
  settings: { display_name: "", tone: "balanced", instructions: "" },
  agents: [],
  chats: [],
  query: "",
  view: "chat",                       // "chat" | "agents"
  current: { id: null, title: "New chat", agent: "kacy", messages: [] },
  newChatAgent: "kacy",
  pendingActions: new Set(),          // approval-card ids still waiting for the person
  actionView: {},                     // id -> latest card state returned by the server
  busy: false,
  abort: null,
  selected: null,                     // activity shown in the right pane
  reveal: null,                       // running typewriter, so Stop can finish it
};

const agentName = (id) => (state.agents.find((a) => a.id === id) || { name: "Kacy" }).name;

// ---------- 4. rendering ----------

// Sidebar -----------------------------------------------------------

function groupOf(ts) {
  const day = 86400, now = Date.now() / 1000;
  const startToday = new Date().setHours(0, 0, 0, 0) / 1000;
  if (ts >= startToday) return "Today";
  if (ts >= startToday - day) return "Yesterday";
  if (ts >= now - 7 * day) return "Previous 7 days";
  return "Older";
}

function renderSidebar() {
  const list = $("chatList");
  list.textContent = "";
  if (!state.chats.length) {
    list.append(el("p", "empty-list", state.query ? "No chats match that search." : "Your chats will show up here."));
    return;
  }
  const sections = state.query ? [["Results", state.chats]] : [];
  if (!state.query) {
    const pinned = state.chats.filter((c) => c.pinned);
    if (pinned.length) sections.push(["Pinned", pinned]);
    const order = ["Today", "Yesterday", "Previous 7 days", "Older"];
    const rest = state.chats.filter((c) => !c.pinned);
    for (const g of order) {
      const items = rest.filter((c) => groupOf(c.updated) === g);
      if (items.length) sections.push([g, items]);
    }
  }
  for (const [label, items] of sections) {
    list.append(el("div", "group-label", label));
    for (const c of items) list.append(chatItem(c));
  }
}

function chatItem(c) {
  const row = el("div", "chat-item" + (c.id === state.current.id && state.view === "chat" ? " on" : ""));
  row.setAttribute("role", "listitem");
  const open = el("button", "open");
  if (c.pinned) open.append(el("span", "pin-dot"));
  open.append(document.createTextNode(c.title));
  open.title = c.title;
  open.onclick = () => openChat(c.id);
  const more = el("button", "more");
  more.setAttribute("aria-label", "Chat options");
  more.append(icon("more"));
  more.onclick = (e) => { e.stopPropagation(); showMenu(more, [
    { label: c.pinned ? "Unpin" : "Pin", icon: "pin", run: () => patchChat(c.id, { pinned: !c.pinned }) },
    { label: "Rename", icon: "edit", run: () => renameInline(row, c) },
    { label: "Delete", icon: "trash", danger: true, run: () => deleteChat(c) },
  ]); };
  row.append(open, more);
  return row;
}

function renameInline(row, c) {
  const input = el("input");
  input.value = c.title;
  input.maxLength = 80;
  input.style.cssText = "flex:1;margin:0 6px;height:30px;border:1px solid var(--thread);border-radius:8px;padding:0 8px;background:var(--bg);outline:0";
  row.textContent = "";
  row.append(input);
  input.focus(); input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return; done = true;
    if (save && input.value.trim() && input.value.trim() !== c.title) await patchChat(c.id, { title: input.value.trim() });
    else renderSidebar();
  };
  input.onkeydown = (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); };
  input.onblur = () => finish(true);
}

// Floating menu ------------------------------------------------------

function showMenu(anchor, items, { above = false } = {}) {
  const menu = $("menu");
  menu.textContent = "";
  for (const item of items) {
    const b = el("button", item.danger ? "danger" : "");
    b.setAttribute("role", "menuitem");
    if (item.icon) b.append(icon(item.icon));
    b.append(document.createTextNode(item.label));
    b.onclick = () => { hideMenu(); item.run(); };
    menu.append(b);
  }
  menu.hidden = false;
  const r = anchor.getBoundingClientRect();
  const w = menu.offsetWidth, h = menu.offsetHeight;
  const left = Math.min(Math.max(8, r.left), innerWidth - w - 8);
  const top = above ? Math.max(8, r.top - h - 6) : Math.min(r.bottom + 4, innerHeight - h - 8);
  menu.style.left = left + "px";
  menu.style.top = top + "px";
}
const hideMenu = () => ($("menu").hidden = true);

// Thread ------------------------------------------------------------

function greeting() {
  const h = new Date().getHours();
  const part = h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  const name = (state.settings.display_name || state.me.name || "").split(" ")[0];
  return name ? `${part}, ${name}` : part;
}

function renderThread({ keepScroll = false } = {}) {
  const thread = $("thread");
  const scroller = $("scroll");
  const before = scroller.scrollTop;
  thread.textContent = "";
  const c = state.current;
  $("chatTitle").textContent = c.title;
  const a = c.id ? c.agent : state.newChatAgent;
  $("agentChip").textContent = a === "kacy" ? "" : agentName(a);
  $("agentPickName").textContent = agentName(a);
  $("agentPick").disabled = !!c.id;

  const empty = !c.messages.length;
  $("viewChat").classList.toggle("is-empty", empty);
  const starters = $("starters");
  starters.textContent = "";
  if (empty) {
    const hello = el("div", "hello");
    const mark = el("span", "mark");
    mark.innerHTML = '<svg viewBox="0 0 32 32"><path d="M9 8v16M9 16c5 0 7-4 14-8M9 16c5 0 7 4 14 8"/></svg>';
    hello.append(mark, el("h2", null, greeting()), el("p", null, "Ask about company policy, your calendar or inbox, or hand me something to draft. I'll ask before anything leaves the building."));
    thread.append(hello);
    const agent = state.agents.find((x) => x.id === a);
    for (const text of (agent ? agent.starters : []).slice(0, 4)) {
      const b = el("button", "starter");
      b.append(icon(starterIcon(text)), document.createTextNode(text));
      b.onclick = () => send(text);
      starters.append(b);
    }
  }

  c.messages.forEach((m, idx) => thread.append(messageNode(m, idx === c.messages.length - 1)));
  if (state.busy) thread.append(workingNode());
  if (keepScroll) scroller.scrollTop = before; else scrollToEnd();
}

function starterIcon(text) {
  const t = text.toLowerCase();
  if (/email|inbox|reply|mail/.test(t)) return "mail";
  if (/slot|schedule|meeting|calendar|brief/.test(t)) return "calendar";
  if (/document|draft a|plan/.test(t)) return "doc";
  if (/ticket|vpn|access|laptop/.test(t)) return "ticket";
  return "check";
}

function scrollToEnd() { const s = $("scroll"); s.scrollTop = s.scrollHeight; }

function workingNode() {
  const row = el("div", "msg kacy");
  row.append(faceNode());
  const body = el("div", "body working");
  const dots = el("span", "dots"); dots.append(el("i"), el("i"), el("i"));
  body.append(dots, el("span", null, "Kacy is checking and working on it"));
  row.append(body);
  return row;
}

function faceNode() {
  const f = el("span", "face");
  f.innerHTML = '<svg viewBox="0 0 32 32"><path d="M9 8v16M9 16c5 0 7-4 14-8M9 16c5 0 7 4 14 8"/></svg>';
  return f;
}

function messageNode(m, isLast) {
  if (m.role === "user") {
    const row = el("div", "msg user");
    row.append(el("div", "bubble", m.content));
    return row;
  }
  const row = el("div", "msg kacy");
  row.append(faceNode());
  const body = el("div", "body");
  if (m.role === "error") {
    const n = el("div", "notice" + (m.info ? " info" : ""));
    n.append(el("b", null, m.title), el("span", null, m.content));
    body.append(n);
    row.append(body);
    return row;
  }
  const prose = el("div", "prose");
  prose.innerHTML = markdown(m.content);
  body.append(prose);
  for (const a of (m.meta && m.meta.actions) || []) body.append(approvalCard(a));

  const actions = el("div", "actions");
  const copy = el("button", "icon-btn"); copy.setAttribute("aria-label", "Copy answer"); copy.title = "Copy"; copy.append(icon("copy"));
  copy.onclick = async () => { try { await navigator.clipboard.writeText(m.content); toast("Copied"); } catch { toast("Copy blocked by the browser"); } };
  actions.append(copy);
  if (isLast && state.current.id) {
    const redo = el("button", "icon-btn"); redo.setAttribute("aria-label", "Regenerate answer"); redo.title = "Regenerate"; redo.append(icon("redo"));
    redo.onclick = () => send("", { regenerate: true });
    actions.append(redo);
  }
  const act = m.meta && m.meta.activity;
  if (act) {
    if (act.steps.some((s) => s.status === "denied")) actions.append(el("span", "chip denied", "Blocked by role check"));
    if (act.steps.some((s) => s.id === "cache" && s.status === "ok")) actions.append(el("span", "chip cached", "From cache"));
    const info = el("button", "icon-btn"); info.setAttribute("aria-label", "Show activity"); info.title = "Show activity"; info.append(icon("panel"));
    info.onclick = () => { state.selected = act; renderPane(); $("app").classList.remove("no-pane"); };
    actions.append(info);
  }
  body.append(actions);
  row.append(body);
  return row;
}

// Approval card ------------------------------------------------------
// The model can only queue a card. This card's button calls the backend route that
// actually does the thing (send, create, file, record), as the signed-in person.

const KIND_ICON = { email: "mail", event: "calendar", document: "doc", leave_request: "check", leave_decision: "check", ticket: "ticket" };

function approvalCard(a) {
  a = state.actionView[a.id] || a;           // newest state we know about
  state.actionView[a.id] = a;
  const waiting = a.status === "pending_approval";
  const done = a.status === "done";
  const card = el("div", "approval" + (done ? " done" : waiting ? "" : " gone"));
  const head = el("header");
  head.append(icon(done ? "check" : KIND_ICON[a.kind] || "mail"));
  head.append(el("span", null, done ? "Done" : a.status === "discarded" ? "Discarded" : a.status === "failed" ? "Could not be applied" : a.title + " · waiting for your approval"));
  card.append(head);
  const dl = el("dl");
  for (const [k, v] of a.view.rows) dl.append(el("dt", null, k), el("dd", null, v));
  card.append(dl);
  if (a.view.body) card.append(el("pre", null, a.view.body));

  const msg = a.result && a.result.message;
  if (msg) {
    const r = el("p", "result", msg);
    for (const [label, url] of [["Open", a.result.link], ["Join with Meet", a.result.meet]]) {
      if (url && /^https:\/\//.test(url)) { // only ever link to https, whatever came back
        const link = el("a", null, label); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
        r.append(" ", link);
      }
    }
    card.append(r);
  }
  if (waiting) {
    const foot = el("footer");
    const go = el("button", "primary", a.confirm_label);
    const discard = el("button", "ghost", "Discard");
    const err = el("span", "err");
    go.onclick = () => actionRequest(a, "approve", [go, discard], err);
    discard.onclick = () => actionRequest(a, "discard", [go, discard], err);
    foot.append(go, discard, err);
    card.append(foot);
  }
  return card;
}

async function actionRequest(a, what, buttons, errEl) {
  buttons.forEach((b) => (b.disabled = true));
  errEl.textContent = "";
  const r = await api(`/api/actions/${a.id}/${what}`, { method: "POST" });
  if (r.ok) {
    state.actionView[a.id] = r.data;
    state.pendingActions.delete(a.id);
    toast(what === "approve" ? "Done" : "Discarded");
    renderThread({ keepScroll: true });
    if (state.onAction) state.onAction();   // Desk windows redraw their own cards
  } else {
    buttons.forEach((b) => (b.disabled = false));
    const detail = r.data && r.data.detail;
    errEl.textContent = typeof detail === "string" ? detail : "Could not complete that. Try again.";
  }
}

// Activity pane: the thread of checks behind an answer ----------------

function renderPane() {
  const body = $("paneBody");
  body.textContent = "";
  $("paneMs").textContent = "";
  if (state.busy) {
    const ol = el("ol", "thread-line");
    const k = el("li", "knot live");
    k.append(el("b", null, "Working"), el("span", null, "Checking your message, then asking Kacy"));
    ol.append(k); body.append(ol);
    return;
  }
  const act = state.selected;
  if (!act) {
    body.append(el("p", "pane-empty", "Send a message to see which checks ran, which tools Kacy used and what waits for your approval."));
    return;
  }
  if (act.ms != null) $("paneMs").textContent = (act.ms / 1000).toFixed(1) + " s";
  const ol = el("ol", "thread-line");
  for (const s of act.steps) {
    const k = el("li", "knot " + s.status);
    k.append(el("b", null, s.label), el("span", null, s.detail));
    ol.append(k);
  }
  body.append(ol);
}

// ---------- 5. actions ----------

function setBusy(b) {
  state.busy = b;
  const btn = $("sendBtn");
  btn.replaceChildren(icon(b ? "stop" : "send"));
  btn.setAttribute("aria-label", b ? "Stop" : "Send message");
  btn.type = b ? "button" : "submit";
  btn.disabled = b ? false : !$("input").value.trim();
}

async function send(text, { regenerate = false } = {}) {
  text = (text || "").trim();
  if (state.busy || (!text && !regenerate)) return;
  if (state.view !== "chat") showView("chat");
  finishReveal();
  const c = state.current;

  if (regenerate) {
    if (c.messages.length && c.messages[c.messages.length - 1].role !== "user") c.messages.pop();
  } else {
    c.messages.push({ role: "user", content: text });
    $("input").value = ""; autosize();
  }
  state.selected = null;
  setBusy(true);
  renderThread(); renderPane();

  state.abort = new AbortController();
  let r;
  try {
    r = await api("/chat", {
      method: "POST",
      signal: state.abort.signal,
      body: JSON.stringify({ message: text, chat_id: c.id, agent: c.id ? null : state.newChatAgent, regenerate }),
    });
  } catch (err) {
    setBusy(false);
    if (err.name === "AbortError") {
      c.messages.push({ role: "error", info: true, title: "Stopped", content: "You stopped this answer. If Kacy had already started, it may still appear in this chat when you reopen it." });
    } else {
      c.messages.push({ role: "error", title: "Can't reach Kacy", content: "The connection dropped. Check your network and send the message again." });
    }
    renderThread(); renderPane();
    return;
  }
  setBusy(false);

  if (r.status === 401) { location.reload(); return; }
  if (!r.ok) {
    const d = r.data && r.data.detail;
    if (d && d.activity) state.selected = d.activity;
    c.messages.push(errorMessage(r.status, d));
    renderThread(); renderPane();
    return;
  }

  const d = r.data;
  if (!c.id) c.agent = state.newChatAgent;
  c.id = d.chat_id; c.title = d.title;
  state.pendingActions = new Set(d.pending_actions.map((x) => x.id));
  const msg = { role: "assistant", content: d.reply, meta: { activity: d.activity, actions: d.new_actions } };
  c.messages.push(msg);
  state.selected = d.activity;
  history.replaceState(null, "", "#/c/" + c.id);
  renderThread(); renderPane();
  revealLast(msg);
  refreshChats();
}

function errorMessage(status, d) {
  if (d && d.error === "blocked") {
    return { role: "error", title: "Prompt Shields stopped this message",
      content: `It looked like a ${String(d.kind).replace(/_/g, " ")} attempt. Rephrase it without asking Kacy to ignore its rules.` };
  }
  if (d && d.error === "content_filtered") {
    const cats = Object.keys(d.flagged || {}).join(", ");
    return { role: "error", title: "Azure's content filter flagged this message",
      content: (cats ? `Category: ${cats}. ` : "") + "Rephrase it and try again. This filter can flag harmless wording." };
  }
  if (status === 403) return { role: "error", title: "Your role can't do that", content: typeof d === "string" ? d : "Ask a manager or admin if you need access." };
  return { role: "error", title: "Kacy ran into a problem", content: `The server answered ${status}. Send the message again; if it keeps failing, check the backend log.` };
}

// Typewriter: reveals the finished answer quickly. Instant when the user prefers reduced motion.
function revealLast(msg) {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const nodes = $("thread").querySelectorAll(".msg.kacy .prose");
  const target = nodes[nodes.length - 1];
  if (!target || reduce || msg.content.length < 4) return;
  let shown = 0;
  const step = Math.max(3, Math.ceil(msg.content.length / 110));
  const token = {};
  state.reveal = token;
  state.busy = true; setBusy(true);
  const tick = () => {
    if (state.reveal !== token) return;
    shown = Math.min(msg.content.length, shown + step);
    target.innerHTML = markdown(msg.content.slice(0, shown));
    scrollToEnd();
    if (shown < msg.content.length) requestAnimationFrame(tick);
    else { state.reveal = null; setBusy(false); }
  };
  tick();
}

function finishReveal() {
  if (!state.reveal) return;
  state.reveal = null;
  setBusy(false);
  renderThread({ keepScroll: true });
}

function stop() {
  if (state.reveal) finishReveal();
  else if (state.abort) state.abort.abort();
}

// Chats ----------------------------------------------------------------

async function refreshChats() {
  const r = await api("/api/chats" + (state.query ? "?q=" + encodeURIComponent(state.query) : ""));
  if (r.ok) { state.chats = r.data; renderSidebar(); }
}

async function openChat(id) {
  if (state.busy) return;
  const r = await api("/api/chats/" + id);
  if (!r.ok) { toast("That chat no longer exists"); history.replaceState(null, "", "#"); return newChat(); }
  const dr = await api("/api/actions");
  if (dr.ok) state.pendingActions = new Set(dr.data.map((x) => x.id));
  state.actionView = {};
  state.current = { id: r.data.id, title: r.data.title, agent: r.data.agent, messages: r.data.messages };
  const lastAnswer = [...r.data.messages].reverse().find((m) => m.role === "assistant");
  state.selected = lastAnswer && lastAnswer.meta ? lastAnswer.meta.activity || null : null;
  history.replaceState(null, "", "#/c/" + id);
  showView("chat");
  renderThread(); renderPane(); renderSidebar(); closeSidebarIfNarrow();
}

function newChat(agent = "kacy") {
  if (state.reveal) finishReveal();
  state.current = { id: null, title: "New chat", agent, messages: [] };
  state.newChatAgent = agent;
  state.selected = null;
  history.replaceState(null, "", "#");
  showView("chat");
  renderThread(); renderPane(); renderSidebar(); closeSidebarIfNarrow();
  $("input").focus();
}

async function patchChat(id, body) {
  const r = await api("/api/chats/" + id, { method: "PATCH", body: JSON.stringify(body) });
  if (!r.ok) toast("Could not update that chat");
  if (state.current.id === id && body.title) { state.current.title = body.title; $("chatTitle").textContent = body.title; }
  refreshChats();
}

async function deleteChat(c) {
  if (!confirm(`Delete "${c.title}"? This can't be undone.`)) return;
  const r = await api("/api/chats/" + c.id, { method: "DELETE" });
  if (!r.ok) return toast("Could not delete that chat");
  if (state.current.id === c.id) newChat();
  refreshChats();
}

// Views, sidebar, theme ---------------------------------------------------

function showView(v) {
  state.view = v;
  $("viewChat").hidden = v !== "chat";
  $("viewAgents").hidden = v !== "agents";
  $("viewDesk").hidden = v !== "desk";
  $("navAgents").classList.toggle("on", v === "agents");
  $("segChat").classList.toggle("on", v === "chat");
  $("segDesk").classList.toggle("on", v === "desk");
  $("chatTitle").textContent = v === "agents" ? "Agents" : v === "desk" ? "Desk" : state.current.title;
  if (v === "desk") import("/ui/desk.js").then((m) => m.mount());
  if (v === "agents") $("agentChip").textContent = "";
  renderSidebar();
}

function renderAgents() {
  const grid = $("agentGrid");
  grid.textContent = "";
  for (const a of state.agents) {
    const card = el("button", "agent-card");
    const badge = el("span", "initial"); badge.append(icon(a.icon || "agents"));
    const can = el("ul", "can");
    for (const t of a.can || []) can.append(el("li", null, t));
    card.append(badge, el("b", null, a.name), el("span", null, a.blurb), can);
    card.onclick = () => newChat(a.id);
    grid.append(card);
  }
}

const narrow = () => matchMedia("(max-width: 760px)").matches;
function closeSidebarIfNarrow() { if (narrow()) $("app").classList.add("no-sidebar"); }

function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  const dark = t === "dark" || (t === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  $("themeBtn").replaceChildren(icon(dark ? "sun" : "moon"));
}

function autosize() {
  const t = $("input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 220) + "px";
  if (!state.busy) $("sendBtn").disabled = !t.value.trim();
}

// ---------- 6. boot ----------

async function showGate() {
  const gate = $("gate");
  gate.hidden = false;
  const card = el("div", "gate-card");
  const mark = el("span", "mark");
  mark.innerHTML = '<svg viewBox="0 0 32 32"><path d="M9 8v16M9 16c5 0 7-4 14-8M9 16c5 0 7 4 14 8"/></svg>';
  const google = el("a", "google", "Continue with Google");
  google.href = "/auth/google/login";
  card.append(mark, el("h1", null, "Kacy, the assistant for Loom & Light"),
    el("p", null, "Sign in with your company Google account. Your role decides what Kacy can look up for you, and Kacy connects to your Gmail, Calendar and Drive."),
    google, el("p", "muted", "Access is limited to approved accounts."));

  const personas = await api("/api/personas");
  if (personas.ok) {
    const box = el("div", "personas");
    box.append(el("p", null, "Or try a demo account with sample company data. Nothing is sent from a demo account."));
    const row = el("div", "persona-row");
    for (const p of personas.data) {
      const b = el("button", "ghost", `${p.id[0].toUpperCase() + p.id.slice(1)} · ${p.role}`);
      b.onclick = async () => {
        b.disabled = true;
        const r = await api("/auth/demo", { method: "POST", body: JSON.stringify({ persona: p.id }) });
        if (r.ok) location.reload(); else { b.disabled = false; toast("Could not start the demo"); }
      };
      row.append(b);
    }
    box.append(row);
    card.append(box);
  }
  gate.append(card);
}

async function boot() {
  applyTheme(store("kacy-theme") || "dark");
  const me = await api("/me");
  if (!me.ok || !me.data.signed_in) return showGate();
  state.me = me.data;

  $("whoName").textContent = me.data.name || me.data.email;
  $("whoRole").textContent = me.data.role + (me.data.demo ? " · demo data" : "");
  const need = ["gmail.send", "gmail.readonly", "calendar.events", "calendar.freebusy", "drive.file"];
  if (!me.data.demo && need.some((n) => !me.data.scopes.includes(n))) $("reconnect").hidden = false;
  $("avatar").textContent = (me.data.name || me.data.email)[0].toUpperCase();
  $("app").hidden = false;
  if (narrow()) $("app").classList.add("no-sidebar");
  if (innerWidth <= 1180) $("app").classList.add("no-pane");

  const [settings, agents, pending] = await Promise.all([api("/api/settings"), api("/api/agents"), api("/api/actions")]);
  if (settings.ok) state.settings = settings.data;
  if (agents.ok) state.agents = agents.data;
  if (pending.ok) state.pendingActions = new Set(pending.data.map((x) => x.id));
  renderAgents();
  await refreshChats();

  const m = location.hash.match(/^#\/c\/([a-f0-9]+)$/);
  if (m) await openChat(m[1]); else { renderThread(); renderPane(); }
  $("input").focus();
}

// events
$("composer").addEventListener("submit", (e) => { e.preventDefault(); send($("input").value); });
$("sendBtn").addEventListener("click", (e) => { if (state.busy) { e.preventDefault(); stop(); } });
$("input").addEventListener("input", autosize);
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!state.busy) send($("input").value); }
});
$("newChat").onclick = () => newChat();
$("segChat").onclick = () => showView("chat");
$("segDesk").onclick = () => { showView("desk"); closeSidebarIfNarrow(); };
$("navAgents").onclick = () => { showView("agents"); closeSidebarIfNarrow(); };
$("agentPick").onclick = () => showMenu($("agentPick"), state.agents.map((a) => ({
  label: a.name, icon: "agents", run: () => { state.newChatAgent = a.id; renderThread({ keepScroll: true }); },
})), { above: true });

let searchTimer;
$("search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.query = e.target.value.trim(); refreshChats(); }, 180);
});

$("sbClose").onclick = () => $("app").classList.add("no-sidebar");
$("sbOpen").onclick = () => $("app").classList.remove("no-sidebar");
$("sbOpenWide").onclick = () => $("app").classList.remove("no-sidebar");
$("scrim").onclick = () => $("app").classList.add("no-sidebar");
$("paneBtn").onclick = () => $("app").classList.toggle("no-pane");
$("paneClose").onclick = () => $("app").classList.add("no-pane");

$("themeBtn").onclick = () => {
  const dark = document.documentElement.dataset.theme === "dark" ||
    (document.documentElement.dataset.theme === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  const next = dark ? "light" : "dark";
  store("kacy-theme", next); applyTheme(next);
};
$("signOut").onclick = async () => { await api("/auth/logout", { method: "POST" }); location.href = "/"; };

// Customize Kacy
$("navCustomize").onclick = () => {
  $("cName").value = state.settings.display_name || "";
  $("cTone").value = state.settings.tone || "balanced";
  $("cInstr").value = state.settings.instructions || "";
  $("customize").showModal();
  closeSidebarIfNarrow();
};
$("cCancel").onclick = () => $("customize").close();
$("customizeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await api("/api/settings", { method: "PUT", body: JSON.stringify({
    display_name: $("cName").value, tone: $("cTone").value, instructions: $("cInstr").value }) });
  if (!r.ok) return toast("Could not save your settings");
  state.settings = r.data;
  $("customize").close();
  toast("Saved. New messages will use these settings.");
  if (!state.current.messages.length) renderThread();
});

// global shortcuts + closing the floating menu
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("app").classList.remove("no-sidebar"); $("search").focus(); }
  if (e.key === "Escape") hideMenu();
});
document.addEventListener("click", (e) => { if (!$("menu").contains(e.target)) hideMenu(); }, true);

boot();

// Desk (desk.js) reuses these instead of copying them.
export { $, api, el, icon, markdown, approvalCard, faceNode, toast, state, refreshChats, showMenu, openChat };
