/* Trading OS Cockpit SPA — MODULE 45.
 * Zero build step, zero dependencies: servable by the M44 gateway (/ui) or any
 * static host; wrappable in Tauri 2 unchanged. Client GPU is used for canvas
 * rendering only. ZERO order logic lives here (spec §12.11) — this file renders
 * state and sends authenticated control INTENTS to the gateway.
 *
 * ?demo=1 runs against built-in mock data (no gateway needed) — used for design
 * review and the published preview. Everything else identical.
 */
"use strict";

const DEMO = new URLSearchParams(location.search).has("demo");
const POLL_MS = 3000;
const KILL_PHRASE = "KILL ALL POSITIONS";

const $ = (id) => document.getElementById(id);

/* localStorage throws in sandboxed iframes and some private-browsing modes —
 * a thrown SecurityError at load killed the entire cockpit. Degrade to
 * in-memory storage instead of dying. */
const storage = (() => {
  try { localStorage.setItem("__t", "1"); localStorage.removeItem("__t"); return localStorage; }
  catch (_e) { const m = {}; return { getItem: (k) => m[k] ?? null, setItem: (k, v) => { m[k] = v; } }; }
})();

const state = { token: storage.getItem("cockpit_token") || "", role: null,
                equityHistory: [], lastState: null, ackedEvents: new Set() };

/* ---------------- gateway client (intents only) ---------------- */

async function api(path, opts = {}) {
  if (DEMO) return demoApi(path, opts);
  const resp = await fetch(path, {
    ...opts,
    headers: { "Authorization": `Bearer ${state.token}`,
               "Content-Type": "application/json", ...(opts.headers || {}) },
  });
  if (resp.status === 401) throw new Error("auth");
  if (resp.status === 403) throw new Error("forbidden");
  if (!resp.ok) throw new Error(`http_${resp.status}`);
  return resp.json();
}

/* ---------------- demo fixtures (design review without a gateway) ------- */

const demo = {
  halted: false,
  equity: 1002340.55, pnl: 2340.55, costs: 512.30, var95: 0.011,
  positions: [
    { symbol: "RELIANCE", leg: "india", qty: 15, entry: 2503.1, stop: 2531.0, r_now: 1.8, state: "TRAILING", mfe_r: 2.2 },
    { symbol: "BTCUSD", leg: "mt5_crypto", qty: 0.12, entry: 60110, stop: 59200, r_now: 0.4, state: "RISK_ON", mfe_r: 0.6 },
    { symbol: "EURUSD", leg: "mt5_forex", qty: 0.5, entry: 1.0852, stop: 1.0852, r_now: 1.1, state: "BREAKEVEN", mfe_r: 1.3 },
  ],
  workers: { var_worker: true, exit_manager: true, anomaly_guard: true, news_poll: true, reconciler: false },
  approvals: [
    { id: "rule-7", label: "Rule: skip entries when GEX regime = amplify + severity ≥ 7 (holdout p=0.04)" },
    { id: "model-v3", label: "Model promotion v3: Brier 0.184→0.171, after-cost +6.2% on holdout" },
  ],
  pnl_history: [
    { date: "2026-06-30", equity: 1000000 }, { date: "2026-07-15", equity: 1004200 },
    { date: "2026-07-31", equity: 1002340 }, { date: "2026-08-04", equity: 1006100 },
  ],
  config_view: { risk_limits: { max_risk_per_trade_pct: 0.01, max_position_pct: 0.05 },
                 exit_manager: { breakeven_at_r: 1.0, never_widen_stop: true } },
  trades: [
    { symbol: "RELIANCE", direction: "buy", realized_r: 1.8, reason: "trail_stop", mfe_captured_pct: 78.3, sleeve: "tsmom_f" },
    { symbol: "BTCUSD", direction: "sell", realized_r: -0.9, reason: "stop_hit", mfe_captured_pct: 0.0, sleeve: "tsmom_f" },
    { symbol: "EURUSD", direction: "buy", realized_r: 0.4, reason: "time_stop_no_progress", mfe_captured_pct: 31.0, sleeve: "accurate" },
  ],
  events: [
    { t: "10:42:11", m: "anomaly_guard: velocity_5s trigger NIFTY — entries paused 15m" },
    { t: "10:41:58", m: "regime NIFTY → SHOCK (vol pctl 0.97)" },
    { t: "09:15:02", m: "session open · workers healthy · VaR95 0.8%" },
  ],
};

