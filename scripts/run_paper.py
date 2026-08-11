#!/usr/bin/env python3
"""RUN THE PAPER COCKPIT — one command, the real product (Aug 2026).

    python3 scripts/run_paper.py            # http://127.0.0.1:8080/ui

This is the assembly `--mode paper` always promised: the REAL runtime
(router → guards → sizer → margin → paper broker with real cost schedules →
ExitManager → kill switch → hash-chained audit) + the MODULE 44 gateway +
the v2 cockpit, fed by MODULE 62's replay-of-real-history quote feed
(session-aware: india freezes outside NSE hours — MODULE 58).

Tokens (RBAC): set COCKPIT_OPERATOR_TOKEN / COCKPIT_VIEWER_TOKEN env vars;
dev defaults are generated and PRINTED at boot when unset.

What is paper vs real here:
  prices    real bundled OHLC (Feb–Aug 2026), replayed tick-by-tick
  costs     real india cost schedule + real MT5 CFD costs
  engine    the same code path live trading will use
  broker    the in-process paper broker (no external orders, ever)
On the VPS, swap ReplayQuoteFeed for the OpenAlgo/MT5 quote adapters and the
paper server for real connections — same interfaces (DEPLOY.md §4).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from src.core.config_loader import load_config
from src.core.order_router import VAR_CACHE_KEY, OrderRequest
from src.core.paper_broker import PaperBroker
from src.ops.broker_settings import BrokerSettings
from src.ops.cockpit_gateway import create_gateway
from src.ops.market_clock import MarketClock
from src.ops.paper_server import create_paper_server
from src.ops.quote_feed import ReplayQuoteFeed
from src.ops.research_lab import ResearchLab
from src.ops.strategy_engine import StrategyEngine
from src.runtime import build_runtime

ROOT = Path(__file__).resolve().parent.parent
DATA_DIRS = ["data/market_india_6m", "data/market_forex_6m", "data/market_crypto_6m"]
FEED_INTERVAL_S = float(os.environ.get("FEED_INTERVAL_S", "2.0"))
DEFAULT_SIGMA = {"india": 0.015, "mt5_forex": 0.007, "mt5_crypto": 0.04}


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
        self._c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://paper")
    def get_openalgo(self): return self._c
    def get_mt5(self): return self._c


def load_universe():
    """symbol -> {leg, lot, adv, dir, file} from every bundled dataset."""
    uni = {}
    for d in DATA_DIRS:
        spec = json.loads((ROOT / d / "symbols.json").read_text())
        for sym, meta in spec["symbols"].items():
            uni[sym] = {**meta, "dir": str(ROOT / d), "file": f"{sym}.json"}
    return uni


def r_now(pos, px: float) -> float:
    risk = abs(pos.entry - pos.stop) or 1e-9
    sign = 1.0 if pos.direction == "buy" else -1.0
    return sign * (px - pos.entry) / risk


async def assemble(data_dir: Path):
    cfg = load_config(str(ROOT / "config/master.yaml"))
    uni = load_universe()
    clock = MarketClock((cfg.model_extra or {}).get("trading_hours"))
    feed = ReplayQuoteFeed(
        {s: (m["dir"], m["file"]) for s, m in uni.items()},
        market_clock=clock, symbol_legs={s: m["leg"] for s, m in uni.items()})

    broker = PaperBroker(
        costs=cfg.execution_costs.india, impact=cfg.execution_costs.impact_model,
        starting_cash=float(os.environ.get("STARTING_CASH", "1000000")),
        adv_map={s: m.get("adv", 1e7) for s, m in uni.items()},
        daily_sigma_map={s: DEFAULT_SIGMA.get(m["leg"], 0.02) for s, m in uni.items()},
        mt5_cost_map={s: {"half_spread": m["half_spread"],
                          "commission_pct": m["commission_pct"]}
                      for s, m in uni.items() if "half_spread" in m})
    conns = Connections(create_paper_server(broker))
    redis = MemRedis()
    redis.store[VAR_CACHE_KEY] = "0.005"

    async def balance():
        return broker.equity()

    runtime = await build_runtime(
        cfg, mode="paper", redis=redis, connections=conns, kill_brokers={},
        india_margin_api=PaperMarginAPI(broker), mt5_margin_api=PaperMarginAPI(broker),
        balance_fn=balance, data_dir=data_dir,
        gate_path=data_dir / "gate_state.json",
        india_apikey="PAPER", algo_id="ALGO-PAPER-1")

    events: list = []
    closed: list = []
    day_start = {"equity": broker.equity(), "date": None}

    def note(msg: str) -> None:
        events.insert(0, {"t": dt.datetime.now().strftime("%H:%M:%S"), "m": msg})
        del events[40:]

    # MODULE 65 — auto-trading sleeves. Every sleeve boots DISABLED; the
    # operator enables them from the cockpit Strategies page (audited).
    engine = StrategyEngine(router=runtime.router, exit_mgr=runtime.exit_mgr,
                            feed=feed, universe=uni, note_fn=note)

    async def on_exit_cb(sym, telemetry):
        sleeve = engine.sleeve_for(sym) or "manual"
        closed.insert(0, {
            "date": dt.date.today().isoformat(), "symbol": sym,
            "leg": uni.get(sym, {}).get("leg", ""),
            "direction": runtime.exit_mgr.positions.get(sym).direction
            if runtime.exit_mgr.positions.get(sym) else "",
            "realized_r": round(telemetry.realized_r, 2),
            "reason": telemetry.exit_reason, "exit_reason": telemetry.exit_reason,
            "mfe_captured_pct": round(telemetry.mfe_captured_pct, 1),
            "sleeve": sleeve})
        engine.record_exit(sym, telemetry.realized_r)
        note(f"exit {sym}: {telemetry.exit_reason} "
             f"({telemetry.realized_r:+.2f}R)")
    runtime.exit_mgr.on_exit = on_exit_cb

    # ---------------- feed loop ----------------

    async def feed_loop():
        while True:
            now = dt.datetime.now(dt.timezone.utc)
            today = dt.date.today().isoformat()
            if day_start["date"] != today:
                day_start.update(date=today, equity=broker.equity())
            for sym, px in feed.tick_once(now).items():
                broker.on_tick(sym, px)
                pos = runtime.exit_mgr.positions.get(sym)
                if pos is not None and pos.state != "EXITED":
                    k = feed.candles(sym, 1)
                    if k:
                        await runtime.exit_mgr.on_bar(
                            sym, high=k[-1]["h"], low=k[-1]["l"],
                            close=k[-1]["c"], regime={})
            # MODULE 65: enabled sleeves evaluate on completed bars — same
            # router door as manual tickets, nothing fires while disabled
            await engine.on_tick()
            await asyncio.sleep(FEED_INTERVAL_S)

    # ---------------- gateway providers ----------------

    async def snapshot():
        rows = []
        for sym, pos in runtime.exit_mgr.positions.items():
            if pos.state == "EXITED":
                continue
            px = feed.last_price(sym) or pos.entry
            rows.append({"symbol": sym, "leg": pos.leg, "qty": pos.remaining_qty,
                         "entry": round(pos.entry, 4), "stop": round(pos.stop, 4),
                         "r_now": round(r_now(pos, px), 2), "state": pos.state,
                         "mfe_r": round(r_now(pos, pos.extreme), 2),
                         "unrealized": round((px - pos.entry) * pos.remaining_qty
                                             * (1 if pos.direction == "buy" else -1), 2)})
        gate = {}
        gp = data_dir / "gate_state.json"
        if gp.exists():
            try:
                gate = json.loads(gp.read_text())
            except json.JSONDecodeError:
                gate = {}
        var_raw = await redis.get(VAR_CACHE_KEY)
        return {"mode": "paper", "equity": broker.equity(),
                "pnl": broker.equity() - day_start["equity"],
                "costs": broker.total_costs,
                "var95": float(var_raw or 0.0),
                "positions": rows, "events": list(events),
                "workers": {"quote_feed": True, "exit_manager": True,
                            "order_router": True, "kill_switch": True,
                            "strategy_engine": any(
                                s["enabled"] for s in engine.sleeves.values())},
                "gate": gate,
                "feed": feed.status()}

    async def close_position(symbol: str, reason: str):
        px = feed.last_price(symbol)
        if px is None:
            raise KeyError(f"no price for {symbol!r}")
        result = await runtime.exit_mgr.manual_exit(symbol, px, "manual_close")
        return result

    async def place_order(ticket: dict, actor: str):
        sym = ticket["symbol"]
        meta = uni.get(sym)
        if meta is None:
            return {"accepted": False,
                    "reason": f"unknown_symbol: {sym!r} not in the paper universe "
                              f"({', '.join(sorted(uni))})"}
        px = feed.last_price(sym)
        if px is None:
            return {"accepted": False, "reason": "no_price_yet"}
        # prime the paper book at the current feed mark — the feed loop does
        # this continuously; doing it here too makes the first ticket after
        # boot deterministic instead of racing the first loop tick
        broker.on_tick(sym, px)
        atr = feed.atr_proxy(sym) or abs(px - float(ticket["stop"])) or px * 0.01
        req = OrderRequest(symbol=sym, direction=ticket["direction"], entry=px,
                           stop=float(ticket["stop"]), atr=atr,
                           algo_id="ALGO-PAPER-1" if meta["leg"] == "india" else None,
                           lot_size=meta.get("lot", 1.0), product="intraday")
        result = await runtime.router.route_order(req)
        if result.accepted and result.record.filled_qty > 0:
            runtime.exit_mgr.positions.pop(sym, None)
            await runtime.exit_mgr.attach(
                symbol=sym, direction=ticket["direction"],
                entry=result.record.avg_fill_price, qty=result.record.filled_qty,
                atr=atr, leg=meta["leg"], lot_size=meta.get("lot", 1.0))
            note(f"ENTRY {sym} {ticket['direction']} "
                 f"x{result.record.filled_qty} @ {result.record.avg_fill_price}")
            return {"accepted": True, "qty": result.record.filled_qty,
                    "avg_fill_price": result.record.avg_fill_price}
        return {"accepted": False, "reason": result.reason, "checks": result.checks}

    settings = BrokerSettings(cfg, overlay_path=ROOT / "config/brokers_local.yaml")
    lab = ResearchLab(ROOT, out_root=ROOT / "data/research_runs")

    tokens = {}
    op = os.environ.get("COCKPIT_OPERATOR_TOKEN") or f"op-{secrets.token_hex(8)}"
    vw = os.environ.get("COCKPIT_VIEWER_TOKEN") or f"vw-{secrets.token_hex(8)}"
    tokens[op], tokens[vw] = "operator", "viewer"

    app = create_gateway(
        tokens=tokens, kill_switch=runtime.kill_switch, audit_log=runtime.audit,
        snapshot_fn=snapshot,
        pause_entries_fn=lambda reason: redis.set("pause_entries", reason),
        resume_entries_fn=lambda actor: redis.delete("pause_entries"),
        trades_fn=lambda: list(closed[:50]),
        history_fn=lambda: list(closed),
        pnl_history_fn=lambda: [],
        config_view_fn=lambda: {"risk_limits": dict(cfg.risk_limits.__dict__)
                                if hasattr(cfg.risk_limits, "__dict__")
                                else {}, "mode": "paper"},
        ui_dir=str(ROOT / "cockpit/web"),
        market_clock=runtime.market_clock or clock,
        brokers_status_fn=settings.status, broker_test_fn=settings.test,
        broker_save_fn=settings.save,
        candles_fn=lambda symbol, n: feed.candles(symbol, n),
        close_position_fn=close_position, place_order_fn=place_order,
        research_lab=lab, strategy_engine=engine)

    # test/introspection handles (FastAPI's designed extension point)
    app.state.engine = engine
    app.state.feed = feed
    app.state.runtime = runtime
    app.state.broker = broker

    return app, feed_loop, (op, vw)


def main() -> int:
    try:
        import uvicorn
    except ImportError:
        print("run_paper needs uvicorn:  python3 -m pip install uvicorn")
        return 1
    data_dir = ROOT / "data/runtime"
    data_dir.mkdir(parents=True, exist_ok=True)

    async def serve():
        app, feed_loop, (op, vw) = await assemble(data_dir)
        print("=" * 64)
        print("Trading OS — PAPER cockpit")
        print(f"  UI        http://127.0.0.1:{os.environ.get('PORT', 8080)}/ui")
        print(f"  operator  {op}")
        print(f"  viewer    {vw}")
        print("  feed      replay of real bundled OHLC (session-aware)")
        print("=" * 64)
        asyncio.get_event_loop().create_task(feed_loop())
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=int(os.environ.get("PORT", "8080")),
            log_level="warning"))
        await server.serve()

    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
