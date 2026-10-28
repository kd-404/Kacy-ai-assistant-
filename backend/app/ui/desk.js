// Desk: several chats open side by side as floating windows.
//
//   window  = one real Kacy chat (same history, tools, approval cards and role checks as the Chat view)
//   sticky  = a minimized window, shown in the tray with a live status
//   wire    = a link between two chats. The listening chat reads the other chat's short brief.
//
// Loaded on first use (app.js imports it when you open Desk) and shares app.js's state and helpers.

import { $, api, el, icon, markdown, approvalCard, faceNode, toast, state, refreshChats, showMenu } from "/ui/app.js";

const D = {
  wins: new Map(),      // key -> window; key is the chat id, or "new-N" until the first answer exists
  links: [],            // [{id, a, b, two_way}] where a is the source and b the listener
  z: 10,
  seq: 0,
  mounted: false,
  raf: 0,
  pop: null,            // the open link popover, if any
};

const canvas = () => $("deskCanvas");
const narrow = () => matchMedia("(max-width: 760px)").matches;

// ---------- loading ----------

export async function mount() {
  if (!D.mounted) {
    D.mounted = true;
    $("deskNew").onclick = () => { const w = addWindow({}); renderAll(); w.node.querySelector("textarea").focus(); };
    $("deskAdd").onclick = addExisting;
    state.onAction = () => { D.wins.forEach(renderMsgs); renderTray(); };
    new ResizeObserver(() => drawWires()).observe(canvas());
    document.addEventListener("pointerdown", (e) => { if (D.pop && !D.pop.contains(e.target) && !e.target.closest(".wirebtn")) closePop(); });
  }
  await load();
}

async function load() {
  const r = await api("/api/desk");
  if (!r.ok) return toast("Could not load your Desk");
  const open = new Set();
  await Promise.all(r.data.windows.map(async (row) => {
    open.add(row.id);
    let w = D.wins.get(row.id);
    if (!w) {
      const g = row.desk;
      w = addWindow({ chatId: row.id, title: row.title, agent: row.agent, ...g });
      const c = await api("/api/chats/" + row.id);
      if (c.ok) w.messages = c.data.messages;
    } else {
      w.title = row.title;
    }
    D.z = Math.max(D.z, w.z);
  }));
  // Windows closed elsewhere (another tab) disappear here too. Unsent new windows stay.
  for (const [key, w] of D.wins) if (w.chatId && !open.has(key)) removeWindow(w);
  D.links = r.data.links.filter((l) => D.wins.has(l.a) && D.wins.has(l.b));
  renderAll();
}

// ---------- windows ----------

function addWindow(o) {
  const n = D.wins.size;
  const cw = canvas().clientWidth || 900;
  const w = {
    key: o.chatId || "new-" + ++D.seq,
    chatId: o.chatId || null,
    title: o.title || "New chat",
    agent: o.agent || "kacy",
    x: o.x ?? 24 + (n % 4) * 44,
    y: o.y ?? 16 + (n % 4) * 36,
    w: o.w ?? Math.min(500, Math.max(320, Math.floor((cw - 72) / 2))),
    h: o.h ?? 540,
    z: o.z ?? ++D.z,
    min: !!o.min,
    messages: [],
    busy: false,
    node: null,
    msgsEl: null,
  };
  D.wins.set(w.key, w);
  buildNode(w);
  return w;
}

function removeWindow(w) {
  w.node?.remove();
  D.wins.delete(w.key);
  D.links = D.links.filter((l) => l.a !== w.key && l.b !== w.key);
}