function demoApi(path, opts) {
  state.role = "operator";
  if (path === "/state") {
    demo.equity += (Math.random() - 0.45) * 400;
    demo.pnl = demo.equity - 1000000;
    return Promise.resolve(JSON.parse(JSON.stringify(demo)));
  }
  if (path === "/approvals") return Promise.resolve(demo.approvals);
  if (path === "/trades") return Promise.resolve(demo.trades);
  if (path === "/pnl_history") return Promise.resolve(demo.pnl_history);
  if (path === "/config") return Promise.resolve(demo.config_view);
  if (path === "/control/kill") { demo.halted = true; return Promise.resolve({ ok: true }); }
  if (path === "/control/unlock") { demo.halted = false; return Promise.resolve({ halted: false }); }
  if (path.startsWith("/control/approve/")) {
    demo.approvals = demo.approvals.filter(a => `/control/approve/${a.id}` !== path);
    return Promise.resolve({ ok: true });
  }
  return Promise.resolve({});
}

/* ---------------- rendering ---------------- */

/* Escape EVERYTHING interpolated into innerHTML. Event text and approval
 * labels can carry news-derived strings — treat all of it as hostile. */
function esc(x) {
  return String(x ?? "").replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function fmtMoney(x) {
  return (x < 0 ? "-" : "") + "₹" + Math.abs(x).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function render(s) {
  state.lastState = s;
  $("halt-banner").classList.toggle("hidden", !s.halted);
  $("unlock-panel").classList.toggle("hidden", !s.halted || state.role !== "operator");
  $("kill-btn").disabled = state.role !== "operator" || s.halted;

  $("equity").textContent = fmtMoney(s.equity ?? 0);
  const pnl = s.pnl ?? 0;
  $("pnl").textContent = (pnl >= 0 ? "+" : "") + fmtMoney(pnl).replace("₹-", "-₹");
  $("pnl").className = "big " + (pnl >= 0 ? "pos" : "neg");
  $("costs").textContent = `costs ${fmtMoney(s.costs ?? 0)}`;

  const varLimit = 0.02, v = s.var95 ?? 0;
  const pct = Math.min(100, (v / varLimit) * 100);
  const fill = $("var-fill");
  fill.style.width = pct + "%";
  fill.style.background = pct < 60 ? "var(--green)" : pct < 90 ? "var(--amber)" : "var(--red)";
  $("var-label").textContent = `${(v * 100).toFixed(2)}% of ${(varLimit * 100).toFixed(1)}% limit`;

  $("workers").innerHTML = Object.entries(s.workers || {})
    .map(([name, ok]) => `<span class="chip ${ok ? "" : "dead"}">${esc(name)}${ok ? "" : " ✗"}</span>`)
    .join("") || "—";

  const tbody = $("positions").querySelector("tbody");
  tbody.innerHTML = (s.positions || []).map(p => `
    <tr><td>${esc(p.symbol)}</td><td>${esc(p.leg)}</td><td>${esc(p.qty)}</td>
    <td>${esc(p.entry)}</td><td>${esc(p.stop)}</td>
    <td class="${p.r_now >= 0 ? "pos" : "neg"}">${(p.r_now ?? 0).toFixed(1)}R</td>
    <td><span class="state ${esc(p.state)}">${esc(p.state)}</span></td>
    <td>${(p.mfe_r ?? 0).toFixed(1)}R</td></tr>`).join("")
    || `<tr><td colspan="8" class="sub">no open positions</td></tr>`;

  $("events").innerHTML = (s.events || [])
    .filter(e => !state.ackedEvents.has(`${e.t}|${e.m}`))
    .map(e => `<div class="row"><span class="t">${esc(e.t)}</span><span>${esc(e.m)}</span>
      <button class="ghost small ack" data-k="${esc(`${e.t}|${e.m}`)}">✓</button></div>`).join("")
    || `<div class="sub">no unacknowledged events</div>`;
  document.querySelectorAll(".ack").forEach(btn =>
    btn.addEventListener("click", () => { state.ackedEvents.add(btn.dataset.k); render(state.lastState); }));

  const canResume = state.role === "operator" && !s.halted;
  $("resume-btn").classList.toggle("hidden", !canResume);
  if (!canResume) $("resume-confirm").classList.add("hidden");
  $("pause-btn").classList.toggle("hidden", !canResume);
  if (!canResume) $("pause-confirm").classList.add("hidden");

  state.equityHistory.push(s.equity ?? 0);
  if (state.equityHistory.length > 80) state.equityHistory.shift();
  drawSpark();
}

function drawSpark() {
  const c = $("spark"), ctx = c.getContext("2d");
  const xs = state.equityHistory;
  ctx.clearRect(0, 0, c.width, c.height);
  if (xs.length < 2) return;
  const min = Math.min(...xs), max = Math.max(...xs), span = (max - min) || 1;
  ctx.beginPath();
  xs.forEach((v, i) => {
    const x = (i / (xs.length - 1)) * c.width;
    const y = c.height - 4 - ((v - min) / span) * (c.height - 8);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.strokeStyle = xs[xs.length - 1] >= xs[0] ? "#2ecc71" : "#e74c3c";
  ctx.lineWidth = 1.6;
  ctx.stroke();
}

async function renderBlotter() {
  const trades = await api("/trades").catch(() => []);
  const tbody = $("blotter").querySelector("tbody");
  tbody.innerHTML = (trades || []).slice(-25).reverse().map(t => `
    <tr><td>${esc(t.symbol)}</td><td>${esc(t.direction || "")}</td>
    <td class="${(t.realized_r ?? 0) >= 0 ? "pos" : "neg"}">${(t.realized_r ?? 0).toFixed(2)}R</td>
    <td>${esc(t.reason)}</td><td>${esc(t.mfe_captured_pct ?? "")}</td></tr>`).join("")
    || `<tr><td colspan="5" class="sub">no closed trades yet</td></tr>`;
}

async function renderPnlPanels() {
  // monthly P&L rollup from daily equity closes
  const hist = await api("/pnl_history").catch(() => []);
  const byMonth = new Map();
  for (const p of hist || []) {
    const m = p.date.slice(0, 7);
    if (!byMonth.has(m)) byMonth.set(m, { first: p.equity, last: p.equity });
    byMonth.get(m).last = p.equity;
  }
  let prevEnd = null;
  const rows = [...byMonth.entries()].map(([m, v]) => {
    const start = prevEnd ?? v.first;
    const pnl = v.last - start, ret = start ? (pnl / start) * 100 : 0;
    prevEnd = v.last;
    return `<tr><td>${esc(m)}</td>
      <td class="${pnl >= 0 ? "pos" : "neg"}">${pnl >= 0 ? "+" : ""}${fmtMoney(pnl).replace("₹-", "-₹")}</td>
      <td class="${pnl >= 0 ? "pos" : "neg"}">${ret.toFixed(2)}%</td>
      <td>${fmtMoney(v.last)}</td></tr>`;
  });
  $("pnl-monthly").querySelector("tbody").innerHTML =
    rows.join("") || `<tr><td colspan="4" class="sub">no history yet</td></tr>`;

  // sleeve attribution from the blotter
  const trades = await api("/trades").catch(() => []);
  const agg = new Map();
  for (const t of trades || []) {
    const k = t.sleeve || "unattributed";
    const a = agg.get(k) || { r: 0, n: 0, w: 0 };
    a.r += t.realized_r ?? 0; a.n += 1; if ((t.realized_r ?? 0) > 0) a.w += 1;
    agg.set(k, a);
  }
  $("sleeves").innerHTML = [...agg.entries()].map(([k, a]) =>
    `<div class="row"><span>${esc(k)}</span>
     <span class="${a.r >= 0 ? "pos" : "neg"}">${a.r >= 0 ? "+" : ""}${a.r.toFixed(2)}R</span>
     <span class="sub">${a.n} trades · ${a.n ? Math.round((a.w / a.n) * 100) : 0}% win</span></div>`).join("")
    || "—";
}

async function renderConfig() {
  const cfg = await api("/config").catch(() => null);
  // textContent (not innerHTML): config is data, never markup
  $("config-view").textContent = cfg ? JSON.stringify(cfg, null, 2) : "unavailable";
}

async function renderApprovals() {
  const items = await api("/approvals").catch(() => []);
  $("approvals").innerHTML = (items || []).map(a => `
    <div class="row"><span>${esc(a.label || a.id)}</span>
    ${state.role === "operator"
      ? `<button class="ghost small approve" data-id="${esc(a.id)}">APPROVE</button>` : ""}
    </div>`).join("") || `<div class="sub">nothing pending — the gates are quiet</div>`;
  document.querySelectorAll(".approve").forEach(btn =>
    btn.addEventListener("click", () => api(`/control/approve/${btn.dataset.id}`,
      { method: "POST", body: "{}" }).then(renderApprovals)));
}

/* ---------------- poll loop ---------------- */

async function tick() {
  try {
    const s = await api("/state");
    if (!DEMO && state.role === null) state.role = "viewer"; // real role arrives via 403 probes
    $("conn").textContent = DEMO ? "demo mode — mock data" : "live";
    $("conn").className = "conn ok";
    $("role").textContent = state.role || "viewer";
    render(s);
  } catch (err) {
    $("conn").textContent = err.message === "auth" ? "invalid token" : "gateway unreachable";
    $("conn").className = "conn err";
  }
}

/* ---------------- controls (intents, never logic) ---------------- */

async function probeRole() {
  // side-effect-free role probe (GET /whoami). NEVER probe via a control
  // endpoint — a POST /control/* is a REAL state change on a live system.
  if (DEMO) { state.role = "operator"; return; }
  try { state.role = (await api("/whoami")).role; }
  catch (err) { state.role = null; }
}

function wire() {
  $("token").value = state.token;
  $("token").addEventListener("change", async (e) => {
    state.token = e.target.value.trim();
    storage.setItem("cockpit_token", state.token);
    await probeRole();
    tick(); renderApprovals();
  });

  $("kill-btn").addEventListener("click", () => $("kill-confirm").classList.remove("hidden"));
  $("kill-cancel").addEventListener("click", () => $("kill-confirm").classList.add("hidden"));
  $("kill-go").addEventListener("click", async () => {
    if ($("kill-phrase").value !== KILL_PHRASE) { $("kill-phrase").value = ""; return; }
    await api("/control/kill", { method: "POST",
      body: JSON.stringify({ confirm: KILL_PHRASE, reason: $("kill-reason").value }) });
    $("kill-confirm").classList.add("hidden");
    tick();
  });
  $("unlock-go").addEventListener("click", async () => {
    await api("/control/unlock", { method: "POST",
      body: JSON.stringify({ confirm: $("unlock-phrase").value }) }).catch(() => {});
    $("unlock-phrase").value = "";
    tick();
  });

  // SAFE-START release (OPERATOR.md step 7): deliberate two-click resume.
  $("resume-btn").addEventListener("click", () => $("resume-confirm").classList.remove("hidden"));
  $("resume-cancel").addEventListener("click", () => $("resume-confirm").classList.add("hidden"));
  $("resume-go").addEventListener("click", async () => {
    await api("/control/resume_entries", { method: "POST",
      body: JSON.stringify({ reason: "cockpit resume" }) }).catch(() => {});
    $("resume-confirm").classList.add("hidden");
    tick();
  });

  // PAUSE ENTRIES: the deliberate button the old role-probe never was.
  $("pause-btn").addEventListener("click", () => $("pause-confirm").classList.remove("hidden"));
  $("pause-cancel").addEventListener("click", () => $("pause-confirm").classList.add("hidden"));
  $("pause-go").addEventListener("click", async () => {
    await api("/control/pause_entries", { method: "POST",
      body: JSON.stringify({ reason: $("pause-reason").value || "cockpit manual pause" }) }).catch(() => {});
    $("pause-reason").value = "";
    $("pause-confirm").classList.add("hidden");
    tick();
  });
}

async function boot() {
  wire();
  await probeRole();       // role known BEFORE first render (stored token case)
  tick();
  renderApprovals();
  renderBlotter();
  renderPnlPanels();
  renderConfig();
  setInterval(tick, POLL_MS);
  setInterval(renderBlotter, POLL_MS * 4);
  setInterval(renderPnlPanels, POLL_MS * 10);
}

boot();
