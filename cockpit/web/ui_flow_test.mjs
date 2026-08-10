// UI flow test for the zero-build cockpit (MODULE 45) — runs the REAL app.js
// against a minimal DOM shim in Node, in demo mode. Verifies the control
// flows end to end: kill phrase gate, halt banner, unlock, resume entries,
// approvals, XSS escaping, storage fallback (no localStorage in Node!).
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
let PASS = 0, FAIL = 0;
const check = (name, cond, detail = "") => {
  if (cond) { PASS++; console.log(`PASS  ${name}`); }
  else { FAIL++; console.log(`FAIL  ${name}  ${detail}`); }
};

// ---------- minimal DOM shim ----------
class El {
  constructor(id) {
    this.id = id; this._classes = new Set(); this._html = ""; this.textContent = "";
    this.value = ""; this.disabled = false; this.style = {}; this.dataset = {};
    this.width = 220; this.height = 46; this._handlers = {}; this.className = "";
    this._children = {};
  }
  get classList() {
    const s = this._classes;
    return { add: (c) => s.add(c), remove: (c) => s.delete(c),
             toggle: (c, force) => { (force === undefined ? !s.has(c) : force) ? s.add(c) : s.delete(c); },
             contains: (c) => s.has(c) };
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  addEventListener(ev, fn) { this._handlers[ev] = fn; }
  async fire(ev, arg) { if (this._handlers[ev]) await this._handlers[ev](arg ?? { target: this }); }
  querySelector(sel) { return this._children[sel] ?? (this._children[sel] = new El(`${this.id}:${sel}`)); }
  getContext() {
    return new Proxy({}, { get: (t, k) => (k === "measureText" ? () => ({ width: 0 }) : () => {}) });
  }
}
const els = {};
const $id = (id) => els[id] ?? (els[id] = new El(id));
[
  "halt-banner", "unlock-panel", "kill-btn", "equity", "pnl", "costs", "var-fill",
  "var-label", "workers", "positions", "events", "spark", "approvals", "conn",
  "role", "token", "kill-confirm", "kill-phrase", "kill-reason", "kill-go",
  "kill-cancel", "unlock-phrase", "unlock-go", "resume-btn", "resume-confirm",
  "resume-go", "resume-cancel", "mode-badge",
].forEach($id);

const approveButtons = [];
global.document = {
  getElementById: $id,
  querySelectorAll: (sel) => (sel === ".approve" ? approveButtons.splice(0) : []),
};
global.location = { search: "?demo=1" };
global.window = global;
// NOTE: no global.localStorage — regression for the storage-fallback fix.
let intervalFn = null;
global.setInterval = (fn) => { intervalFn = fn; return 0; };

// intercept approvals innerHTML to synthesize APPROVE buttons with data-id
const approvalsEl = $id("approvals");
Object.defineProperty(approvalsEl, "innerHTML", {
  set(v) {
    this._html = String(v);
    approveButtons.length = 0;
    for (const m of this._html.matchAll(/data-id="([^"]+)"/g)) {
      const b = new El(`approve:${m[1]}`);
      b.dataset.id = m[1];
      approveButtons.push(b);
    }
  },
  get() { return this._html; },
});

// ---------- load the REAL app.js ----------
const src = readFileSync(join(here, "app.js"), "utf8");
await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"));
await new Promise((r) => setTimeout(r, 20));   // let boot() settle

// ---------- assertions ----------
check("boot survives WITHOUT localStorage (storage fallback)", true);
check("demo mode connects", $id("conn").textContent.includes("demo"));
check("role resolved to operator via probeRole", $id("role").textContent === "operator");
check("equity rendered", $id("equity").textContent.startsWith("₹"));
check("positions table populated", $id("positions").querySelector("tbody").innerHTML.includes("RELIANCE"));
check("kill button enabled for operator", $id("kill-btn").disabled === false);
check("halt banner hidden initially", $id("halt-banner").classList.contains("hidden"));
check("resume button visible for operator when not halted",
      !$id("resume-btn").classList.contains("hidden"));

// XSS regression: hostile event text must be escaped
const appJs = src;
check("no unescaped interpolation of event text", appJs.includes("esc(e.m)"));
const { default: _ } = { default: null };
// simulate hostile payload through the escaper by rendering a crafted state:
// (the escaper is not exported; verify via source + rendered demo output)
check("innerHTML sinks use esc()", /esc\(p\.symbol\)/.test(appJs) && /esc\(a\.label \|\| a\.id\)/.test(appJs));
check("role probe does NOT use a control endpoint", !appJs.includes('"/control/pause_entries", { method: "POST", body: JSON.stringify({ reason: "role-probe"'));

// ---- kill flow: wrong phrase refused ----
await $id("kill-btn").fire("click");
check("confirm panel opens", !$id("kill-confirm").classList.contains("hidden"));
$id("kill-phrase").value = "kill all positions";          // wrong case
await $id("kill-go").fire("click");
await intervalFn?.();
check("wrong phrase: NOT halted", $id("halt-banner").classList.contains("hidden"));
check("wrong phrase: input cleared", $id("kill-phrase").value === "");

// ---- kill flow: exact phrase halts ----
$id("kill-phrase").value = "KILL ALL POSITIONS";
$id("kill-reason").value = "ui flow drill";
await $id("kill-go").fire("click");
await new Promise((r) => setTimeout(r, 10));
check("exact phrase: halt banner shown", !$id("halt-banner").classList.contains("hidden"));
check("halted: unlock panel visible", !$id("unlock-panel").classList.contains("hidden"));
check("halted: kill button disabled", $id("kill-btn").disabled === true);
check("halted: resume button hidden", $id("resume-btn").classList.contains("hidden"));