function buildNode(w) {
  const node = el("article", "dwin");
  node.dataset.key = w.key;

  const head = el("header", "dhead");
  const port = el("button", "port"); port.type = "button";
  port.title = "Drag onto another window to link them"; port.setAttribute("aria-label", "Link to another window");
  port.append(icon("link"));
  port.addEventListener("pointerdown", (e) => startLink(e, w));
  // The title doubles as the drag handle, so it only becomes editable on a double-click.
  const title = el("input", "dtitle"); title.value = w.title; title.setAttribute("aria-label", "Chat name (double-click to rename)"); title.maxLength = 80; title.readOnly = true;
  title.ondblclick = () => { title.readOnly = false; title.focus(); title.select(); };
  title.onblur = () => { title.readOnly = true; };
  title.onkeydown = (e) => { if (e.key === "Enter") title.blur(); };
  title.onchange = async () => {
    w.title = title.value.trim() || "Untitled chat";
    if (w.chatId) { await api("/api/chats/" + w.chatId, { method: "PATCH", body: JSON.stringify({ title: w.title }) }); refreshChats(); }
    renderTray();
  };
  let agent;
  if (w.chatId) {
    agent = el("span", "dagent", (state.agents.find((a) => a.id === w.agent) || { name: "Kacy" }).name);
  } else {
    agent = el("select", "dagent");
    agent.setAttribute("aria-label", "Agent");
    for (const a of state.agents) { const o = el("option", null, a.name); o.value = a.id; agent.append(o); }
    agent.value = w.agent;
    agent.onchange = () => (w.agent = agent.value);
  }
  const badge = el("span", "dlinks"); badge.hidden = true;
  const min = el("button", "icon-btn"); min.type = "button"; min.title = "Minimize to a sticky note"; min.setAttribute("aria-label", "Minimize"); min.append(icon("min"));
  min.onclick = () => { w.min = true; saveGeom(w); renderAll(); };
  const close = el("button", "icon-btn"); close.type = "button"; close.title = "Remove from Desk (the chat stays in your history)"; close.setAttribute("aria-label", "Remove from Desk"); close.append(icon("x"));
  close.onclick = () => closeWindow(w);
  head.append(port, title, agent, badge, min, close);

  const msgs = el("div", "dmsgs");
  const form = el("form", "dcomposer"); form.autocomplete = "off";
  const ta = el("textarea"); ta.rows = 1; ta.placeholder = "Message Kacy"; ta.setAttribute("aria-label", "Message");
  const send = el("button", "send"); send.type = "submit"; send.setAttribute("aria-label", "Send"); send.append(icon("send"));
  form.append(ta, send);
  form.onsubmit = (e) => { e.preventDefault(); const t = ta.value.trim(); if (t && !w.busy) { ta.value = ""; ta.style.height = ""; sendMsg(w, t); } };
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); } });
  ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 120) + "px"; });

  node.append(head, msgs, form);
  node.addEventListener("pointerdown", () => front(w));
  head.addEventListener("pointerdown", (e) => startDrag(e, w));
  new ResizeObserver(() => {
    if (w.min || !node.offsetWidth) return;
    w.w = node.offsetWidth; w.h = node.offsetHeight;
    drawWires(); saveGeom(w);
  }).observe(node);

  w.node = node; w.msgsEl = msgs; w.badge = badge; w.titleEl = title;
  canvas().append(node);
  applyGeom(w);
  renderMsgs(w);
}

function applyGeom(w) {
  const s = w.node.style;
  s.left = w.x + "px"; s.top = w.y + "px"; s.width = w.w + "px"; s.height = w.h + "px"; s.zIndex = w.z;
  w.node.hidden = w.min;
}

function front(w) {
  if (w.z === D.z) return;
  w.z = ++D.z; w.node.style.zIndex = w.z; saveGeom(w);
}

async function closeWindow(w) {
  if (w.chatId) {
    const r = await api("/api/desk/" + w.chatId, { method: "PUT", body: JSON.stringify({ open: false }) });
    if (!r.ok) return toast("Could not close that window");
  }
  removeWindow(w);
  renderAll();
}

let saveTimers = new Map();
function saveGeom(w) {
  if (!w.chatId) return;
  clearTimeout(saveTimers.get(w.key));
  saveTimers.set(w.key, setTimeout(() => {
    api("/api/desk/" + w.chatId, { method: "PUT", body: JSON.stringify({ open: true, min: w.min, x: w.x, y: w.y, w: w.w, h: w.h, z: w.z }) });
  }, 400));
}

