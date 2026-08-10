/* Trading OS Cockpit SPA v2 — MODULE 61 (Aug 2026).
 * Zero build step, zero dependencies: servable by the M44 gateway (/ui) or any
 * static host; wrappable in Tauri 2 unchanged. Client GPU is used for canvas
 * rendering only. ZERO order logic lives here (spec §12.11) — this file renders
 * state and sends authenticated control INTENTS to the gateway.
 *
 * v2 (operator video review, 2026-08-10): CRM-style multi-page shell
 * (dashboard / portfolio / pnl / history / markets / ops / settings) over a
 * hash router; market-session awareness end to end (MODULE 58 /clock) — india
 * charts FREEZE and badge CLOSED outside NSE hours instead of ticking fake
 * candles at 21:00 IST; broker + MT5 settings page (MODULE 59/60, env-var
 * booleans only — a credential value never reaches this file).
 *
 * ?demo=1 runs against built-in mock data (no gateway needed) — used for design
 * review and the published preview. The demo feed obeys the SAME session rules.
 */
"use strict";

const DEMO = new URLSearchParams(location.search).has("demo");
const POLL_MS = 3000;
const KILL_PHRASE = "KILL ALL POSITIONS";
/* Destructive confirms ARM after a short delay instead of demanding typed
 * phrases (operator feedback, 2026-08-10): under real stress, typing an
 * exact 18-char phrase is slower and MORE error-prone than two deliberate
 * clicks. The arm delay defeats double-click accidents; the spec's friction
 * budget stays on the risk-INCREASING side (unlock phrase, resume, go-live),
 * not on the airbag. The gateway API still requires its confirm phrase —
 * the UI supplies it after explicit human confirmation. */
const ARM_MS = 700;

function armButton(btn) {
  btn.disabled = true;
  setTimeout(() => { btn.disabled = false; }, ARM_MS);
}

const $ = (id) => document.getElementById(id);

/* localStorage throws in sandboxed iframes and some private-browsing modes —
 * a thrown SecurityError at load killed the entire cockpit. Degrade to
 * in-memory storage instead of dying. */
const storage = (() => {
  try { localStorage.setItem("__t", "1"); localStorage.removeItem("__t"); return localStorage; }
  catch (_e) { const m = {}; return { getItem: (k) => m[k] ?? null, setItem: (k, v) => { m[k] = v; } }; }
})();

const state = { token: storage.getItem("cockpit_token") || "", role: null,
                equityHistory: [], lastState: null, lastClock: null,
                ackedEvents: new Set(), histRows: [] };

/* ---------------- hash router (CRM shell) ----------------
 * The ui_flow_test DOM shim has no location.hash and no addEventListener —
 * both must degrade: undefined hash -> dashboard; no listener -> static page. */
const PAGES = ["dashboard", "portfolio", "pnl", "history", "markets",
               "research", "ops", "settings"];

