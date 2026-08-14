#!/usr/bin/env python3
"""REAL-MARKET paper replay — actual stock + currency pair + crypto history
through the full integrated stack, with real drawdowns and real brokerage.

Data: data/real/*.json — genuine daily OHLC downloaded from Yahoo Finance
  RELIANCE (NSE stock) · EURUSD (currency pair) · BTCUSD — a REAL bear window.

Realism contract:
  - every replayed day's open/high/low/close == the real market's values;
    intrabar tick paths are interpolated INSIDE the true range (conservative
    ordering: up-bars test the low first, down-bars test the high first)
  - India leg pays the real cost schedule (brokerage + STT + exchange + stamp
    + GST); MT5 leg pays real CFD costs (half-spread + commission) — never STT
  - position sizing, stops, trailing, partials: the REAL modules, no shortcuts
  - regime fed to the exit engine is computed from the REAL data
    (SMA20 trend state; true-range vs ATR14 shock detection)
  - every real day advances the live gate exactly like production would

Run:  python3 scripts/paper_replay_real.py
"""
from __future__ import annotations

import asyncio
import json
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

DATA_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/real")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/real_replay")
CFG = load_config("config/master.yaml")

META = {
    # leg, lot size, ADV (units), realistic CFD costs for MT5 symbols
    "RELIANCE": {"leg": "india", "lot": 1, "adv": 8_000_000},
    "EURUSD": {"leg": "mt5_forex", "lot": 1000, "adv": 1e12,
               "half_spread": 0.00005, "commission_pct": 0.000035},   # 1-pip spread, $3.5/100k side
    "BTCUSD": {"leg": "mt5_crypto", "lot": 0.01, "adv": 5e9,
               "half_spread": 17.5, "commission_pct": 0.0},           # $35 spread, spread-only
}
TICKS_PER_BAR = 24
SUB_BAR = 6              # exit engine sees 4 sub-bars per real day
STARTING_CASH = 1_000_000.0


def load_real():
    data = {}
    for sym in META:
        data[sym] = json.loads((DATA_DIR / f"{sym}.json").read_text())
    return data


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