function startDrag(e, w) {
  if (narrow() || e.target.closest("select,button") || (e.target.matches("input") && !e.target.readOnly)) return;
  const sx = e.clientX, sy = e.clientY, ox = w.x, oy = w.y;
  const head = e.currentTarget;
  head.setPointerCapture(e.pointerId);
  head.classList.add("dragging");
  const move = (ev) => {
    const c = canvas();
    w.x = Math.max(0, Math.min(Math.max(0, c.clientWidth - w.w), ox + ev.clientX - sx));
    w.y = Math.max(0, Math.min(Math.max(0, c.clientHeight - 56), oy + ev.clientY - sy));
    w.node.style.left = w.x + "px"; w.node.style.top = w.y + "px";
    scheduleWires();
  };
  head.addEventListener("pointermove", move);
  head.addEventListener("pointerup", () => { head.removeEventListener("pointermove", move); head.classList.remove("dragging"); saveGeom(w); }, { once: true });
}

async function addExisting() {
  const onDesk = new Set([...D.wins.values()].map((w) => w.chatId).filter(Boolean));
  const items = state.chats.filter((c) => !onDesk.has(c.id)).slice(0, 12).map((c) => ({
    label: c.title, icon: "doc",
    run: async () => {
      const n = D.wins.size;
      const r = await api("/api/desk/" + c.id, { method: "PUT", body: JSON.stringify({ open: true, x: 24 + (n % 4) * 44, y: 16 + (n % 4) * 36, w: 480, h: 540, z: ++D.z }) });
      if (!r.ok) return toast("Could not add that chat");
      await load();
    },
  }));
  if (!items.length) return toast("Every chat you have is already on the Desk");
  showMenu($("deskAdd"), items);
}

// ---------- messages ----------

function msgNode(m) {
  if (m.role === "user") { const row = el("div", "msg user"); row.append(el("div", "bubble", m.content)); return row; }
  const row = el("div", "msg kacy");
  row.append(faceNode());
  const body = el("div", "body");
  if (m.role === "error") {
    const n = el("div", "notice" + (m.info ? " info" : ""));
    n.append(el("b", null, m.title), el("span", null, m.content));
    body.append(n);
  } else {
    const prose = el("div", "prose");
    prose.innerHTML = markdown(m.content);   // markdown() escapes before it adds tags
    body.append(prose);
    for (const a of (m.meta && m.meta.actions) || []) body.append(approvalCard(a));
  }
  row.append(body);
  return row;
}

function renderMsgs(w) {
  const box = w.msgsEl;
  const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  box.textContent = "";
  if (!w.messages.length && !w.busy) {
    box.append(el("p", "dhint", "Ask anything here. Use the link dot to connect this chat to another one."));
  }
  w.messages.forEach((m) => box.append(msgNode(m)));
  if (w.busy) {
    const row = el("div", "msg kacy"); row.append(faceNode());
    const b = el("div", "body working"); const dots = el("span", "dots"); dots.append(el("i"), el("i"), el("i"));
    b.append(dots, el("span", null, "Working on it")); row.append(b); box.append(row);
  }
  if (stick) box.scrollTop = box.scrollHeight;
}

function errorFor(status, d) {
  if (d && d.error === "blocked") return { role: "error", title: "Prompt Shields stopped this message", content: "Rephrase it without asking Kacy to ignore its rules." };
  if (d && d.error === "content_filtered") return { role: "error", title: "Azure's content filter flagged this message", content: "Rephrase it and try again." };
  if (status === 403) return { role: "error", title: "Your role can't do that", content: typeof d === "string" ? d : "Ask a manager or admin if you need access." };
  return { role: "error", title: "Kacy ran into a problem", content: `The server answered ${status}. Send the message again.` };
}