function currentPage() {
  const h = (typeof location !== "undefined" && typeof location.hash === "string")
    ? location.hash : "";
  const p = h.replace(/^#\/?/, "");
  return PAGES.includes(p) ? p : "dashboard";
}

function navRender() {
  const cur = currentPage();
  for (const p of PAGES) {
    $(`page-${p}`).classList.toggle("hidden", p !== cur);
    $(`nav-${p}`).classList.toggle("active", p === cur);
  }
}
if (typeof addEventListener === "function") addEventListener("hashchange", navRender);

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

/* ---------------- market sessions (client mirror of MODULE 58) ----------
 * The gateway /clock is the authority; this mirror only drives the DEMO feed
 * and instant chip rendering between polls. Same rules, same holiday list. */

const NSE_HOLIDAYS_2026 = [
  "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31",
  "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26",
  "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24",
  "2026-12-25",
];

function istDate(d = new Date()) {           // read via getUTC* — IST wall time
  return new Date(d.getTime() + 5.5 * 3600 * 1000);
}
function indiaOpenNow(d = new Date()) {
  const t = istDate(d), dow = t.getUTCDay();
  if (dow === 0 || dow === 6) return false;                 // weekend
  if (NSE_HOLIDAYS_2026.includes(t.toISOString().slice(0, 10))) return false;
  const mins = t.getUTCHours() * 60 + t.getUTCMinutes();
  return mins >= 555 && mins < 930;                          // 09:15–15:30 IST
}
function fxOpenNow(d = new Date()) {
  const dow = d.getUTCDay(), mins = d.getUTCHours() * 60 + d.getUTCMinutes();
  if (dow === 0 || dow === 6) return false;                  // weekend
  if (dow === 5 && mins >= 21 * 60) return false;            // Fri 21:00 UTC cut
  return true;
}
function demoClock() {
  return { now_utc: new Date().toISOString(), legs: {
    india: { open: indiaOpenNow(), label: "NSE 09:15–15:30 IST" },
    mt5_forex: { open: fxOpenNow(), label: "FX 24/5 (UTC week)" },
    mt5_crypto: { open: true, label: "Crypto 24/7" },
  } };
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
  gate: { paper_days_completed: 5, clean_reconciliation_streak: 2,
          sebi_checks_passed: false, static_ip_confirmed: false, human_ack: false },
  trades: [
    { symbol: "RELIANCE", direction: "buy", realized_r: 1.8, reason: "trail_stop", mfe_captured_pct: 78.3, sleeve: "tsmom_f" },
    { symbol: "BTCUSD", direction: "sell", realized_r: -0.9, reason: "stop_hit", mfe_captured_pct: 0.0, sleeve: "tsmom_f" },
    { symbol: "EURUSD", direction: "buy", realized_r: 0.4, reason: "time_stop_no_progress", mfe_captured_pct: 31.0, sleeve: "accurate" },
  ],
  history: [
    { date: "2026-08-07", symbol: "RELIANCE", leg: "india", direction: "buy", realized_r: 1.8, exit_reason: "trail_stop", sleeve: "tsmom_f" },
    { date: "2026-08-06", symbol: "BTCUSD", leg: "mt5_crypto", direction: "sell", realized_r: -0.9, exit_reason: "stop_hit", sleeve: "tsmom_f" },
    { date: "2026-08-06", symbol: "EURUSD", leg: "mt5_forex", direction: "buy", realized_r: 0.4, exit_reason: "time_stop_no_progress", sleeve: "accurate" },
    { date: "2026-08-05", symbol: "TCS", leg: "india", direction: "buy", realized_r: -1.0, exit_reason: "stop_hit", sleeve: "tsmom" },
    { date: "2026-08-04", symbol: "RELIANCE", leg: "india", direction: "buy", realized_r: 0.7, exit_reason: "time_stop_no_progress", sleeve: "tsmom_f" },
    { date: "2026-08-03", symbol: "ETHUSD", leg: "mt5_crypto", direction: "buy", realized_r: 2.4, exit_reason: "trail_stop", sleeve: "tsmom" },
    { date: "2026-07-31", symbol: "GBPUSD", leg: "mt5_forex", direction: "sell", realized_r: -0.8, exit_reason: "stop_hit", sleeve: "accurate" },
    { date: "2026-07-30", symbol: "RELIANCE", leg: "india", direction: "buy", realized_r: 3.1, exit_reason: "profit_lock", sleeve: "tsmom_f" },
    { date: "2026-07-29", symbol: "BTCUSD", leg: "mt5_crypto", direction: "buy", realized_r: 0.9, exit_reason: "trail_stop", sleeve: "tsmom" },
    { date: "2026-07-28", symbol: "INFY", leg: "india", direction: "buy", realized_r: -0.9, exit_reason: "stop_hit", sleeve: "tsmom" },
    { date: "2026-07-27", symbol: "EURUSD", leg: "mt5_forex", direction: "buy", realized_r: 1.2, exit_reason: "trail_stop", sleeve: "accurate" },
    { date: "2026-07-24", symbol: "RELIANCE", leg: "india", direction: "buy", realized_r: -0.5, exit_reason: "time_stop_no_progress", sleeve: "tsmom_f" },
  ],
  brokers: {
    india: { hub: "openalgo", provider: "dhan",
             providers_available: ["dhan", "shoonya", "fyers", "zerodha"],
             base_url: "http://127.0.0.1:5000", default_exchange: "NSE",
             env: { INDIA_BROKER_API_KEY: true, INDIA_BROKER_SECRET: false },
             static_ip_confirmed: false },
    mt5: { exec_service_url: "https://mt5-vps.internal:8443",
           symbol_classes: { forex: ["EURUSD", "GBPUSD"], crypto_cfd: ["BTCUSD", "ETHUSD"] },
           env: { MT5_LOGIN: true, MT5_PASSWORD: true, MT5_SERVER: true, MT5_SERVICE_TOKEN: false } },
  },
  events: [
    { t: "10:42:11", m: "anomaly_guard: velocity_5s trigger NIFTY — entries paused 15m" },
    { t: "10:41:58", m: "regime NIFTY → SHOCK (vol pctl 0.97)" },
    { t: "09:15:02", m: "session open · workers healthy · VaR95 0.8%" },
  ],
  analysis: {
    technicals: [
      { symbol: "RELIANCE", read: "bullish", score: 2,
        studies: { wilder_rsi: 58.2, macd_hist: 12.3, adx: 31.5, pct_b: 0.72, stoch_k: 68.0 } },
      { symbol: "BTCUSD", read: "bearish", score: -2,
        studies: { wilder_rsi: 74.1, macd_hist: -180.4, adx: 27.8, pct_b: 1.04, stoch_k: 82.0 } },
      { symbol: "EURUSD", read: "neutral", score: 0,
        studies: { wilder_rsi: 49.5, macd_hist: 0.0002, adx: 14.2, pct_b: 0.51, stoch_k: 50.0 } },
    ],
    fundamentals: [
      { symbol: "RELIANCE", score: 83.3, coverage: 6,
        ratios: { pe: 22.4, pb: 3.1, roe: 0.18, debt_to_equity: 0.6, net_margin: 0.12, current_ratio: 1.7 },
        flags: ["strong_roe", "strong_net_margin", "strong_current_ratio"] },
      { symbol: "TCS", score: 58.3, coverage: 6,
        ratios: { pe: 28.9, pb: 11.2, roe: 0.42, debt_to_equity: 0.1, net_margin: 0.19, current_ratio: 2.6 },
        flags: ["strong_roe", "weak_fcf_yield"] },
    ],
  },
};

/* demo candles — SESSION-AWARE: a closed market's series does NOT advance.
 * This is the fix for the operator video: no more india candles at night. */
const demoCandles = { RELIANCE: [], BTCUSD: [], EURUSD: [] };
const CANDLE_SEEDS = { RELIANCE: 2503, BTCUSD: 60110, EURUSD: 1.0852 };
const CANDLE_LEG = { RELIANCE: "india", BTCUSD: "mt5_crypto", EURUSD: "mt5_forex" };
let candleTick = 0;

function legOpenNow(leg) {
  if (leg === "india") return indiaOpenNow();
  if (leg === "mt5_forex") return fxOpenNow();
  return true;
}

function stepDemoCandles() {
  candleTick++;
  for (const sym of Object.keys(demoCandles)) {
    const arr = demoCandles[sym];
    if (arr.length === 0) {           // backfill a static history once
      let px = CANDLE_SEEDS[sym];
      for (let i = 0; i < 60; i++) {
        const drift = (Math.sin(i * 1.7 + sym.length) * 0.004 + 0.0008) * px;
        const o = px, c = px + drift;
        arr.push({ o, c, h: Math.max(o, c) * 1.002, l: Math.min(o, c) * 0.998 });
        px = c;
      }
      continue;
    }
    if (!legOpenNow(CANDLE_LEG[sym])) continue;   // market closed -> frozen
    const last = arr[arr.length - 1].c;
    const vol = sym === "EURUSD" ? 0.0008 : 0.004;
    const move = (Math.random() - 0.48) * vol * last;
    const o = last, c = last + move;
    arr.push({ o, c, h: Math.max(o, c) * 1.001, l: Math.min(o, c) * 0.999 });
    if (arr.length > 60) arr.shift();
  }
}

function demoApi(path, opts) {
  state.role = "operator";
  if (path === "/state") {
    // equity only drifts from OPEN legs — a closed india book cannot move
    const anyOpen = Object.values(CANDLE_LEG).some(legOpenNow);
    if (anyOpen) demo.equity += (Math.random() - 0.45) * 400;
    demo.pnl = demo.equity - 1000000;
    stepDemoCandles();
    return Promise.resolve(JSON.parse(JSON.stringify(demo)));
  }
  if (path === "/clock") return Promise.resolve(demoClock());
  if (path === "/approvals") return Promise.resolve(demo.approvals);
  if (path === "/trades") return Promise.resolve(demo.trades);
  if (path === "/pnl_history") return Promise.resolve(demo.pnl_history);
  if (path === "/config") return Promise.resolve(demo.config_view);
  if (path === "/analysis") return Promise.resolve(demo.analysis);
  if (path === "/brokers") return Promise.resolve(JSON.parse(JSON.stringify(demo.brokers)));
  if (path.startsWith("/history")) {
    const q = new URLSearchParams((path.split("?")[1] || ""));
    const sym = (q.get("symbol") || "").toUpperCase();
    const leg = q.get("leg") || "", reason = q.get("exit_reason") || "";
    const since = q.get("since") || "", until = q.get("until") || "";
    return Promise.resolve(demo.history.filter(r =>
      (!sym || r.symbol.toUpperCase() === sym) &&
      (!leg || r.leg === leg) && (!reason || r.exit_reason === reason) &&
      (!since || r.date >= since) && (!until || r.date <= until)));
  }
  if (path === "/brokers/test") return Promise.resolve({ ok: true, detail: "HTTP 200 (demo)" });
  if (path === "/brokers/save") {
    const body = JSON.parse(opts.body || "{}");
    Object.assign(demo.brokers[body.broker] || {}, body.settings || {});
    return Promise.resolve({ saved: body.settings || {} });
  }
  if (path === "/control/kill") { demo.halted = true; return Promise.resolve({ ok: true }); }
  if (path === "/control/unlock") { demo.halted = false; return Promise.resolve({ halted: false }); }
  if (path === "/control/close_position") {
    const body = JSON.parse(opts.body || "{}");
    const pos = demo.positions.find(p => p.symbol === body.symbol);
    if (!pos) return Promise.reject(new Error("http_404"));
    demo.positions = demo.positions.filter(p => p !== pos);
    demo.history.unshift({ date: new Date().toISOString().slice(0, 10),
      symbol: pos.symbol, leg: pos.leg, direction: pos.symbol ? "buy" : "",
      realized_r: pos.r_now ?? 0, exit_reason: "manual_close", sleeve: "manual" });
    return Promise.resolve({ symbol: pos.symbol, reason: "manual_close",
                             realized_r: pos.r_now ?? 0 });
  }
  if (path === "/control/order") {
    const body = JSON.parse(opts.body || "{}");
    if (!legOpenNow(CANDLE_LEG[body.symbol] ??
        ({ RELIANCE: "india", TCS: "india", HDFCBANK: "india" }[body.symbol] || "mt5_crypto"))) {
      return Promise.resolve({ accepted: false,
                               reason: "precheck_failed:session_failed" });
    }
    const entry = (demoCandles[body.symbol]
      ? demoCandles[body.symbol][demoCandles[body.symbol].length - 1].c : 100);
    demo.positions.push({ symbol: body.symbol, leg: CANDLE_LEG[body.symbol] || "india",
      qty: body.qty || 10, entry, stop: body.stop, r_now: 0.0,
      state: "RISK_ON", mfe_r: 0.0 });
    return Promise.resolve({ accepted: true, qty: body.qty || 10,
                             avg_fill_price: entry });
  }
  if (path === "/research/runs") {
    return Promise.resolve({ runs: demo.researchRuns || [], options: {
      strategies: ["baseline", "tsmom", "tsmom_f", "donchian", "rsi2",
                   "improved", "improved2", "improved3", "accurate", "accurate_ls"],
      datasets: ["india_6m", "forex_6m", "crypto_6m", "covid_2020",
                 "gfc_2008", "flash_crash_2012"], busy: false } });
  }
  if (path === "/research/run") {
    const body = JSON.parse(opts.body || "{}");
    demo.researchRuns = demo.researchRuns || [];
    demo.researchRuns.unshift({ id: `${body.strategy}_${body.dataset}_demo`,
      strategy: body.strategy, dataset: body.dataset, status: "done",
      results: { return_pct: 1.15, MAX_DRAWDOWN_pct: 0.82,
                 sharpe_annualized: 1.41, win_rate_pct: 57.9,
                 closed_trades: 38, reconciliation: "CLEAN",
                 audit_chain_ok: true } });
    return Promise.resolve(demo.researchRuns[0]);
  }
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
  const canClose = state.role === "operator" && !s.halted;
  tbody.innerHTML = (s.positions || []).map(p => `
    <tr><td>${esc(p.symbol)}</td><td>${esc(p.leg)}</td><td>${esc(p.qty)}</td>
    <td>${esc(p.entry)}</td><td>${esc(p.stop)}</td>
    <td class="${p.r_now >= 0 ? "pos" : "neg"}">${(p.r_now ?? 0).toFixed(1)}R</td>
    <td><span class="state ${esc(p.state)}">${esc(p.state)}</span></td>
    <td>${(p.mfe_r ?? 0).toFixed(1)}R</td>
    <td>${canClose
      ? `<button class="ghost small row-close pos-close" data-sym="${esc(p.symbol)}">CLOSE</button>`
      : ""}</td></tr>`).join("")
    || `<tr><td colspan="9" class="sub">no open positions</td></tr>`;
  document.querySelectorAll(".pos-close").forEach(btn =>
    btn.addEventListener("click", () => openCloseConfirm(btn.dataset.sym)));

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
  renderPortfolio(s);
  renderGate(s.gate);
}

function drawSpark() {
  drawLine($("spark"), state.equityHistory);
  drawLine($("equity-canvas"), state.equityHistory);
}

function drawLine(c, xs) {
  const ctx = c.getContext("2d");
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

/* ---------------- market clock (MODULE 58) ---------------- */

const LEG_SHORT = { india: "NSE", mt5_forex: "FX", mt5_crypto: "CRYPTO" };

async function renderClock() {
  const clk = await api("/clock").catch(() => null);
  state.lastClock = clk;
  const legs = (clk && clk.legs) || {};
  const chip = (leg, l) => `<span class="mkt-chip ${l.open ? "open" : "closed"}">
    ${esc(LEG_SHORT[leg] || leg)} ${l.open ? "● OPEN" : "○ CLOSED"}</span>`;
  const chips = Object.entries(legs).map(([leg, l]) => chip(leg, l)).join("");
  $("clock-chips").innerHTML = chips;
  $("clock-cards").innerHTML = chips || "—";

  // Markets page: sessions table + per-chart badges
  $("sessions-table").querySelector("tbody").innerHTML =
    Object.entries(legs).map(([leg, l]) => `
      <tr><td>${esc(leg)}</td><td>${esc(l.label || "")}</td>
      <td class="${l.open ? "pos" : "neg"}">${l.open ? "OPEN" : "CLOSED"}</td>
      <td class="sub">${esc((l.open ? l.next_close_utc : l.next_open_utc) || "—")}</td></tr>`)
      .join("") || `<tr><td colspan="4" class="sub">clock not wired</td></tr>`;

  for (const [sym, leg] of Object.entries(CANDLE_LEG)) {
    const open = legs[leg] ? legs[leg].open : legOpenNow(leg);
    const b = $(`chart-badge-${sym}`);
    b.textContent = open ? "OPEN" : "MARKET CLOSED — chart frozen";
    b.className = "mkt-badge " + (open ? "open" : "closed");
  }
}

/* ---------------- portfolio page (derived from /state) ---------------- */

function renderPortfolio(s) {
  const ps = s.positions || [];
  const notional = (p) => Math.abs((p.entry ?? 0) * (p.qty ?? 0));
  const risk = (p) => Math.abs(((p.entry ?? 0) - (p.stop ?? 0)) * (p.qty ?? 0));
  const total = ps.reduce((a, p) => a + notional(p), 0);
  const unreal = ps.reduce((a, p) => a + (p.unrealized ?? 0), 0);
  $("pf-exposure").textContent = fmtMoney(total);
  $("pf-unreal").textContent = fmtMoney(unreal);
  $("pf-unreal").className = "big " + (unreal >= 0 ? "pos" : "neg");
  $("pf-risk").textContent = fmtMoney(ps.reduce((a, p) => a + risk(p), 0));

  const legs = (state.lastClock && state.lastClock.legs) || {};
  const byLeg = new Map();
  for (const p of ps) {
    const cur = byLeg.get(p.leg) || { n: 0, notional: 0 };
    cur.n += 1; cur.notional += notional(p);
    byLeg.set(p.leg, cur);
  }
  $("pf-legs").innerHTML = [...byLeg.keys()].map(leg => {
    const open = legs[leg] ? legs[leg].open : legOpenNow(leg);
    return `<span class="mkt-chip ${open ? "open" : "closed"}">${esc(LEG_SHORT[leg] || leg)}
      ${open ? "OPEN" : "CLOSED"}</span>`;
  }).join("") || "—";

  $("pf-alloc").innerHTML = [...byLeg.entries()].map(([leg, v]) => {
    const pct = total ? (v.notional / total) * 100 : 0;
    const open = legs[leg] ? legs[leg].open : legOpenNow(leg);
    return `<div class="alloc-row"><span>${esc(leg)}</span>
      <div class="alloc-bar"><div class="alloc-fill" style="width:${pct.toFixed(0)}%"></div></div>
      <span>${pct.toFixed(0)}%</span>
      <span class="${open ? "pos" : "neg"}">${open ? "open" : "closed"}</span></div>`;
  }).join("") || `<div class="sub">no open positions</div>`;
}

/* ---------------- per-position close (v2.1) ---------------- */

function openCloseConfirm(symbol) {
  state.closeSymbol = symbol;
  $("close-symbol").textContent = symbol;
  $("close-confirm").classList.remove("hidden");
  armButton($("close-go"));
}

async function confirmClose() {
  if ($("close-go").disabled) return;     // not armed yet — defensive
  const sym = state.closeSymbol;
  if (!sym) return;
  const r = await api("/control/close_position", { method: "POST",
    body: JSON.stringify({ symbol: sym, confirm: `CLOSE ${sym}`,
                           reason: "cockpit manual close" }) })
    .catch(e => ({ error: e.message }));
  $("close-confirm").classList.add("hidden");
  state.closeSymbol = null;
  if (!r.error) { tick(); renderBlotter(); renderHistory(); }
}

/* ---------------- manual trade ticket (v2.1) ---------------- */

function ticketSymbols(s) {
  const feed = (s && s.feed && s.feed.symbols) ? Object.keys(s.feed.symbols) : [];
  const fallback = ["RELIANCE", "TCS", "HDFCBANK", "EURUSD", "GBPUSD",
                    "USDJPY", "BTCUSD", "ETHUSD"];
  const syms = feed.length ? feed : fallback;
  const sel = $("tk-symbol");
  const have = sel.dataset.syms || "";
  if (have !== syms.join(",")) {
    sel.dataset.syms = syms.join(",");
    sel.innerHTML = syms.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
  }
}

function openTicketConfirm() {
  const sym = $("tk-symbol").value, dir = $("tk-direction").value;
  const stop = parseFloat($("tk-stop").value);
  if (!sym || !(stop > 0)) {
    $("tk-msg").textContent = "✗ a protective stop is mandatory";
    return;
  }
  $("tk-summary").textContent =
    `${dir.toUpperCase()} ${sym} @ market · stop ${stop}`;
  $("tk-confirm").classList.remove("hidden");
  armButton($("tk-go"));
}

async function confirmTicket() {
  if ($("tk-go").disabled) return;        // not armed yet — defensive
  const sym = $("tk-symbol").value;
  const body = { symbol: sym, direction: $("tk-direction").value,
                 stop: parseFloat($("tk-stop").value) || 0,
                 qty: parseFloat($("tk-qty").value) || 0,
                 confirm: `PLACE ${sym}` };
  const r = await api("/control/order", { method: "POST",
    body: JSON.stringify(body) }).catch(e => ({ error: e.message }));
  $("tk-confirm").classList.add("hidden");
  if (r.error) {
    $("tk-msg").textContent = r.error === "forbidden"
      ? "✗ operator token required" : `✗ ${r.error}`;
  } else if (r.accepted) {
    $("tk-msg").textContent = `✓ filled x${r.qty} @ ${r.avg_fill_price}`;
    $("tk-stop").value = ""; $("tk-qty").value = "";
  } else {
    // the router's rejection reason verbatim — session:india_closed,
    // budget_exhausted, margin, heat cap … the operator sees WHY
    $("tk-msg").textContent = `✗ refused: ${r.reason || "unknown"}`;
  }
  tick();
}

/* ---------------- research lab (v2.1) ---------------- */

async function renderResearch() {
  const data = await api("/research/runs").catch(() => null);
  if (!data) return;
  const opts = data.options || {};
  const fill = (id, items) => {
    const sel = $(id), key = (items || []).join(",");
    if (sel.dataset.opts !== key) {
      sel.dataset.opts = key;
      sel.innerHTML = (items || []).map(x =>
        `<option value="${esc(x)}">${esc(x)}</option>`).join("");
    }
  };
  fill("rs-strategy", opts.strategies);
  fill("rs-dataset", opts.datasets);
  $("rs-run").disabled = !!opts.busy || state.role !== "operator";

  const fmt = (v, digits = 2) => (v == null ? "—" : Number(v).toFixed(digits));
  $("rs-table").querySelector("tbody").innerHTML = (data.runs || []).map(r => {
    const res = r.results || {};
    return `<tr><td>${esc(r.strategy)} · ${esc(r.dataset)}</td>
      <td class="${r.status === "done" ? "pos" : r.status === "failed" ? "neg" : "warn"}">${esc(r.status)}</td>
      <td class="${(res.return_pct ?? 0) >= 0 ? "pos" : "neg"}">${fmt(res.return_pct)}%</td>
      <td>${fmt(res.MAX_DRAWDOWN_pct)}%</td>
      <td>${fmt(res.sharpe_annualized)}</td>
      <td>${fmt(res.win_rate_pct, 0)}%</td>
      <td>${esc(res.closed_trades ?? "—")}</td>
      <td class="${res.reconciliation === "CLEAN" ? "pos" : "neg"}">${esc(res.reconciliation || "—")}</td></tr>`;
  }).join("") || `<tr><td colspan="8" class="sub">no runs yet — pick a strategy and dataset above</td></tr>`;
}

async function launchResearch() {
  const r = await api("/research/run", { method: "POST",
    body: JSON.stringify({ strategy: $("rs-strategy").value,
                           dataset: $("rs-dataset").value }) })
    .catch(e => ({ error: e.message }));
  $("rs-msg").textContent = r.error
    ? (r.error === "forbidden" ? "✗ operator token required" : `✗ ${r.error}`)
    : `✓ running ${r.id} — the certified harness is replaying real data, refresh in a minute`;
  renderResearch();
}

/* ---------------- go-live gate (read-only report) ---------------- */

function renderGate(g) {
  const item = (done, label) => `<div class="gate-item ${done ? "done" : "todo"}">
    <span class="tick">${done ? "✓" : "○"}</span><span>${esc(label)}</span></div>`;
  const html = !g ? "—" : [
    item((g.paper_days_completed ?? 0) >= 14, `Paper days ${g.paper_days_completed ?? 0}/14`),
    item((g.clean_reconciliation_streak ?? 0) >= 5, `Clean recon streak ${g.clean_reconciliation_streak ?? 0}/5`),
    item(!!g.sebi_checks_passed, "SEBI Feb-2025 checks (human, on VPS)"),
    item(!!(g.static_ip ?? g.static_ip_confirmed), "Broker static IP confirmed (human, on VPS)"),
    item(!!g.human_ack, "Risk acknowledgement phrase (human, on VPS)"),
  ].join("");
  $("gate-list").innerHTML = html;
  const doneCt = !g ? 0 : (html.match(/gate-item done/g) || []).length;
  $("gate-mini").textContent = g ? `live gate ${doneCt}/5` : "";
}

/* ---------------- P&L page ---------------- */

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

  // headline stats + sleeve attribution from the blotter
  const trades = await api("/trades").catch(() => []);
  const n = (trades || []).length;
  const wins = (trades || []).filter(t => (t.realized_r ?? 0) > 0).length;
  const sumR = (trades || []).reduce((a, t) => a + (t.realized_r ?? 0), 0);
  $("pnl-ntrades").textContent = String(n);
  $("pnl-winrate").textContent = n ? `${Math.round((wins / n) * 100)}%` : "—";
  $("pnl-avgr").textContent = n ? `${(sumR / n).toFixed(2)}R` : "—";
  $("pnl-costs").textContent = fmtMoney((state.lastState && state.lastState.costs) || 0);

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

/* ---------------- history screener ---------------- */

function histQuery() {
  const q = new URLSearchParams();
  if ($("f-symbol").value) q.set("symbol", $("f-symbol").value.trim());
  if ($("f-leg").value) q.set("leg", $("f-leg").value);
  if ($("f-reason").value) q.set("exit_reason", $("f-reason").value);
  if ($("f-since").value) q.set("since", $("f-since").value.trim());
  if ($("f-until").value) q.set("until", $("f-until").value.trim());
  const qs = q.toString();
  return "/history" + (qs ? `?${qs}` : "");
}

async function renderHistory() {
  const rows = await api(histQuery()).catch(() => []);
  state.histRows = rows || [];
  const sumR = state.histRows.reduce((a, r) => a + (r.realized_r ?? 0), 0);
  const wins = state.histRows.filter(r => (r.realized_r ?? 0) > 0).length;
  $("hist-summary").textContent = state.histRows.length
    ? `${state.histRows.length} trades · ${wins} wins · net ${sumR >= 0 ? "+" : ""}${sumR.toFixed(2)}R`
    : "no trades match the filter";
  $("hist-table").querySelector("tbody").innerHTML = state.histRows.map(r => `
    <tr><td>${esc(r.date || "")}</td><td>${esc(r.symbol)}</td><td>${esc(r.leg || "")}</td>
    <td>${esc(r.direction || "")}</td>
    <td class="${(r.realized_r ?? 0) >= 0 ? "pos" : "neg"}">${(r.realized_r ?? 0).toFixed(2)}R</td>
    <td>${esc(r.exit_reason || "")}</td><td>${esc(r.sleeve || "")}</td></tr>`).join("")
    || `<tr><td colspan="7" class="sub">no rows</td></tr>`;
}

function exportHistoryCsv() {
  if (typeof Blob === "undefined" || typeof URL === "undefined" || !URL.createObjectURL
      || typeof document.createElement !== "function") return;
  const cols = ["date", "symbol", "leg", "direction", "realized_r", "exit_reason", "sleeve"];
  const lines = [cols.join(",")].concat(state.histRows.map(r =>
    cols.map(c => JSON.stringify(r[c] ?? "")).join(",")));
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "trade_history.csv";
  a.click();
}

/* ---------------- markets: candles (demo feed only) ---------------- */

function drawCandles(c, candles) {
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  if (!candles || candles.length < 2) return;
  const lo = Math.min(...candles.map(k => k.l)), hi = Math.max(...candles.map(k => k.h));
  const span = (hi - lo) || 1;
  const w = c.width / candles.length;
  const y = (v) => c.height - 4 - ((v - lo) / span) * (c.height - 8);
  candles.forEach((k, i) => {
    const x = i * w + w / 2;
    const up = k.c >= k.o;
    ctx.strokeStyle = ctx.fillStyle = up ? "#2ecc71" : "#e74c3c";
    ctx.beginPath(); ctx.moveTo(x, y(k.h)); ctx.lineTo(x, y(k.l)); ctx.stroke();
    const top = y(Math.max(k.o, k.c)), bot = y(Math.min(k.o, k.c));
    ctx.fillRect(x - w * 0.3, top, w * 0.6, Math.max(1, bot - top));
  });
}

function renderCharts() {
  if (DEMO) {
    $("chart-note").textContent = "demo feed — closed markets freeze (session-aware)";
    for (const sym of Object.keys(demoCandles)) drawCandles($(`chart-${sym}`), demoCandles[sym]);
  } else {
    $("chart-note").textContent =
      "live candle feed is not served by the gateway — sessions and analysis below are live; use the broker terminal for charts";
  }
}

/* ---------------- analysis (MODULES 56/57, read-only) ---------------- */

function readBadge(read) {
  return `<span class="read ${esc(read)}">${esc((read || "n/a").toUpperCase())}</span>`;
}
function scoreRing(score) {
  const v = Math.max(0, Math.min(100, Number(score) || 0));
  const tone = v >= 70 ? "pos" : v >= 40 ? "warn" : "neg";
  return `<span class="score ${tone}">${score == null ? "—" : v.toFixed(0)}</span>`;
}
async function renderAnalysis() {
  const a = await api("/analysis").catch(() => null);
  const tech = a && a.technicals;
  const fund = a && a.fundamentals;

  $("technicals").innerHTML = (tech && tech.length) ? tech.map(t => {
    const s = t.studies || {};
    const chip = (label, val) => `<span class="chip mono">${esc(label)} ${esc(val)}</span>`;
    return `<div class="analysis-item">
      <div class="analysis-head"><b>${esc(t.symbol)}</b>${readBadge(t.read)}</div>
      <div class="chips">
        ${chip("RSI", (s.wilder_rsi ?? 0).toFixed(1))}
        ${chip("MACD", (s.macd_hist ?? 0).toFixed(1))}
        ${chip("ADX", (s.adx ?? 0).toFixed(1))}
        ${chip("%B", (s.pct_b ?? 0).toFixed(2))}
        ${chip("%K", (s.stoch_k ?? 0).toFixed(0))}
      </div></div>`;
  }).join("") : `<div class="sub">no technicals (gateway /analysis not wired)</div>`;

  $("fundamentals").innerHTML = (fund && fund.length) ? fund.map(f => {
    const r = f.ratios || {};
    const chip = (label, val) => `<span class="chip mono">${esc(label)} ${esc(val)}</span>`;
    const flags = (f.flags || []).map(fl =>
      `<span class="flag ${fl.startsWith("strong") ? "pos" : "neg"}">${esc(fl.replace(/_/g, " "))}</span>`).join("");
    return `<div class="analysis-item">
      <div class="analysis-head"><b>${esc(f.symbol)}</b>${scoreRing(f.score)}
        <span class="sub">${esc(f.coverage ?? 0)}/6 metrics</span></div>
      <div class="chips">
        ${chip("P/E", (r.pe ?? 0).toFixed(1))}
        ${chip("P/B", (r.pb ?? 0).toFixed(1))}
        ${chip("ROE", ((r.roe ?? 0) * 100).toFixed(0) + "%")}
        ${chip("D/E", (r.debt_to_equity ?? 0).toFixed(2))}
        ${chip("NM", ((r.net_margin ?? 0) * 100).toFixed(0) + "%")}
      </div>
      <div class="flags">${flags}</div></div>`;
  }).join("") : `<div class="sub">no fundamentals (wire a provider — yfinance/FMP/OpenBB-as-service)</div>`;
}

/* ---------------- settings: brokers (MODULES 59/60) ---------------- */

function envChips(env) {
  return Object.entries(env || {}).map(([k, set]) =>
    `<span class="env-chip ${set ? "set" : "unset"}">${esc(k)} ${set ? "✓" : "✗ unset"}</span>`).join("");
}

async function renderBrokers() {
  const b = await api("/brokers").catch(() => null);
  if (!b || !b.india) {
    $("broker-india").innerHTML = `<div class="sub">/brokers not wired on this gateway</div>`;
    $("broker-mt5").innerHTML = `<div class="sub">/brokers not wired on this gateway</div>`;
    return;
  }
  const i = b.india;
  $("broker-india").innerHTML = `
    <div class="kv"><b>hub</b><span>${esc(i.hub || "openalgo")}</span></div>
    <div class="kv"><b>provider</b><span>${esc(i.provider || "—")}</span></div>
    <div class="kv"><b>base_url</b><span>${esc(i.base_url || "—")}</span></div>
    <div class="kv"><b>exchange</b><span>${esc(i.default_exchange || "—")}</span></div>
    <div class="kv"><b>credentials</b><span>${envChips(i.env)}</span></div>
    <div class="kv"><b>static IP gate</b><span class="${i.static_ip_confirmed ? "pos" : "neg"}">
      ${i.static_ip_confirmed ? "confirmed" : "NOT confirmed — set on VPS only"}</span></div>`;
  if (i.provider) $("bk-provider").value = i.provider;
  if (!$("bk-baseurl").value) $("bk-baseurl").value = i.base_url || "";

  const m = b.mt5;
  $("broker-mt5").innerHTML = `
    <div class="kv"><b>exec service</b><span>${esc(m.exec_service_url || "—")}</span></div>
    <div class="kv"><b>forex</b><span>${esc(((m.symbol_classes || {}).forex || []).join(", ") || "—")}</span></div>
    <div class="kv"><b>crypto CFD</b><span>${esc(((m.symbol_classes || {}).crypto_cfd || []).join(", ") || "—")}</span></div>
    <div class="kv"><b>credentials</b><span>${envChips(m.env)}</span></div>`;
  if (!$("bk-mt5-url").value) $("bk-mt5-url").value = m.exec_service_url || "";
}

async function renderConfig() {
  const cfg = await api("/config").catch(() => null);
  // textContent (not innerHTML): config is data, never markup
  $("config-view").textContent = cfg ? JSON.stringify(cfg, null, 2) : "unavailable";
}

/* ---------------- approvals ---------------- */

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
    renderCharts();
    ticketSymbols(s);
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
    tick(); renderApprovals(); renderBrokers();
  });

  // KILL: click -> confirm dialog -> (arms after ARM_MS) -> one click kills.
  // No typing under stress; the API's confirm phrase is supplied by the UI
  // after the explicit second click. Unlock friction is untouched.
  $("kill-btn").addEventListener("click", () => {
    $("kill-confirm").classList.remove("hidden");
    armButton($("kill-go"));
  });
  $("kill-cancel").addEventListener("click", () => $("kill-confirm").classList.add("hidden"));
  $("kill-go").addEventListener("click", async () => {
    if ($("kill-go").disabled) return;      // not armed yet — defensive
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

  // per-position close + trade ticket (v2.1)
  $("close-go").addEventListener("click", confirmClose);
  $("close-cancel").addEventListener("click", () => {
    $("close-confirm").classList.add("hidden"); state.closeSymbol = null;
  });
  $("tk-place").addEventListener("click", openTicketConfirm);
  $("tk-go").addEventListener("click", confirmTicket);
  $("tk-cancel").addEventListener("click", () => $("tk-confirm").classList.add("hidden"));

  // research lab
  $("rs-run").addEventListener("click", launchResearch);

  // history screener
  $("f-apply").addEventListener("click", renderHistory);
  $("f-csv").addEventListener("click", exportHistoryCsv);
  $("f-symbol").addEventListener("change", renderHistory);
  $("f-leg").addEventListener("change", renderHistory);
  $("f-reason").addEventListener("change", renderHistory);
  $("f-since").addEventListener("change", renderHistory);
  $("f-until").addEventListener("change", renderHistory);

  // broker settings (operator intents; gateway enforces RBAC + allowlists)
  $("bk-india-test").addEventListener("click", async () => {
    const r = await api("/brokers/test", { method: "POST",
      body: JSON.stringify({ broker: "india" }) }).catch(e => ({ ok: false, detail: e.message }));
    $("bk-india-msg").textContent = `${r.ok ? "✓" : "✗"} ${r.detail || ""}`;
  });
  $("bk-india-save").addEventListener("click", async () => {
    const r = await api("/brokers/save", { method: "POST",
      body: JSON.stringify({ broker: "india", settings: {
        provider: $("bk-provider").value, base_url: $("bk-baseurl").value.trim() } }) })
      .catch(e => ({ error: e.message }));
    $("bk-india-msg").textContent = r.error
      ? (r.error === "forbidden" ? "✗ operator token required" : `✗ ${r.error}`)
      : "✓ saved to overlay (restart runtime to apply)";
    renderBrokers();
  });
  $("bk-mt5-test").addEventListener("click", async () => {
    const r = await api("/brokers/test", { method: "POST",
      body: JSON.stringify({ broker: "mt5" }) }).catch(e => ({ ok: false, detail: e.message }));
    $("bk-mt5-msg").textContent = `${r.ok ? "✓" : "✗"} ${r.detail || ""}`;
  });
  $("bk-mt5-save").addEventListener("click", async () => {
    const r = await api("/brokers/save", { method: "POST",
      body: JSON.stringify({ broker: "mt5", settings: {
        exec_service_url: $("bk-mt5-url").value.trim() } }) })
      .catch(e => ({ error: e.message }));
    $("bk-mt5-msg").textContent = r.error
      ? (r.error === "forbidden" ? "✗ operator token required" : `✗ ${r.error}`)
      : "✓ saved to overlay (restart runtime to apply)";
    renderBrokers();
  });
}

async function boot() {
  navRender();
  wire();
  await probeRole();       // role known BEFORE first render (stored token case)
  tick();
  renderClock();
  renderApprovals();
  renderBlotter();
  renderPnlPanels();
  renderHistory();
  renderBrokers();
  renderConfig();
  renderAnalysis();
  renderResearch();
  setInterval(renderResearch, POLL_MS * 8);
  setInterval(renderClock, POLL_MS * 5);
  setInterval(renderBlotter, POLL_MS * 4);
  setInterval(renderPnlPanels, POLL_MS * 10);
  setInterval(renderAnalysis, POLL_MS * 10);
  setInterval(tick, POLL_MS);
}

boot();
