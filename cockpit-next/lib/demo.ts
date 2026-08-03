import type { CockpitState, Candle } from './types';

// Deterministic-ish mock world so the demo UI is alive without a gateway.
let t0 = Math.floor(Date.now() / 1000) - 240 * 60;
const SYMBOLS = ['RELIANCE', 'BTCUSD', 'EURUSD'];
const seeds: Record<string, number> = { RELIANCE: 2500, BTCUSD: 60000, EURUSD: 1.085 };
const candleStore: Record<string, Candle[]> = {};
let equityCurve: { time: number; value: number }[] = [];
let equity = 1_000_000;
let halted = false;
let tick = 0;

function rnd(seed: number) { const x = Math.sin(seed * 999.7) * 1e4; return x - Math.floor(x); }

function step() {
  tick++;
  for (const s of SYMBOLS) {
    const arr = candleStore[s] || (candleStore[s] = []);
    const last = arr.length ? arr[arr.length - 1].close : seeds[s];
    const drift = s === 'BTCUSD' ? 0.0006 : 0.0003;
    const vol = s === 'EURUSD' ? 0.0007 : 0.004;
    const move = drift + (rnd(tick + s.length * 13) - 0.48) * vol;
    const close = last * (1 + move);
    const high = Math.max(last, close) * (1 + rnd(tick + 2) * vol * 0.5);
    const low = Math.min(last, close) * (1 - rnd(tick + 3) * vol * 0.5);
    arr.push({ time: t0 + tick * 300, open: last, high, low, close });
    if (arr.length > 120) arr.shift();
  }
  equity += (rnd(tick + 7) - 0.46) * 900;
  equityCurve.push({ time: t0 + tick * 300, value: equity });
  if (equityCurve.length > 120) equityCurve.shift();
}
for (let i = 0; i < 80; i++) step();

export function demoState(): CockpitState {
  step();
  const px = (s: string) => candleStore[s][candleStore[s].length - 1].close;
  const pnl = equity - 1_000_000;
  return {
    mode: 'paper', halted, role: 'operator',
    equity, pnl, costs: 531.26 + tick * 0.4,
    var95: 0.008 + rnd(tick) * 0.01, varLimit: 0.02,
    positions: [
      { symbol: 'RELIANCE', leg: 'india', qty: 12, entry: 2503.1, stop: 2531 + tick % 5,
        r_now: 1.6 + rnd(tick) * 0.6, state: 'TRAILING', mfe_r: 2.2,
        unrealized: (px('RELIANCE') - 2503.1) * 12 },
      { symbol: 'BTCUSD', leg: 'mt5_crypto', qty: 0.12, entry: 60110, stop: 59200,
        r_now: 0.4 + rnd(tick + 1) * 0.5, state: 'RISK_ON', mfe_r: 0.7,
        unrealized: (px('BTCUSD') - 60110) * 0.12 },
      { symbol: 'EURUSD', leg: 'mt5_forex', qty: 0.5, entry: 1.0852, stop: 1.0852,
        r_now: 1.1, state: 'BREAKEVEN', mfe_r: 1.3,
        unrealized: (px('EURUSD') - 1.0852) * 100000 * 0.5 },
    ],
    equityCurve: [...equityCurve],
    candles: JSON.parse(JSON.stringify(candleStore)),
    workers: { var_worker: true, exit_manager: true, anomaly_guard: true,
               news_poll: true, reconciler: tick % 40 > 3 },
    approvals: [
      { id: 'rule-7', kind: 'rule',
        label: 'Skip entries when GEX regime = amplify + severity ≥ 7 (holdout p=0.04)' },
      { id: 'model-v3', kind: 'model',
        label: 'Model v3 promotion: Brier 0.184→0.171, after-cost +6.2% on holdout' },
    ],
    events: [
      { t: hhmm(tick), m: 'exit_manager RELIANCE: trail 3.0×ATR', level: 'info' },
      { t: hhmm(tick - 3), m: 'anomaly_guard NIFTY: velocity_5s trigger — entries paused 15m', level: 'alert' },
      { t: hhmm(tick - 5), m: 'regime BTCUSD → HIGH (vol pctl 0.88)', level: 'warn' },
      { t: hhmm(tick - 9), m: 'session open · workers healthy · VaR95 0.8%', level: 'info' },
    ],
    gex: {
      net: -1.8e6, regime: 'amplify',
      strikes: [24000, 24500, 25000, 25500, 26000].map((k, i) => ({
        strike: k, gex: (i - 2.3) * 1.2e6 + (rnd(tick + i) - 0.5) * 3e5 })),
    },
    gate: { paper_days_completed: 5, clean_reconciliation_streak: 2,
            sebi_checks_passed: false, static_ip: false, human_ack: false },
  };
}

export function demoKill() { halted = true; }
export function demoUnlock(phrase: string) { if (phrase === 'RESUME PAPER TRADING') halted = false; return !halted; }
function hhmm(n: number) {
  const total = 9 * 60 + 15 + n; const h = Math.floor(total / 60) % 24;
  const m = total % 60; return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}