async function sendMsg(w, text) {
  w.messages.push({ role: "user", content: text });
  w.busy = true; renderMsgs(w); renderTray();
  let r;
  try {
    r = await api("/chat", { method: "POST", body: JSON.stringify({ message: text, chat_id: w.chatId, agent: w.chatId ? null : w.agent, on_desk: true }) });
  } catch {
    w.busy = false;
    w.messages.push({ role: "error", title: "Can't reach Kacy", content: "The connection dropped. Send the message again." });
    renderMsgs(w); return;
  }
  w.busy = false;
  if (r.status === 401) { location.reload(); return; }
  if (!r.ok) { w.messages.push(errorFor(r.status, r.data && r.data.detail)); renderMsgs(w); return; }
  const d = r.data;
  const first = !w.chatId;
  if (first) {   // the chat now exists on the server: re-key the window and save where it sits
    D.wins.delete(w.key);
    w.chatId = d.chat_id; w.key = d.chat_id; w.title = d.title;
    D.wins.set(w.key, w);
    w.node.dataset.key = w.key;
    w.titleEl.value = w.title;
    const sel = w.node.querySelector("select.dagent");
    if (sel) sel.replaceWith(el("span", "dagent", (state.agents.find((a) => a.id === w.agent) || { name: "Kacy" }).name));
    saveGeom(w);
  }
  state.pendingActions = new Set(d.pending_actions.map((x) => x.id));
  w.messages.push({ role: "assistant", content: d.reply, meta: { activity: d.activity, actions: d.new_actions } });
  renderMsgs(w); renderTray();
  if (d.activity && d.activity.steps.some((s) => s.id === "linked")) toast("Used the summary of a linked chat");
  refreshChats();
}

// ---------- tray (sticky notes) ----------

const waitingCount = (w) => w.messages.reduce((n, m) => n + (((m.meta && m.meta.actions) || [])
  .filter((a) => (state.actionView[a.id] || a).status === "pending_approval").length), 0);

function renderTray() {
  const tray = $("tray");
  tray.textContent = "";
  for (const w of D.wins.values()) {
    if (!w.min) continue;
    const s = el("div", "sticky"); s.dataset.key = w.key; s.tabIndex = 0; s.setAttribute("role", "button");
    s.setAttribute("aria-label", "Restore " + w.title);
    const last = [...w.messages].reverse().find((m) => m.role === "assistant");
    const snippet = last ? last.content.replace(/[#*_`>-]/g, "").replace(/\s+/g, " ").trim().slice(0, 110) : "No messages yet";
    s.append(el("strong", null, w.title), el("small", null, snippet));
    const wait = waitingCount(w);
    if (wait) s.append(el("span", "stk-wait", wait + " waiting for you"));
    if (D.links.some((l) => l.a === w.key || l.b === w.key)) { const lk = el("span", "stk-link"); lk.append(icon("link")); s.append(lk); }
    const open = () => { w.min = false; front(w); applyGeom(w); saveGeom(w); renderAll(); };
    s.onclick = open;
    s.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } };
    tray.append(s);
  }
}

// ---------- links ----------

function startLink(e, from) {
  if (narrow()) return toast("Linking works on a wider screen");
  e.preventDefault(); e.stopPropagation();
  if (!from.chatId) return toast("Send a message first, so there is something to share.");
  const c = canvas().getBoundingClientRect();
  const p = e.currentTarget.getBoundingClientRect();
  const sx = p.left + p.width / 2 - c.left, sy = p.top + p.height / 2 - c.top;
  const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
  line.setAttribute("class", "wire temp");
  $("wires").append(line);
  const over = (ev) => document.elementFromPoint(ev.clientX, ev.clientY)?.closest(".dwin, .sticky");
  const move = (ev) => {
    const x = ev.clientX - c.left, y = ev.clientY - c.top;
    line.setAttribute("d", `M${sx},${sy} C${sx + 80},${sy} ${x - 80},${y} ${x},${y}`);
    document.querySelectorAll(".drop").forEach((n) => n.classList.remove("drop"));
    const t = over(ev); if (t && t.dataset.key !== from.key) t.classList.add("drop");
  };
  const up = async (ev) => {
    document.removeEventListener("pointermove", move);
    document.querySelectorAll(".drop").forEach((n) => n.classList.remove("drop"));
    line.remove();
    const t = over(ev);
    const to = t && D.wins.get(t.dataset.key);
    if (!to || to === from) return;
    if (!to.chatId) return toast("Send a message in that window first, so it has something to share.");
    const r = await api("/api/links", { method: "POST", body: JSON.stringify({ a: from.chatId, b: to.chatId, two_way: false }) });
    if (!r.ok) return toast(typeof (r.data && r.data.detail) === "string" ? r.data.detail : "Could not link those chats");
    D.links.push(r.data);
    renderAll();
    toast(`${to.title} can now read a summary of ${from.title}`);
  };
  document.addEventListener("pointermove", move);
  document.addEventListener("pointerup", up, { once: true });
  move(e);
}

