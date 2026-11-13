const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (n) => Number(n ?? 0).toLocaleString("en-US");
const short = (n) => Math.abs(n) >= 1e6 ? (n / 1e6).toFixed(2) + "M" : Math.abs(n) >= 1e4 ? (n / 1e3).toFixed(1) + "k" : num(n);
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu"];  // the Gulf working week

let S = null, H = { adaptive: [], baseline: [] }, feed = [], meta = null;
let filter = "all", tab = "leads", freshSeq = 0, replayTimer = null;

const KPIS = [
  ["cash", "Cash", true], ["revenue", "Revenue", true], ["mrr", "Monthly recurring", true],
  ["backlog_hours", "Backlog hours", false], ["churned", "Customers lost", false], ["on_time_pct", "On-time delivery %", true],
];
const CHARTS = [
  ["cash", "Cash on hand"], ["profit", "Profit to date"], ["mrr", "Monthly recurring revenue"],
  ["backlog_hours", "Delivery backlog, hours"], ["churned", "Customers lost"], ["customers", "Active customers"],
];
const ROLE = { sales: "Qualifies, prices, closes", ops: "Delivers the work", finance: "Invoices, collects, approves spend", support: "Tickets and retention" };
const COLS = {
  leads: [["id", "ID"], ["company", "Company"], ["city", "City"], ["need", "Need"], ["value", "AED / month", 1], ["score", "Score", 1], ["discount", "Disc. %", 1], ["status", "Status"]],
  customers: [["id", "ID"], ["name", "Customer"], ["mrr", "AED / month", 1], ["health", "Health", 1], ["status", "Status"]],
  work: [["id", "ID"], ["name", "Customer"], ["kind", "Job"], ["done", "Done h", 1], ["hours", "Total h", 1], ["due_day", "Due day", 1], ["status", "Status"]],
  invoices: [["id", "ID"], ["name", "Customer"], ["amount", "AED", 1], ["due_day", "Due day", 1], ["reminders", "Reminders", 1], ["status", "Status"]],
  tickets: [["id", "ID"], ["name", "Customer"], ["text", "Message"], ["severity", "Severity"], ["status", "Status"]],
};

async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  if (!r.ok) { toast((await r.json().catch(() => ({}))).detail || "That did not work, try again."); throw new Error(path); }
  return r.json();
}
function toast(text) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = text; document.body.append(t);
  setTimeout(() => t.remove(), 3200);
}

async function load() {
  const j = await api("/api/state");
  S = j.state; H = j.history; feed = j.feed.reverse();
  render();
}

function connect() {
  const es = new EventSource("/api/stream");
  es.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.kind === "reload") return load();
    S = m.state;
    for (const w of ["adaptive", "baseline"]) {
      const p = m.points[w], last = H[w][H[w].length - 1];
      if (p && (!last || last.t < p.t)) H[w].push(p);
    }
    if (m.events.length) {
      freshSeq = m.events[0].seq;
      feed = m.events.reverse().concat(feed).slice(0, 220);
    }
    render();
  };
}

// Never swap the DOM under a finger: a button replaced mid-press would swallow the click.
let holding = false;
document.addEventListener("pointerdown", () => { holding = true; });
document.addEventListener("pointerup", () => { holding = false; setTimeout(render, 0); });
document.addEventListener("pointercancel", () => { holding = false; });

function render() {
  if (!S || holding) return;
  const c = S.clock, started = c.t >= 0;
  $("clock").textContent = started ? `Week ${c.week} · Day ${c.day} · ${DAYS[(c.day - 1) % 5]} ${c.time}` : "Not started";
  $("play").textContent = S.finished ? "Finished" : S.running ? "Pause" : "Play";
  $("play").disabled = S.finished;
  document.querySelectorAll(".seg button").forEach((b) => b.classList.toggle("on", Number(b.dataset.speed) === S.speed));
  const llm = S.llm.enabled ? `${S.llm.calls} LLM decisions via ${S.llm.provider}` : "LLM off, rules only";
  $("status").textContent = `${S.running ? "Live" : "Paused"} · ${num(S.events_logged)} events logged · ${llm}`;
  $("foot-meta").textContent = `Market seed ${S.seed} · runs for ${meta ? meta.max_day : 40} working days`;
  renderKpis(); renderInbox(); renderAgents(); renderFeed(); renderCharts(); renderRecords(); renderReplayDays();
}

