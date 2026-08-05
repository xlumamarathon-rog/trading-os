#!/usr/bin/env python3
"""RESEARCH replay — pluggable entry strategies through the FULL real stack.

Identical realism contract to scripts/paper_replay_real.py (real OHLC, real
cost schedules, real sizer/router/exit-manager/kill-switch/gate), but the
entry signal is selectable so candidate strategies can be compared on REAL
results, not paper estimates.

Run:  python3 scripts/research_replay.py <strategy> <data_dir> <out_dir> [exit_overrides_json]

Strategies:
  baseline  — production rule: prev close > SMA20, long-only (control)
  tsmom     — time-series momentum long/short: SMA50 direction + 63d momentum
  donchian  — 20-day channel breakout long/short, entries skipped in SHOCK
  rsi2      — mean reversion: long when prev close > SMA50 and RSI(2) < 10
  improved  — regime-aware combo: SMA20/SMA50 alignment + 63d momentum,
              long/short, no fresh entries during SHOCK vol regime
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from src.app import LiveGateError, assert_live_allowed
from src.core.config_loader import load_config
from src.core.kill_switch import KillSwitch
from src.core.margin_checker import MarginChecker
from src.core.order_router import VAR_CACHE_KEY, OrderRequest, OrderRouter
from src.core.paper_broker import PaperBroker
from src.exits.adapters.composite import CompositeStopAdapter
from src.exits.adapters.india_stops import IndiaStopAdapter
from src.exits.adapters.mt5_stops import Mt5StopAdapter
from src.exits.exit_manager import ExitManager
from src.intel.anomaly_guard import AnomalyGuard, Tick
from src.ops.eod_reconciler import reconcile
from src.ops.paper_report import advance_gate
from src.ops.paper_server import create_paper_server
from src.ops.persistence import JsonlAuditLog

STRATEGY = sys.argv[1] if len(sys.argv) > 1 else "baseline"
DATA_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/real")
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(f"data/research/{STRATEGY}")
EXIT_OVERRIDES = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
LONG_ONLY = bool(int(os.environ.get("LONG_ONLY", "0")))
CFG = load_config("config/master.yaml")

META = {
    "RELIANCE": {"leg": "india", "lot": 1, "adv": 8_000_000},
    "EURUSD": {"leg": "mt5_forex", "lot": 1000, "adv": 1e12,
               "half_spread": 0.00005, "commission_pct": 0.000035},
    "BTCUSD": {"leg": "mt5_crypto", "lot": 0.01, "adv": 5e9,
               "half_spread": 17.5, "commission_pct": 0.0},
}

# Datasets may carry their own symbol universe + report window
# (see scripts/fetch_market_data.py). REPORT_FROM slices the metrics to the
# report window while indicators warm up on the real lead-in bars.
REPORT_FROM = os.environ.get("REPORT_FROM", "")
_symbols_file = DATA_DIR / "symbols.json"
if _symbols_file.exists():
    _spec = json.loads(_symbols_file.read_text())
    META = _spec["symbols"]
    REPORT_FROM = REPORT_FROM or _spec.get("report_from", "")

DEFAULT_SIGMA = {"india": 0.016, "mt5_forex": 0.005, "mt5_crypto": 0.035}

# Portfolio giveback throttle: pause NEW entries while equity sits more than
# GIVEBACK_PCT below its rolling 20-session high (open positions keep their
# stops/trails — this only stops adding risk during a losing cluster).
GIVEBACK_PCT = float(os.environ.get("GIVEBACK_PCT", "0"))   # e.g. 0.02 = 2%
TICKS_PER_BAR = 24
SUB_BAR = 6
STARTING_CASH = 1_000_000.0


def load_real():
    """Load whichever META symbols exist in DATA_DIR (e.g. no BTC before 2014)."""
    out = {}
    for sym in list(META):
        p = DATA_DIR / f"{sym}.json"
        if p.exists():
            out[sym] = json.loads(p.read_text())
        else:
            META.pop(sym)
    if not out:
        raise SystemExit(f"no symbol data found in {DATA_DIR}")
    return out


def atr14(bars, i):
    if i < 15:
        return None
    trs = []
    for k in range(i - 13, i + 1):
        h, l, pc = bars[k]["high"], bars[k]["low"], bars[k - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / 14


def sma(bars, i, n=20):
    if i < n:
        return None
    return sum(b["close"] for b in bars[i - n:i]) / n


def mom(bars, i, n=63):
    if i - 1 - n < 0:
        return None
    return bars[i - 1]["close"] / bars[i - 1 - n]["close"] - 1


def rsi(bars, i, n=2):
    if i - 1 - n < 0:
        return None
    gains = losses = 0.0
    for k in range(i - n, i):
        d = bars[k]["close"] - bars[k - 1]["close"]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if gains + losses == 0:
        return 50.0
    return 100.0 * gains / (gains + losses)


def donchian(bars, i, n=20):
    if i - 1 - n < 0:
        return None, None
    window = bars[i - 1 - n:i - 1]
    return max(b["high"] for b in window), min(b["low"] for b in window)


# ---- entry strategies: return "buy" | "sell" | None ------------------------

def sig_baseline(bars, i, regime):
    s20 = sma(bars, i, 20)
    if s20 and bars[i - 1]["close"] > s20:
        return "buy"
    return None


def sig_tsmom(bars, i, regime):
    s50 = sma(bars, i, 50)
    m = mom(bars, i, 63)
    if s50 is None or m is None:
        return None
    c = bars[i - 1]["close"]
    if c > s50 and m > 0:
        return "buy"
    if c < s50 and m < 0:
        return "sell"
    return None


def sig_donchian(bars, i, regime):
    if regime["vol_regime"] == "SHOCK":
        return None
    hi, lo = donchian(bars, i, 20)
    if hi is None:
        return None
    c = bars[i - 1]["close"]
    if c > hi:
        return "buy"
    if c < lo:
        return "sell"
    return None


def sig_rsi2(bars, i, regime):
    s50 = sma(bars, i, 50)
    r = rsi(bars, i, 2)
    if s50 is None or r is None:
        return None
    if bars[i - 1]["close"] > s50 and r < 10:
        return "buy"
    return None


def sig_improved(bars, i, regime):
    if regime["vol_regime"] == "SHOCK":
        return None                       # never initiate into a shock bar
    s20, s50 = sma(bars, i, 20), sma(bars, i, 50)
    m = mom(bars, i, 63)
    if s20 is None or s50 is None or m is None:
        return None
    c = bars[i - 1]["close"]
    if c > s20 > s50 and m > 0:
        return "buy"
    if c < s20 < s50 and m < 0:
        return "sell"
    return None


def sig_improved2(bars, i, regime):
    """v2: faster re-entry — SMA20 + 21d momentum, still no entries in SHOCK."""
    if regime["vol_regime"] == "SHOCK":
        return None
    s20 = sma(bars, i, 20)
    m = mom(bars, i, 21)
    if s20 is None or m is None:
        return None
    c = bars[i - 1]["close"]
    if c > s20 and m > 0:
        return "buy"
    return None


def sig_improved3(bars, i, regime):
    """v3: fast momentum gated by long-trend confirmation — SHOCK filter,
    close > SMA20 AND SMA50, 21d momentum positive."""
    if regime["vol_regime"] == "SHOCK":
        return None
    s20, s50 = sma(bars, i, 20), sma(bars, i, 50)
    m = mom(bars, i, 21)
    if s20 is None or s50 is None or m is None:
        return None
    c = bars[i - 1]["close"]
    if c > s20 and c > s50 and m > 0:
        return "buy"
    return None


def sig_accurate(bars, i, regime):
    """Accuracy-focused: trend-aligned PULLBACK entry. Instead of chasing
    breakouts (low win rate in ranges), buy weakness inside a confirmed
    uptrend / short strength inside a confirmed downtrend. No SHOCK entries,
    no RANGE entries — fewer, higher-quality trades."""
    if regime["vol_regime"] == "SHOCK" or regime["trend_state"] == "RANGE":
        return None
    s20, s50 = sma(bars, i, 20), sma(bars, i, 50)
    m = mom(bars, i, 21)
    r = rsi(bars, i, 2)
    if s20 is None or s50 is None or m is None or r is None:
        return None
    c = bars[i - 1]["close"]
    if c > s50 and m > 0 and r < 25:
        return "buy"                     # dip inside an uptrend
    return None


def sig_accurate_ls(bars, i, regime):
    """accurate + mirrored short side: short the bounce inside a downtrend."""
    d = sig_accurate(bars, i, regime)
    if d:
        return d
    if regime["vol_regime"] == "SHOCK":
        return None
    s50 = sma(bars, i, 50)
    m = mom(bars, i, 21)
    r = rsi(bars, i, 2)
    if s50 is None or m is None or r is None:
        return None
    c = bars[i - 1]["close"]
    if c < s50 and m < 0 and r > 75:
        return "sell"                    # bounce inside a downtrend
    return None


def sig_tsmom_f(bars, i, regime):
    """TSMOM filtered: same long/short momentum, but only when price is
    meaningfully AWAY from SMA20 in either direction (>1%) — a symmetric
    not-in-chop filter (the repo regime maps downtrends to RANGE, which
    would silently kill shorts). No SHOCK entries."""
    if regime["vol_regime"] == "SHOCK":
        return None
    s20 = sma(bars, i, 20)
    if s20 is None:
        return None
    dev = (bars[i - 1]["close"] - s20) / s20
    if abs(dev) < 0.01:
        return None                      # chopping around the mean — stand aside
    return sig_tsmom(bars, i, regime)


SIGNALS = {"baseline": sig_baseline, "tsmom": sig_tsmom, "donchian": sig_donchian,
           "rsi2": sig_rsi2, "improved": sig_improved, "improved2": sig_improved2,
           "improved3": sig_improved3, "accurate": sig_accurate,
           "accurate_ls": sig_accurate_ls, "tsmom_f": sig_tsmom_f}


def intrabar_path(bar, rng):
    o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
    way = [o, l, h, c] if c >= o else [o, h, l, c]
    ticks = []
    per_leg = TICKS_PER_BAR // (len(way) - 1)
    for a, b in zip(way, way[1:]):
        for j in range(per_leg):
            f = (j + 1) / per_leg
            px = a + (b - a) * f + rng.gauss(0, abs(h - l) * 0.02)
            ticks.append(min(h, max(l, px)))
    ticks[-1] = c
    return ticks


def real_regime(bars, i):
    s = sma(bars, i)
    tr = max(bars[i]["high"] - bars[i]["low"],
             abs(bars[i]["high"] - bars[i - 1]["close"]),
             abs(bars[i]["low"] - bars[i - 1]["close"]))
    a = atr14(bars, i)
    vol = "SHOCK" if (a and tr > 2.5 * a) else "NORMAL"
    if s is None:
        return {"trend_state": "RANGE", "vol_regime": vol}
    above = (bars[i - 1]["close"] - s) / s
    trend = "STRONG_TREND" if above > 0.02 else ("WEAK_TREND" if above > 0 else "RANGE")
    return {"trend_state": trend, "vol_regime": vol}


class MemRedis:
    def __init__(self):
        import time as _t
        self._t, self.store, self._exp = _t, {}, {}

    async def get(self, k):
        exp = self._exp.get(k)
        if exp is not None and self._t.time() > exp:
            self.store.pop(k, None); self._exp.pop(k, None)
        return self.store.get(k)

    async def set(self, k, v): self.store[k] = v; self._exp.pop(k, None)
    async def setex(self, k, ttl, v): self.store[k] = v; self._exp[k] = self._t.time() + ttl
    async def delete(self, k): self.store.pop(k, None); self._exp.pop(k, None)


class PaperMarginAPI:
    def __init__(self, broker): self.broker = broker
    async def available_margin(self): return self.broker.available_margin()
    async def required_margin(self, s, q, p, prod): return q * p
    async def free_margin(self): return self.broker.available_margin()
    async def equity(self): return self.broker.equity()
    async def margin_required(self, s, lots): return lots * self.broker.last_price.get(s, 0.0)


class Connections:
    def __init__(self, app):
        self._c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://paper")
    def get_openalgo(self): return self._c
    def get_mt5(self): return self._c


async def run():
    OUT.mkdir(parents=True, exist_ok=True)
    gate_path = OUT / "gate_state.json"
    gate_path.unlink(missing_ok=True)
    data = load_real()
    signal_fn = SIGNALS[STRATEGY]

    broker = PaperBroker(
        costs=CFG.execution_costs.india, impact=CFG.execution_costs.impact_model,
        starting_cash=STARTING_CASH,
        adv_map={s: m["adv"] for s, m in META.items()},
        daily_sigma_map={s: DEFAULT_SIGMA.get(m["leg"], 0.02) for s, m in META.items()},
        mt5_cost_map={s: {"half_spread": m["half_spread"], "commission_pct": m["commission_pct"]}
                      for s, m in META.items() if "half_spread" in m})
    app = create_paper_server(broker)
    conns = Connections(app)
    redis = MemRedis()
    redis.store[VAR_CACHE_KEY] = "0.005"
    audit = JsonlAuditLog(OUT / "audit.jsonl")
    ks = KillSwitch(redis=redis, brokers={}, sentinel_path=OUT / "halt.sentinel",
                    unlock_phrase="X", auto_trigger_daily_loss_pct=0.03,
                    auto_trigger_var_breach=True, max_var_daily=CFG.risk_limits.max_var_daily)
    (OUT / "halt.sentinel").unlink(missing_ok=True)
    guard = AnomalyGuard(redis=redis, velocity_sigma={"s1": 6, "s5": 5, "s30": 4},
                         spread_blowout_mult=3.0, volume_spike_mult=5.0, cooloff_minutes=15)
    exits_log = []

    async def on_exit(sym, telemetry):
        exits_log.append({"symbol": sym, "reason": telemetry.exit_reason,
                          "realized_r": round(telemetry.realized_r, 2),
                          "mfe_captured_pct": round(telemetry.mfe_captured_pct, 1)})

    exit_cfg = dict(CFG.model_extra["exit_manager"])
    exit_cfg.update(EXIT_OVERRIDES)
    exit_mgr = ExitManager(exit_cfg, CompositeStopAdapter(
        india_adapter=IndiaStopAdapter(conns.get_openalgo(), apikey="PAPER", algo_id="ALGO-PAPER-1"),
        mt5_adapter=Mt5StopAdapter(conns.get_mt5())), on_exit=on_exit)
    router = OrderRouter(
        config=CFG, kill_switch=ks, anomaly_guard=guard,
        margin_checker=MarginChecker(CFG.risk_limits, india_api=PaperMarginAPI(broker),
                                     mt5_api=PaperMarginAPI(broker)),
        connections=conns, redis=redis, balance_fn=lambda: STARTING_CASH,
        signal_valid_fn=lambda s, d: True, band_check_fn=lambda s, p: True,
        session_open_fn=lambda leg: True,
        audit_fn=lambda row: audit.append({"type": "order", **row}))

    all_dates = sorted({b["date"] for bars in data.values() for b in bars})
    index_of = {s: {b["date"]: i for i, b in enumerate(bars)} for s, bars in data.items()}
    rng = random.Random(11)
    clock = 1_000_000.0
    equity_curve, entries, rejected = [], 0, {}
    throttled_days = 0

    for date in all_dates:
        # giveback throttle: no NEW risk while under water vs the rolling high
        throttle = False
        if GIVEBACK_PCT > 0 and equity_curve:
            recent = [p["equity"] for p in equity_curve[-20:]]
            if broker.equity() < max(recent) * (1 - GIVEBACK_PCT):
                throttle = True
                throttled_days += 1
        for sym, bars in data.items():
            i = index_of[sym].get(date)
            if i is None or i < 21:
                continue
            bar, a = bars[i], atr14(bars, i)
            regime = real_regime(bars, i)
            broker.on_tick(sym, bar["open"])

            held = sym in exit_mgr.positions and exit_mgr.positions[sym].state != "EXITED"
            direction = None if held or not a or throttle else signal_fn(bars, i, regime)
            # Shorts are now first-class: direction is threaded through the
            # exit adapters (BUY protective stops / buy-back exits) and the
            # paper server resolves the closing side from the open position.
            if direction == "sell" and LONG_ONLY:
                direction = None
            if direction:
                stop = bar["open"] - 2 * a if direction == "buy" else bar["open"] + 2 * a
                req = OrderRequest(symbol=sym, direction=direction, entry=bar["open"],
                                   stop=stop, atr=a,
                                   algo_id="ALGO-PAPER-1" if META[sym]["leg"] == "india" else None,
                                   lot_size=META[sym]["lot"], product="intraday")
                result = await router.route_order(req)
                if result.accepted and result.record.filled_qty > 0:
                    exit_mgr.positions.pop(sym, None)
                    await exit_mgr.attach(symbol=sym, direction=direction,
                                          entry=result.record.avg_fill_price,
                                          qty=result.record.filled_qty, atr=a,
                                          leg=META[sym]["leg"], lot_size=META[sym]["lot"])
                    entries += 1
                else:
                    rejected[result.reason.split(":")[0]] = rejected.get(result.reason.split(":")[0], 0) + 1

            ticks = intrabar_path(bar, rng)
            window = []
            for px in ticks:
                clock += 1.0
                broker.on_tick(sym, px)
                await guard.process_tick(sym, Tick(ts=clock, price=px,
                                                   bid=px * 0.9999, ask=px * 1.0001, volume=1000))
                window.append(px)
                if len(window) == SUB_BAR:
                    await exit_mgr.on_bar(sym, max(window), min(window), window[-1], regime)
                    window = []
        await redis.delete("PAUSE_ENTRIES")
        gate = advance_gate(gate_path, reconciliation_clean=True)
        equity_curve.append({"date": date, "equity": broker.equity()})

    internal = [{"client_order_id": f["client_order_id"], "symbol": f["symbol"],
                 "qty": f["qty"], "price": f["price"]} for f in broker.tradebook()]
    rep = reconcile("research-final", internal, broker.tradebook(),
                    naked_positions=exit_mgr.naked_positions())

    # metrics over the REPORT window only (indicators warmed on lead-in bars)
    curve = [p for p in equity_curve if not REPORT_FROM or p["date"] >= REPORT_FROM]
    if not curve:
        curve = equity_curve
    eq = [p["equity"] for p in curve]
    base_equity = eq[0]
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    rets = [(eq[k] / eq[k - 1] - 1) for k in range(1, len(eq))]
    mu = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1))
    sharpe = (mu / sd * math.sqrt(252)) if sd > 0 else 0.0
    meta = json.loads((DATA_DIR / "meta.json").read_text())
    bh = sum(m["period_return_pct"] for m in meta.values()) / len(meta)

    wins = [e for e in exits_log if e["realized_r"] > 0]
    reasons = {}
    for e in exits_log:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
    mfe = [e["mfe_captured_pct"] for e in exits_log if e["mfe_captured_pct"] is not None]

    results = {
        "strategy_name": STRATEGY,
        "exit_overrides": EXIT_OVERRIDES,
        "window": (f"{curve[0]['date']} → {curve[-1]['date']} "
                   f"({len(curve)} report days of {len(all_dates)} replayed)"),
        "final_equity": round(eq[-1], 2),
        "return_pct": round((eq[-1] / base_equity - 1) * 100, 2),
        "MAX_DRAWDOWN_pct": round(mdd * 100, 2),
        "sharpe_annualized": round(sharpe, 2),
        "buy_hold_equal_weight_return_pct": round(bh, 2),
        "entries": entries, "fills": len(broker.fills),
        "throttled_days": throttled_days,
        "closed_trades": len(exits_log),
        "win_rate_pct": round(100 * len(wins) / len(exits_log), 1) if exits_log else None,
        "avg_realized_r": round(sum(e["realized_r"] for e in exits_log) / len(exits_log), 2) if exits_log else None,
        "avg_mfe_captured_pct": round(sum(mfe) / len(mfe), 1) if mfe else None,
        "total_costs": round(broker.total_costs, 2),
        "exit_reasons": reasons,
        "entry_rejections": rejected,
        "reconciliation": "CLEAN" if rep.clean else "MISMATCH",
        "audit_chain_ok": audit.verify_chain(),
    }
    (OUT / "results.json").write_text(json.dumps(results, indent=1))
    (OUT / "equity_curve.json").write_text(json.dumps(equity_curve))
    (OUT / "exits.json").write_text(json.dumps(exits_log, indent=1))
    print(json.dumps(results, indent=1))

    try:
        assert_live_allowed(CFG, gate_path)
        print("!!! LIVE ALLOWED — BUG")
    except LiveGateError:
        pass                              # gate stays shut, as designed


if __name__ == "__main__":
    asyncio.run(run())