function closePop() { D.pop?.remove(); D.pop = null; }

function openPop(link, anchor) {
  closePop();
  const a = D.wins.get(link.a), b = D.wins.get(link.b);
  if (!a || !b) return;
  const pop = el("div", "lpop"); D.pop = pop;
  pop.append(el("h3", null, `${a.title}  →  ${b.title}`));

  const seg = el("div", "seg wide");
  const one = el("button", link.two_way ? "" : "on", "One way"); one.title = `${b.title} reads ${a.title}`;
  const two = el("button", link.two_way ? "on" : "", "Two way"); two.title = "Both chats read each other";
  const setMode = async (tw) => {
    const r = await api("/api/links/" + link.id, { method: "PATCH", body: JSON.stringify({ two_way: tw }) });
    if (!r.ok) return toast("Could not change the link");
    link.two_way = tw; drawWires(); openPop(link, anchor);
  };
  one.onclick = () => setMode(false); two.onclick = () => setMode(true);
  seg.append(one, two);
  pop.append(seg, el("p", "lpop-note", link.two_way
    ? "Both chats read a short summary of the other. Nothing else is shared."
    : `${b.title} reads a short summary of ${a.title}. ${a.title} doesn't see ${b.title}.`));

  const out = el("div", "rel");
  const ask = el("button", "primary sm", "How do these relate?");
  ask.onclick = async () => {
    ask.disabled = true; ask.textContent = "Comparing…";
    const r = await api(`/api/links/${link.id}/relationship`, { method: "POST" });
    ask.disabled = false; ask.textContent = "Compare again";
    out.textContent = "";
    if (!r.ok) { out.append(el("p", "err", typeof (r.data && r.data.detail) === "string" ? r.data.detail : "Could not compare these chats.")); return; }
    renderRelation(out, r.data, link);
  };
  const unlink = el("button", "ghost sm", "Unlink");
  unlink.onclick = async () => {
    const r = await api("/api/links/" + link.id, { method: "DELETE" });
    if (!r.ok) return toast("Could not unlink");
    D.links = D.links.filter((l) => l.id !== link.id); closePop(); renderAll();
  };
  const row = el("div", "lpop-actions"); row.append(ask, unlink);
  pop.append(row, out);

  canvas().append(pop);
  const c = canvas().getBoundingClientRect(), r = anchor.getBoundingClientRect();
  pop.style.left = Math.max(8, Math.min(r.left - c.left - 20, c.width - pop.offsetWidth - 8)) + "px";
  pop.style.top = Math.max(8, Math.min(r.bottom - c.top + 8, c.height - pop.offsetHeight - 8)) + "px";
}

function renderRelation(out, data, link) {
  if (data.empty) { out.append(el("p", "lpop-note", "One of these chats has no summary yet. Send a message in it, then compare again.")); return; }
  const section = (label, items, cls) => {
    if (!items.length) return;
    const h = el("h4", cls, label); const ul = el("ul");
    items.forEach((t) => ul.append(el("li", null, t)));
    out.append(h, ul);
  };
  section("In common", data.shared, "ok");
  section("Disagree", data.conflicts, "bad");
  section("Missing", data.gaps, "warn");
  if (!data.shared.length && !data.conflicts.length && !data.gaps.length) out.append(el("p", "lpop-note", "Nothing in common yet."));
  if (data.basic) out.append(el("p", "lpop-note", "Basic match by shared words. The model was not available for a deeper comparison."));
  if (data.next) {
    const b = D.wins.get(link.b);
    const btn = el("button", "ghost sm", "Put the next step in " + (b ? b.title : "the chat"));
    out.append(el("h4", null, "Suggested next step"), el("p", null, data.next), btn);
    btn.onclick = () => { const ta = b && b.node.querySelector("textarea"); if (ta) { ta.value = data.next; ta.dispatchEvent(new Event("input")); ta.focus(); closePop(); } };
  }
}