function renderKpis() {
  const a = S.kpi.adaptive, b = S.kpi.baseline;
  $("kpis").innerHTML = KPIS.map(([k, label, higherIsBetter]) => {
    const diff = a[k] - b[k], better = higherIsBetter ? diff > 0 : diff < 0;
    const delta = diff === 0 ? "" : `<span class="d ${better ? "up" : "dn"}">${diff > 0 ? "+" : "−"}${short(Math.abs(diff))}</span>`;
    return `<div class="kpi"><div class="k">${label}</div><div class="v">${short(a[k])}</div><div class="b">baseline ${short(b[k])}${delta}</div></div>`;
  }).join("");
}

function renderInbox() {
  $("inbox-list").innerHTML = S.inbox.length ? S.inbox.map((e) => `
    <div class="card esc">
      <div class="from"><span>FROM ${esc(e.agent.toUpperCase())}</span><span>default in ${Math.max(0, e.expires_t - S.clock.t)}h: ${esc(e.options.find((o) => o.key === e.default).label)}</span></div>
      <b>${esc(e.title)}</b><p>${esc(e.detail)}</p>
      <div class="acts">${e.options.map((o, i) => `<button class="btn small ${i ? "" : "primary"}" data-esc="${esc(e.id)}" data-choice="${esc(o.key)}">${esc(o.label)}</button>`).join("")}</div>
    </div>`).join("") : `<div class="empty">Nothing needs you right now. The agents are inside their authority.</div>`;
  $("decided").innerHTML = S.decided.map((e) => `<div>${e.by === "human" ? "You chose" : "Timed out, default"} <b>${esc(e.choice.replace(/_/g, " "))}</b>: ${esc(e.title)}</div>`).join("");
}

function renderAgents() {
  const co = S.company;
  const chips = {
    sales: [[`intake ${co.intake}`, co.intake !== "open" && "amber"], [`terms ${co.terms}`, co.terms !== "normal" && "amber"]],
    ops: [[`capacity ${co.capacity_level}`, co.capacity_level === "red" && "red"], co.contractor_until >= S.clock.day && S.clock.day > 0 ? [`contractor to day ${co.contractor_until}`, "amber"] : null, co.budget_pending ? ["budget pending", "amber"] : null],
    finance: [[`cash ${co.cash_level}`, co.cash_level === "red" && "red"]],
    support: [],
  };
  const open = new Set([...document.querySelectorAll(".agent details[open]")].map((d) => d.dataset.a));
  $("agent-list").innerHTML = Object.entries(S.agents).map(([name, a]) => {
    const st = a.down ? ["down", "Down. Watchdog told the others."] : a.killed ? ["silent", "Silent. Not yet detected."] : ["", `Running · ${num(a.actions)} actions`];
    return `<div class="card agent">
      <div class="top"><span class="name"><span class="dot ${st[0]} ${!st[0] && S.running ? "live" : ""}"></span>${name[0].toUpperCase() + name.slice(1)}</span>
        <button class="btn small ${a.killed ? "" : "danger"}" data-agent="${name}" data-act="${a.killed ? "restore" : "kill"}">${a.killed ? "Restart" : "Switch off"}</button></div>
      <div class="state">${ROLE[name]}. ${st[1]}${a.mailbox ? ` ${a.mailbox} in mailbox.` : ""}</div>
      <div class="chips">${chips[name].filter(Boolean).map(([t, cls]) => `<span class="chip ${cls || ""}">${esc(t)}</span>`).join("")}</div>
      ${a.note ? `<div class="note">${esc(a.note)}</div>` : ""}
      <details data-a="${name}" ${open.has(name) ? "open" : ""}><summary>Charter</summary><pre>${esc(meta ? meta.roles[name] : "")}</pre></details>
    </div>`;
  }).join("");
}

const feedRow = (e, isNew) => `<li class="${e.tone} ${isNew ? "new" : ""}"><time>D${e.day} ${e.time}</time><span class="who">${esc(e.actor)}</span><span class="txt">${esc(e.text)}${e.brain === "llm" ? '<span class="tag">LLM</span>' : ""}</span></li>`;

function renderFeed() {
  const rows = feed.filter((e) => filter === "all" || e.tone === filter).slice(0, 140);
  $("feed").innerHTML = rows.length ? rows.map((e) => feedRow(e, e.seq >= freshSeq && freshSeq)).join("") : `<li><span></span><span></span><span class="txt" style="color:var(--muted)">Press Play to open the company.</span></li>`;
}

