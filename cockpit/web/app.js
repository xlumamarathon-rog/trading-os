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
const state = { token: localStorage.getItem("cockpit_token") || "", role: null,
                equityHistory: [], lastState: null };

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
  if (path === "/control/kill") { demo.halted = true; return Promise.resolve({ ok: true }); }
  if (path === "/control/unlock") { demo.halted = false; return Promise.resolve({ halted: false }); }
  if (path.startsWith("/control/approve/")) {
    demo.approvals = demo.approvals.filter(a => `/control/approve/${a.id}` !== path);
    return Promise.resolve({ ok: true });
  }
  return Promise.resolve({});
}

/* ---------------- rendering ---------------- */

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
    .map(([name, ok]) => `<span class="chip ${ok ? "" : "dead"}">${name}${ok ? "" : " ✗"}</span>`)
    .join("") || "—";

  const tbody = $("positions").querySelector("tbody");
  tbody.innerHTML = (s.positions || []).map(p => `
    <tr><td>${p.symbol}</td><td>${p.leg}</td><td>${p.qty}</td>
    <td>${p.entry}</td><td>${p.stop}</td>
    <td class="${p.r_now >= 0 ? "pos" : "neg"}">${(p.r_now ?? 0).toFixed(1)}R</td>
    <td><span class="state ${p.state}">${p.state}</span></td>
    <td>${(p.mfe_r ?? 0).toFixed(1)}R</td></tr>`).join("")
    || `<tr><td colspan="8" class="sub">no open positions</td></tr>`;

  $("events").innerHTML = (s.events || []).map(e =>
    `<div class="row"><span class="t">${e.t}</span><span>${e.m}</span></div>`).join("");

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

async function renderApprovals() {
  const items = await api("/approvals").catch(() => []);
  $("approvals").innerHTML = (items || []).map(a => `
    <div class="row"><span>${a.label || a.id}</span>
    ${state.role === "operator"
      ? `<button class="ghost small approve" data-id="${a.id}">APPROVE</button>` : ""}
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

function wire() {
  $("token").value = state.token;
  $("token").addEventListener("change", async (e) => {
    state.token = e.target.value.trim();
    localStorage.setItem("cockpit_token", state.token);
    // role probe: viewers get 403 on a no-op control preflight
    if (!DEMO) {
      try { await api("/control/pause_entries", { method: "POST", body: JSON.stringify({ reason: "role-probe", confirm: "" }) }); state.role = "operator"; }
      catch (err) { state.role = err.message === "forbidden" ? "viewer" : state.role; }
    }
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
}

wire();
tick();
renderApprovals();
setInterval(tick, POLL_MS);