// ---------- drawing ----------

function renderAll() {
  for (const w of D.wins.values()) applyGeom(w);
  $("deskEmpty").hidden = D.wins.size > 0;
  for (const w of D.wins.values()) {
    const n = D.links.filter((l) => l.a === w.key || l.b === w.key).length;
    w.badge.hidden = !n; w.badge.textContent = n ? `${n} linked` : "";
  }
  renderTray();
  scheduleWires();
}

function scheduleWires() { if (!D.raf) D.raf = requestAnimationFrame(() => { D.raf = 0; drawWires(); }); }

function anchorRect(key) {
  const w = D.wins.get(key);
  if (!w) return null;
  const node = w.min ? $("tray").querySelector(`.sticky[data-key="${CSS.escape(key)}"]`) : w.node;
  if (!node || node.hidden) return null;
  const c = canvas().getBoundingClientRect(), r = node.getBoundingClientRect();
  return { x: r.left - c.left, y: r.top - c.top, w: r.width, h: r.height };
}

function drawWires() {
  const svg = $("wires");
  svg.querySelectorAll(".wire:not(.temp)").forEach((n) => n.remove());
  canvas().querySelectorAll(".wirebtn").forEach((n) => n.remove());
  if (narrow()) return;
  for (const link of D.links) {
    const A = anchorRect(link.a), B = anchorRect(link.b);
    if (!A || !B) continue;
    const ac = { x: A.x + A.w / 2, y: A.y + A.h / 2 }, bc = { x: B.x + B.w / 2, y: B.y + B.h / 2 };
    const horizontal = Math.abs(bc.x - ac.x) > (A.w + B.w) / 2 * 0.7;
    let p1, p2, d1, d2;
    if (horizontal) {
      const right = bc.x > ac.x;
      p1 = { x: right ? A.x + A.w : A.x, y: ac.y }; p2 = { x: right ? B.x : B.x + B.w, y: bc.y };
      d1 = { x: right ? 1 : -1, y: 0 }; d2 = { x: right ? -1 : 1, y: 0 };
    } else {
      const down = bc.y > ac.y;
      p1 = { x: ac.x, y: down ? A.y + A.h : A.y }; p2 = { x: bc.x, y: down ? B.y : B.y + B.h };
      d1 = { x: 0, y: down ? 1 : -1 }; d2 = { x: 0, y: down ? -1 : 1 };
    }
    const k = Math.max(60, Math.hypot(p2.x - p1.x, p2.y - p1.y) / 2.4);
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", `M${p1.x},${p1.y} C${p1.x + d1.x * k},${p1.y + d1.y * k} ${p2.x + d2.x * k},${p2.y + d2.y * k} ${p2.x},${p2.y}`);
    path.setAttribute("class", "wire" + (link.two_way ? " two" : ""));
    path.setAttribute("marker-end", "url(#arrow)");
    if (link.two_way) path.setAttribute("marker-start", "url(#arrow)");
    svg.append(path);
    const mid = path.getPointAtLength(path.getTotalLength() / 2);
    const btn = el("button", "wirebtn" + (link.two_way ? " two" : "")); btn.type = "button";
    btn.setAttribute("aria-label", "Link options"); btn.title = "Link options";
    btn.append(icon("link"));
    btn.style.left = mid.x + "px"; btn.style.top = mid.y + "px";
    btn.onclick = () => openPop(link, btn);
    canvas().append(btn);
  }
}