function chart(key, title) {
  const W = 340, Hh = 130, L = 6, R = 44, T = 8, B = 18;
  const a = H.adaptive, b = H.baseline, all = a.concat(b).map((p) => p[key]);
  if (!all.length) return `<div class="card chart"><h4>${title}</h4><div class="now">No data yet</div><svg viewBox="0 0 ${W} ${Hh}"></svg></div>`;
  let lo = Math.min(...all), hi = Math.max(...all);
  if (lo === hi) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const maxT = Math.max(a[a.length - 1].t, 39);
  const x = (t) => L + (t / maxT) * (W - L - R), y = (v) => T + (1 - (v - lo) / (hi - lo)) * (Hh - T - B);
  const path = (s) => s.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)} ${y(p[key]).toFixed(1)}`).join("");
  const la = a[a.length - 1], lb = b[b.length - 1];
  let ya = y(la[key]), yb = y(lb[key]);
  if (Math.abs(ya - yb) < 11) { const mid = (ya + yb) / 2, up = ya <= yb ? -1 : 1; ya = mid + up * 5.5; yb = mid - up * 5.5; }
  let days = "";
  for (let d = 5; d * 8 <= maxT + 1; d += 5) days += `<line x1="${x(d * 8 - 1)}" x2="${x(d * 8 - 1)}" y1="${T}" y2="${Hh - B}" stroke="#eceae6"/><text x="${x(d * 8 - 1)}" y="${Hh - 4}" text-anchor="middle">wk ${d / 5}</text>`;
  return `<div class="card chart"><h4>${title}</h4>
    <div class="now">${short(la[key])} adaptive · ${short(lb[key])} baseline</div>
    <svg viewBox="0 0 ${W} ${Hh}" role="img" aria-label="${title}: adaptive ${la[key]}, baseline ${lb[key]}">
      ${days}<line x1="${L}" x2="${W - R}" y1="${Hh - B}" y2="${Hh - B}" stroke="#e2dfd9"/>
      <path d="${path(b)}" fill="none" stroke="#a8a49c" stroke-width="1.6" stroke-dasharray="4 3"/>
      <path d="${path(a)}" fill="none" stroke="#1a1815" stroke-width="2" stroke-linejoin="round"/>
      <circle cx="${x(la.t)}" cy="${y(la[key])}" r="3" fill="#1a1815"/>
      <text x="${x(la.t) + 7}" y="${ya + 3}" style="fill:#1a1815">${short(la[key])}</text>
      <text x="${x(lb.t) + 7}" y="${yb + 3}">${short(lb[key])}</text>
    </svg></div>`;
}
function renderCharts() { $("charts").innerHTML = CHARTS.map(([k, t]) => chart(k, t)).join(""); }

function renderRecords() {
  $("tabs").innerHTML = Object.keys(COLS).map((k) => `<button data-tab="${k}" class="${k === tab ? "on" : ""}">${k[0].toUpperCase() + k.slice(1)} ${S.counts[k]}</button>`).join("");
  const cols = COLS[tab], rows = S.records[tab];
  $("table").innerHTML = `<table><thead><tr>${cols.map((c) => `<th>${c[1]}</th>`).join("")}</tr></thead><tbody>${
    rows.length ? rows.map((r) => `<tr>${cols.map(([k, , n]) => k === "status" ? `<td><span class="st ${esc(r[k])}">${esc(String(r[k]).replace(/_/g, " "))}</span></td>` : `<td class="${n ? "num" : ""} ${k === "text" ? "wide" : ""}">${r[k] == null ? "–" : n ? num(r[k]) : esc(r[k])}</td>`).join("")}</tr>`).join("")
      : `<tr><td colspan="${cols.length}" style="color:var(--muted)">No records yet.</td></tr>`}</tbody></table>`;
}

function renderReplayDays() {
  const sel = $("replay-day"), want = S.days_closed.join(",");
  if (sel.dataset.days === want) return;
  const keep = sel.value;
  sel.dataset.days = want;
  sel.innerHTML = S.days_closed.length ? S.days_closed.map((d) => `<option value="${d}">Day ${d}</option>`).join("") : `<option value="">No closed day yet</option>`;
  if (keep && S.days_closed.includes(Number(keep))) sel.value = keep; else if (S.days_closed.length) sel.value = S.days_closed[S.days_closed.length - 1];
  $("replay-go").disabled = !S.days_closed.length;
}

async function replay() {
  const day = $("replay-day").value;
  if (!day) return;
  clearInterval(replayTimer);
  const r = await api(`/api/replay/${day}`);
  const k = r.kpi.adaptive, kb = r.kpi.baseline;
  $("replay-out").innerHTML = `
    <div class="verify ${r.verified ? "" : "fail"}"><span class="mark">${r.verified ? "✓" : "!"}</span>
      <div><b>${r.verified ? "Replay verified" : "Replay does not match"}.</b> Rebuilt day ${r.day} by folding ${num(r.events_folded)} events from the SQLite log.
      Rebuilt hash <code>${esc(r.rebuilt_hash)}</code> ${r.verified ? "equals" : "differs from"} the hash recorded live <code>${esc(r.recorded_hash)}</code>.</div></div>
    <div class="table-wrap"><table><thead><tr><th>At close of day ${r.day}</th><th>Cash</th><th>Revenue</th><th>MRR</th><th>Backlog h</th><th>Lost</th><th>Open escalations</th></tr></thead><tbody>
      <tr><td>Adaptive</td><td class="num">${num(k.cash)}</td><td class="num">${num(k.revenue)}</td><td class="num">${num(k.mrr)}</td><td class="num">${k.backlog_hours}</td><td class="num">${k.churned}</td><td class="num">${k.open_escalations}</td></tr>
      <tr><td>Baseline</td><td class="num">${num(kb.cash)}</td><td class="num">${num(kb.revenue)}</td><td class="num">${num(kb.mrr)}</td><td class="num">${kb.backlog_hours}</td><td class="num">${kb.churned}</td><td class="num">–</td></tr></tbody></table></div>
    <div class="scrub"><button class="btn small" id="scrub-play">Pause</button><input type="range" id="scrub" min="0" max="${r.events.length}" value="0" aria-label="Position in the day"><span class="status" id="scrub-n"></span></div>
    <ol class="feed" id="replay-feed"></ol>`;
  const slider = $("scrub"), show = (n) => {
    slider.value = n;
    $("scrub-n").textContent = `${n} / ${r.events.length} events`;
    $("replay-feed").innerHTML = r.events.slice(0, n).reverse().map((e) => feedRow(e, false)).join("");
  };
  const start = () => { replayTimer = setInterval(() => { const n = Number(slider.value) + 1; if (n > r.events.length) return stop(); show(n); }, 140); $("scrub-play").textContent = "Pause"; };
  const stop = () => { clearInterval(replayTimer); replayTimer = null; $("scrub-play").textContent = "Play"; };
  slider.oninput = () => { stop(); show(Number(slider.value)); };
  $("scrub-play").onclick = () => { if (replayTimer) return stop(); if (Number(slider.value) >= r.events.length) show(0); start(); };
  show(0); start();
}

document.addEventListener("click", async (e) => {
  const t = e.target.closest("button");
  if (!t) return;
  if (t.dataset.esc) { t.disabled = true; await api(`/api/inbox/${t.dataset.esc}`, { choice: t.dataset.choice }); }
  else if (t.dataset.agent) await api(`/api/agents/${t.dataset.agent}/${t.dataset.act}`, {});
  else if (t.dataset.speed) await api("/api/control", { action: "speed", speed: Number(t.dataset.speed) });
  else if (t.dataset.f) { filter = t.dataset.f; document.querySelectorAll("#filters button").forEach((b) => b.classList.toggle("on", b === t)); renderFeed(); }
  else if (t.dataset.tab) { tab = t.dataset.tab; renderRecords(); }
  else if (t.id === "play") await api("/api/control", { action: S.running ? "pause" : "play" });
  else if (t.id === "next-day" || t.id === "run-week") {
    t.disabled = true;
    try { await api("/api/control", { action: t.id === "next-day" ? "next_day" : "run_week" }); } finally { t.disabled = false; }
  }
  else if (t.id === "reset") { if (confirm("Reset the shared company for everyone watching?")) { await api("/api/control", { action: "reset" }); $("replay-out").innerHTML = ""; } }
  else if (t.id === "replay-go") await replay();
});

(async () => {
  meta = await api("/api/meta");
  $("rules").innerHTML = `<thead><tr><th>Agent</th><th>Goes to a human when</th><th>If nobody answers in ${meta.ttl_hours}h</th></tr></thead><tbody>${meta.rules.map((r) => `<tr><td>${esc(r[0])}</td><td class="wide">${esc(r[1])}</td><td>${esc(r[2])}</td></tr>`).join("")}</tbody>`;
  await load();
  connect();
})();