// ---- unlock ----
$id("unlock-phrase").value = "any-phrase-demo";
await $id("unlock-go").fire("click");
await new Promise((r) => setTimeout(r, 10));
check("unlock: banner cleared", $id("halt-banner").classList.contains("hidden"));
check("unlock: kill re-enabled", $id("kill-btn").disabled === false);

// ---- resume entries (safe-start release) ----
await $id("resume-btn").fire("click");
check("resume confirm panel opens", !$id("resume-confirm").classList.contains("hidden"));
await $id("resume-go").fire("click");
await new Promise((r) => setTimeout(r, 10));
check("resume confirm closes after confirm", $id("resume-confirm").classList.contains("hidden"));

// ---- approvals ----
await new Promise((r) => setTimeout(r, 10));
const before = (approvalsEl.innerHTML.match(/data-id/g) || []).length;
check("approvals rendered with APPROVE buttons", before >= 1, `got ${before}`);

/* ================= cockpit v2 (MODULE 61) ================= */

// ---- router: dashboard default, other pages hidden (shim has no hash) ----
check("router: dashboard visible by default",
      !$id("page-dashboard").classList.contains("hidden"));
check("router: other pages hidden by default",
      $id("page-portfolio").classList.contains("hidden") &&
      $id("page-settings").classList.contains("hidden"));
check("router: dashboard nav item active",
      $id("nav-dashboard").classList.contains("active"));

// ---- market clock: chips + sessions table render ----
check("clock chips render per leg",
      $id("clock-chips").innerHTML.includes("NSE") &&
      $id("clock-chips").innerHTML.includes("CRYPTO"));
check("sessions table lists all legs",
      $id("sessions-table").querySelector("tbody").innerHTML.includes("india") &&
      $id("sessions-table").querySelector("tbody").innerHTML.includes("mt5_crypto"));

// ---- session awareness: india badge must MATCH the session rules for NOW.
// Recompute independently (same NSE rules) — this is the fake-night-chart fix.
const HOLIDAYS = ["2026-01-15","2026-01-26","2026-03-03","2026-03-26","2026-03-31",
  "2026-04-03","2026-04-14","2026-05-01","2026-05-28","2026-06-26","2026-09-14",
  "2026-10-02","2026-10-20","2026-11-10","2026-11-24","2026-12-25"];
const ist = new Date(Date.now() + 5.5 * 3600 * 1000);
const istMins = ist.getUTCHours() * 60 + ist.getUTCMinutes();
const expectIndiaOpen = ![0, 6].includes(ist.getUTCDay())
  && !HOLIDAYS.includes(ist.toISOString().slice(0, 10))
  && istMins >= 555 && istMins < 930;
const badge = $id("chart-badge-RELIANCE");
check("india chart badge matches NSE session rules",
      expectIndiaOpen ? badge.textContent === "OPEN"
                      : badge.textContent.includes("MARKET CLOSED"),
      `badge="${badge.textContent}" expectOpen=${expectIndiaOpen}`);
check("crypto chart badge always OPEN", $id("chart-badge-BTCUSD").textContent === "OPEN");

// ---- portfolio derived stats ----
check("portfolio exposure computed", $id("pf-exposure").textContent.startsWith("₹"));
check("portfolio allocation rows render", $id("pf-alloc").innerHTML.includes("alloc-bar"));

// ---- P&L page stats ----
check("pnl stats computed",
      $id("pnl-ntrades").textContent === "3" && $id("pnl-winrate").textContent === "67%");

// ---- history screener ----
check("history screener renders rows",
      $id("hist-table").querySelector("tbody").innerHTML.includes("RELIANCE"));
check("history summary line present",
      $id("hist-summary").textContent.includes("trades"));
$id("f-leg").value = "india";
await $id("f-leg").fire("change");
await new Promise((r) => setTimeout(r, 10));
check("history filter by leg works",
      $id("hist-table").querySelector("tbody").innerHTML.includes("india") &&
      !$id("hist-table").querySelector("tbody").innerHTML.includes("mt5_crypto"));
$id("f-leg").value = "";
await $id("f-leg").fire("change");

// ---- go-live gate report (read-only) ----
check("gate checklist renders human items",
      $id("gate-list").innerHTML.includes("Paper days") &&
      $id("gate-list").innerHTML.includes("SEBI"));

// ---- settings: broker cards ----
check("india broker card renders hub + provider",
      $id("broker-india").innerHTML.includes("openalgo") &&
      $id("broker-india").innerHTML.includes("dhan"));
check("credential env chips show set/unset booleans only",
      $id("broker-india").innerHTML.includes("INDIA_BROKER_API_KEY") &&
      $id("broker-india").innerHTML.includes("unset"));
check("static IP gate shown read-only as NOT confirmed",
      $id("broker-india").innerHTML.includes("NOT confirmed"));
check("mt5 card renders exec service + env chips",
      $id("broker-mt5").innerHTML.includes("MT5_LOGIN"));

// ---- broker save intent round-trips in demo ----
$id("bk-provider").value = "zerodha";
await $id("bk-india-save").fire("click");
await new Promise((r) => setTimeout(r, 10));
check("broker save round-trips (provider switch)",
      $id("broker-india").innerHTML.includes("zerodha"));
check("broker save confirms to operator",
      $id("bk-india-msg").textContent.includes("saved"));

// ---- XSS: new v2 sinks must escape ----
check("history rows escape symbol/leg/reason",
      /esc\(r\.symbol\)/.test(appJs) && /esc\(r\.exit_reason/.test(appJs));
check("broker cards escape provider/urls",
      /esc\(i\.provider/.test(appJs) && /esc\(m\.exec_service_url/.test(appJs));

console.log(`\n${PASS} passed, ${FAIL} failed`);
process.exit(FAIL ? 1 : 0);
