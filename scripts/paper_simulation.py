#!/usr/bin/env python3
"""Multi-day paper-trading simulation — the full integrated stack, replayed.

Runs N simulated market days through the REAL components (no test mocks of our
own code): OrderRouter -> PaperBroker (verified schemas) -> ExitManager with
broker-resident stops -> AnomalyGuard on every tick -> EOD reconciliation ->
daily report -> gate_state.json progression toward the 14-day live gate.

Drills included (evidence the machinery bites):
  Day 3: KILL-SWITCH DRILL — mid-session kill_all flattens the paper book,
         router provably rejects while halted, phrase unlock resumes.
  Day 4: RECONCILIATION DRILL — one phantom internal row injected;
         the day must NOT count and the clean streak must reset.
  Day 5: CRASH DAY — sharp drop; anomaly guard pauses entries, broker-side
         stops do the protecting.

Run:  python3 scripts/paper_simulation.py
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

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
from src.ops.paper_report import advance_gate, generate_daily_report
from src.ops.paper_server import create_paper_server
from src.ops.persistence import JsonlAuditLog
from src.app import LiveGateError, assert_live_allowed

OUT = Path("data/paper_sim")
CFG = load_config("config/master.yaml")

SYMBOLS = {
    "RELIANCE": {"px": 2500.0, "atr": 30.0, "lot": 1, "leg": "india"},
    "TCS": {"px": 4100.0, "atr": 45.0, "lot": 1, "leg": "india"},
    "BTCUSD": {"px": 60000.0, "atr": 900.0, "lot": 0.01, "leg": "mt5_crypto"},
}
DAY_PLANS = [  # (label, drift/tick, regime)
    ("Day 1  trend-up", +0.00050, "STRONG_TREND"),
    ("Day 2  chop", 0.00000, "RANGE"),
    ("Day 3  trend-up + KILL DRILL", +0.00045, "STRONG_TREND"),
    ("Day 4  chop + RECON DRILL", 0.00000, "RANGE"),
    ("Day 5  CRASH", -0.00020, "RANGE"),
    ("Day 6  recovery trend", +0.00060, "STRONG_TREND"),
]
TICKS_PER_DAY = 150
BAR_EVERY = 10


class MemRedis:
    """TTL-honoring in-memory Redis (real Redis expires PAUSE_ENTRIES; so must the sim)."""

    def __init__(self):
        import time as _t
        self._t = _t
        self.store = {}
        self._exp = {}

    async def get(self, k):
        exp = self._exp.get(k)
        if exp is not None and self._t.time() > exp:
            self.store.pop(k, None)
            self._exp.pop(k, None)
        return self.store.get(k)

    async def set(self, k, v):
        self.store[k] = v
        self._exp.pop(k, None)

    async def setex(self, k, ttl, v):
        self.store[k] = v
        self._exp[k] = self._t.time() + ttl

    async def delete(self, k):
        self.store.pop(k, None)
        self._exp.pop(k, None)


class PaperKillAdapter:
    """Kill switch leg over the ACTUAL paper broker — the drill really flattens."""

    def __init__(self, broker: PaperBroker):
        self.broker = broker

    async def get_open_orders(self):
        return [{"id": o["orderid"]} for o in self.broker.orderbook()]

    async def get_open_positions(self):
        return [{"id": p["symbol"], "qty": p["qty"]} for p in self.broker.positionbook()]

    async def cancel_order(self, oid):
        result = self.broker.cancel_order(oid)
        if result.get("status") != "success":
            raise RuntimeError(result.get("message"))

    async def close_position_market(self, symbol):
        pos = next(p for p in self.broker.positionbook() if p["symbol"] == symbol)
        action = "SELL" if pos["qty"] > 0 else "BUY"
        self.broker.place_order({"symbol": symbol, "action": action,
                                 "quantity": abs(pos["qty"]), "pricetype": "MARKET",
                                 "product": "MIS"})


class PaperMarginAPI:
    def __init__(self, broker):
        self.broker = broker

    async def available_margin(self):
        return self.broker.available_margin()

    async def required_margin(self, s, q, p, prod):
        return q * p

    async def free_margin(self):
        return self.broker.available_margin()

    async def equity(self):
        return self.broker.equity()

    async def margin_required(self, s, lots):
        return lots * self.broker.last_price.get(s, 0.0)


class Connections:
    def __init__(self, app):
        self._c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://paper")

    def get_openalgo(self):
        return self._c

    def get_mt5(self):
        return self._c


async def run():
    OUT.mkdir(parents=True, exist_ok=True)
    gate_path = OUT / "gate_state.json"
    gate_path.unlink(missing_ok=True)

    broker = PaperBroker(costs=CFG.execution_costs.india,
                         impact=CFG.execution_costs.impact_model,
                         starting_cash=1_000_000.0,
                         adv_map={s: 5_000_000 for s in SYMBOLS},
                         daily_sigma_map={s: 0.015 for s in SYMBOLS})
    app = create_paper_server(broker)
    conns = Connections(app)
    redis = MemRedis()
    redis.store[VAR_CACHE_KEY] = "0.005"
    audit = JsonlAuditLog(OUT / "audit.jsonl")
    ks = KillSwitch(redis=redis, brokers={"paper": PaperKillAdapter(broker)},
                    sentinel_path=OUT / "halt.sentinel",
                    unlock_phrase="RESUME PAPER TRADING",
                    auto_trigger_daily_loss_pct=0.03, auto_trigger_var_breach=True,
                    max_var_daily=CFG.risk_limits.max_var_daily,
                    audit_fn=lambda r: audit.append({"type": "kill", **r}))
    (OUT / "halt.sentinel").unlink(missing_ok=True)
    guard = AnomalyGuard(redis=redis,
                         velocity_sigma={"s1": 6, "s5": 5, "s30": 4},
                         spread_blowout_mult=3.0, volume_spike_mult=5.0,
                         cooloff_minutes=15)
    for sym, meta in SYMBOLS.items():
        guard.prime(sym, sigma_1s=0.0005, sigma_5s=0.0011, sigma_30s=0.0028,
                    median_spread=meta["px"] * 0.0002, volume_30s_baseline=50_000)
    exit_mgr = ExitManager(CFG.model_extra["exit_manager"], CompositeStopAdapter(
        india_adapter=IndiaStopAdapter(conns.get_openalgo(), apikey="PAPER",
                                       algo_id="ALGO-PAPER-1"),
        mt5_adapter=Mt5StopAdapter(conns.get_mt5())))
    router = OrderRouter(
        config=CFG, kill_switch=ks, anomaly_guard=guard,
        margin_checker=MarginChecker(CFG.risk_limits, india_api=PaperMarginAPI(broker),
                                     mt5_api=PaperMarginAPI(broker)),
        connections=conns, redis=redis, balance_fn=lambda: 1_000_000.0,
        signal_valid_fn=lambda s, d: True, band_check_fn=lambda s, p: True,
        session_open_fn=lambda leg: True,
        audit_fn=lambda row: audit.append({"type": "order", **row}))

    rng = random.Random(7)
    prices = {s: m["px"] for s, m in SYMBOLS.items()}
    summary = []
    ticks_clock = 1_000_000.0

    for day_idx, (label, drift, regime_name) in enumerate(DAY_PLANS, start=1):
        regime = {"trend_state": regime_name,
                  "vol_regime": "NORMAL"}
        day_events = []
        fills_before = len(broker.fills)
        exit_mgr.positions = {k: v for k, v in exit_mgr.positions.items()
                              if v.state != "EXITED"}

        # -- morning entries (one per flat symbol; sim strategy = plumbing driver)
        for sym, meta in SYMBOLS.items():
            broker.on_tick(sym, prices[sym])
            held = any(p["symbol"] == sym for p in broker.positionbook())
            if held or sym in exit_mgr.positions:
                continue
            req = OrderRequest(symbol=sym, direction="buy", entry=prices[sym],
                               stop=prices[sym] - 2 * meta["atr"], atr=meta["atr"],
                               algo_id="ALGO-PAPER-1" if meta["leg"] == "india" else None,
                               lot_size=meta["lot"], product="intraday")
            result = await router.route_order(req)
            if result.accepted and result.record.filled_qty > 0:
                await exit_mgr.attach(symbol=sym, direction="buy",
                                      entry=result.record.avg_fill_price,
                                      qty=result.record.filled_qty, atr=meta["atr"],
                                      leg=meta["leg"], lot_size=meta["lot"])
                day_events.append(f"ENTRY {sym} x{result.record.filled_qty} @ {result.record.avg_fill_price:.2f}")
            else:
                day_events.append(f"entry {sym} rejected: {result.reason}")

        # -- intraday ticks
        window = {s: [] for s in SYMBOLS}
        for i in range(TICKS_PER_DAY):
            ticks_clock += 1.0
            for sym, meta in SYMBOLS.items():
                crash_kick = 0.0
                if "CRASH" in label and 60 <= i < 70:
                    crash_kick = -0.004                       # ~-4% over 10 ticks
                px = prices[sym] * (1 + drift + crash_kick + rng.gauss(0, 0.0006))
                prices[sym] = px
                triggered = broker.on_tick(sym, px)
                for t in triggered:
                    day_events.append(f"BROKER STOP FIRED {sym} {t.action} {t.qty} @ {t.price:.2f}")
                shocks = await guard.process_tick(sym, Tick(
                    ts=ticks_clock, price=px, bid=px * 0.9999, ask=px * 1.0001,
                    volume=1_000))
                for s in shocks:
                    day_events.append(f"ANOMALY {sym}: {s.trigger} → entries paused")
                window[sym].append(px)
                if len(window[sym]) == BAR_EVERY:
                    bar = window[sym]
                    day_regime = dict(regime)
                    if "CRASH" in label and i >= 60:
                        day_regime["vol_regime"] = "SHOCK"
                    actions = await exit_mgr.on_bar(sym, max(bar), min(bar), bar[-1],
                                                    day_regime)
                    for a in actions:
                        day_events.append(f"EXIT-ENGINE {sym}: {a}")
                    window[sym] = []

            # -- day 3 kill drill at midday
            if "KILL DRILL" in label and i == 75:
                report = await ks.kill_all("scheduled paper kill drill")
                blocked = await router.route_order(OrderRequest(
                    symbol="RELIANCE", direction="buy", entry=prices["RELIANCE"],
                    stop=prices["RELIANCE"] - 60, atr=30.0, algo_id="ALGO-PAPER-1"))
                assert not blocked.accepted and blocked.reason == "trading_halted"
                await ks.unlock("RESUME PAPER TRADING")
                exit_mgr.positions.clear()                     # book flattened by drill
                day_events.append(
                    f"KILL DRILL: cancelled {len(report.orders_cancelled)} stops, "
                    f"flattened {len(report.positions_closed)} positions, router "
                    f"REJECTED while halted, unlocked OK")

        # -- EOD: overnight reset of the intraday shock pause (session boundary)
        await redis.delete("PAUSE_ENTRIES")

        # -- EOD reconciliation
        internal = [{"client_order_id": f["client_order_id"], "symbol": f["symbol"],
                     "qty": f["qty"], "price": f["price"]} for f in broker.tradebook()]
        if "RECON DRILL" in label:
            internal.append({"client_order_id": "PHANTOM-1", "symbol": "GHOST",
                             "qty": 99, "price": 1.0})
            day_events.append("RECON DRILL: phantom internal row injected")
        rep = reconcile(f"sim-day-{day_idx}", internal, broker.tradebook(),
                        naked_positions=exit_mgr.naked_positions())
        gate = advance_gate(gate_path, reconciliation_clean=rep.clean,
                            sebi_checks_passed=False)

        # -- daily report file
        state = {"cash": broker.cash, "equity": broker.equity(),
                 "total_costs": broker.total_costs,
                 "positions": broker.positionbook(), "resting": broker.orderbook()}
        fills_today = broker.tradebook()[fills_before:]
        report_md = generate_daily_report(f"sim-day-{day_idx} ({label})", state,
                                          fills_today, rep.clean, len(audit.rows))
        (OUT / f"report_day{day_idx}.md").write_text(
            report_md + "\n\n## Events\n" + "\n".join(f"- {e}" for e in day_events))

        summary.append({
            "day": label, "equity": round(broker.equity(), 2),
            "fills_today": len(fills_today), "costs_cum": round(broker.total_costs, 2),
            "open_pos": len(broker.positionbook()), "resting_stops": len(broker.orderbook()),
            "recon": "CLEAN" if rep.clean else "MISMATCH (does NOT count)",
            "gate_days": gate["paper_days_completed"],
            "streak": gate["clean_reconciliation_streak"],
            "events": len(day_events),
        })
        print(f"{label:34s} equity={summary[-1]['equity']:>12,.2f}  "
              f"fills={summary[-1]['fills_today']:>2}  recon={summary[-1]['recon']:<26s} "
              f"gate_days={gate['paper_days_completed']:>2}  streak={gate['clean_reconciliation_streak']}")

    # -- attempt LIVE start (must refuse)
    print("\n--- attempting `--mode live` with this evidence ---")
    try:
        assert_live_allowed(CFG, gate_path)
        print("!!! LIVE ALLOWED — THIS WOULD BE A BUG")
    except LiveGateError as exc:
        print(f"LiveGateError (correct): {exc}")

    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))
    print(f"\naudit chain intact: {audit.verify_chain()}  rows={len(audit.rows)}")
    print(f"gate_state.json: {gate_path.read_text()[:400]}")
    return summary


if __name__ == "__main__":
    asyncio.run(run())