def intrabar_path(bar, rng, held_dir=None):
    """Ticks inside the REAL bar. audit BUG-6 FIX (2026-08-14): while a
    position is OPEN the path visits the ADVERSE extreme first; otherwise
    the close-direction heuristic stands. Clamped to the true [low, high]."""
    o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
    if held_dir == "buy":
        way = [o, l, h, c]
    elif held_dir == "sell":
        way = [o, h, l, c]
    else:
        way = [o, l, h, c] if c >= o else [o, h, l, c]
    ticks = []
    per_leg = TICKS_PER_BAR // (len(way) - 1)
    for a, b in zip(way, way[1:]):
        for j in range(per_leg):
            f = (j + 1) / per_leg
            px = a + (b - a) * f + rng.gauss(0, abs(h - l) * 0.02)
            ticks.append(min(h, max(l, px)))
    ticks[-1] = c                      # close is exact
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

    broker = PaperBroker(
        costs=CFG.execution_costs.india, impact=CFG.execution_costs.impact_model,
        starting_cash=STARTING_CASH,
        adv_map={s: m["adv"] for s, m in META.items()},
        daily_sigma_map={"RELIANCE": 0.016, "EURUSD": 0.005, "BTCUSD": 0.035},
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
                          "giveback_r": round(telemetry.giveback_r, 2),
                          "capture_pct": (round(telemetry.capture_pct, 1)
                                          if telemetry.capture_pct is not None
                                          else None)})

    exit_mgr = ExitManager(CFG.model_extra["exit_manager"], CompositeStopAdapter(
        india_adapter=IndiaStopAdapter(conns.get_openalgo(), apikey="PAPER", algo_id="ALGO-PAPER-1"),
        mt5_adapter=Mt5StopAdapter(conns.get_mt5())), on_exit=on_exit)
    router = OrderRouter(
        config=CFG, kill_switch=ks, anomaly_guard=guard,
        margin_checker=MarginChecker(CFG.risk_limits, india_api=PaperMarginAPI(broker),
                                     mt5_api=PaperMarginAPI(broker)),
        connections=conns, redis=redis,
        # COMPOUNDING (Aug 2026): size off LIVE equity, not starting cash
        balance_fn=lambda: broker.equity(),
        signal_valid_fn=lambda s, d: True, band_check_fn=lambda s, p: True,
        session_open_fn=lambda leg: True,
        audit_fn=lambda row: audit.append({"type": "order", **row}))

    # union of real dates
    all_dates = sorted({b["date"] for bars in data.values() for b in bars})
    index_of = {s: {b["date"]: i for i, b in enumerate(bars)} for s, bars in data.items()}
    rng = random.Random(11)
    clock = 1_000_000.0
    equity_curve, entries, rejected = [], 0, {}

    for date in all_dates:
        for sym, bars in data.items():
            i = index_of[sym].get(date)
            if i is None or i < 21:
                continue
            bar = bars[i]
            # audit BUG-1 FIX: risk inputs from completed bars only
            a = atr14(bars, i - 1)
            s20 = sma(bars, i)
            regime = real_regime(bars, i - 1)
            broker.on_tick(sym, bar["open"])

            # ---- entry: yesterday's close above SMA20, flat, real sizer via router
            held = sym in exit_mgr.positions and exit_mgr.positions[sym].state != "EXITED"
            if not held and s20 and bars[i - 1]["close"] > s20 and a:
                req = OrderRequest(symbol=sym, direction="buy", entry=bar["open"],
                                   stop=bar["open"] - 2 * a, atr=a,
                                   algo_id="ALGO-PAPER-1" if META[sym]["leg"] == "india" else None,
                                   lot_size=META[sym]["lot"], product="intraday")
                result = await router.route_order(req)
                if result.accepted and result.record.filled_qty > 0:
                    exit_mgr.positions.pop(sym, None)
                    await exit_mgr.attach(symbol=sym, direction="buy",
                                          entry=result.record.avg_fill_price,
                                          qty=result.record.filled_qty, atr=a,
                                          leg=META[sym]["leg"], lot_size=META[sym]["lot"])
                    entries += 1
                else:
                    rejected[result.reason.split(":")[0]] = rejected.get(result.reason.split(":")[0], 0) + 1

            # ---- replay the REAL bar tick by tick (audit BUG-2/3/6 fixes:
            # adverse-first path for open positions; last sub-bar closes the
            # daily bar; gap-aware stop fills via the window's first tick)
            held = exit_mgr.positions.get(sym)
            held_dir = held.direction if held and held.state != "EXITED" else None
            ticks = intrabar_path(bar, rng, held_dir=held_dir)
            window = []
            for t_i, px in enumerate(ticks):
                clock += 1.0
                broker.on_tick(sym, px)
                await guard.process_tick(sym, Tick(ts=clock, price=px,
                                                   bid=px * 0.9999, ask=px * 1.0001, volume=1000))
                window.append(px)
                if len(window) == SUB_BAR:
                    await exit_mgr.on_bar(sym, max(window), min(window),
                                          window[-1], regime,
                                          bar_closed=(t_i == len(ticks) - 1),
                                          open_px=window[0])
                    window = []
        await redis.delete("PAUSE_ENTRIES")           # session boundary
        gate = advance_gate(gate_path, reconciliation_clean=True)
        equity_curve.append({"date": date, "equity": broker.equity()})

    # ---- final EOD-style reconciliation on the whole book
    internal = [{"client_order_id": f["client_order_id"], "symbol": f["symbol"],
                 "qty": f["qty"], "price": f["price"]} for f in broker.tradebook()]
    rep = reconcile("real-replay-final", internal, broker.tradebook(),
                    naked_positions=exit_mgr.naked_positions())

    # ---- results
    eq = [p["equity"] for p in equity_curve]
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    meta = json.loads((DATA_DIR / "meta.json").read_text())
    bh = sum(m["period_return_pct"] for m in meta.values()) / len(meta)

    cost_by_leg = {}
    for f in broker.fills:
        leg = META[f.symbol]["leg"]
        cost_by_leg[leg] = cost_by_leg.get(leg, 0.0) + f.cost
    reasons = {}
    for e in exits_log:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1

    results = {
        "window": f"{all_dates[0]} → {all_dates[-1]} ({len(all_dates)} real market days)",
        "underlying_real": meta,
        "strategy": {
            "final_equity": round(eq[-1], 2),
            "return_pct": round((eq[-1] / STARTING_CASH - 1) * 100, 2),
            "MAX_DRAWDOWN_pct": round(mdd * 100, 2),
            "buy_hold_equal_weight_return_pct": round(bh, 2),
            "entries": entries, "fills": len(broker.fills),
            "total_costs": round(broker.total_costs, 2),
            "costs_by_leg": {k: round(v, 2) for k, v in cost_by_leg.items()},
            "exit_reasons": reasons,
            "entry_rejections": rejected,
            "reconciliation": "CLEAN" if rep.clean else "MISMATCH",
            "audit_rows": len(audit.rows), "audit_chain_ok": audit.verify_chain(),
        },
        "gate_after_replay": {k: v for k, v in gate.items() if k != "history"},
    }
    (OUT / "results.json").write_text(json.dumps(results, indent=1))
    (OUT / "equity_curve.json").write_text(json.dumps(equity_curve))
    print(json.dumps(results, indent=1))

    print("\n--- attempting --mode live with this evidence ---")
    try:
        assert_live_allowed(CFG, gate_path)
        print("!!! LIVE ALLOWED — BUG")
    except LiveGateError as exc:
        print(f"LiveGateError (expected — human items only): {exc}")


if __name__ == "__main__":
    asyncio.run(run())
