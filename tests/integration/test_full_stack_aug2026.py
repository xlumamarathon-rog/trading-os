"""COMBINED integration tests (Aug 2026) — every new component working
TOGETHER through the real order path, not in unit isolation:

  UniverseManager → corporate-actions-adjusted bars → strategy engine signal
  → guard stack (Budget + SessionGuard + Heat) through the ROUTER's
  portfolio_guard_fn hook → PaperBroker fills → ExitManager lifecycle fed by
  the BarAggregator → TCA records → ShadowRunner parity → gateway panels.

Prior to this file, none of MODULES 46-55 had ever met in one test."""
from pathlib import Path

import httpx
import pytest

from src.core.budget_manager import BudgetManager
from src.core.config_loader import load_config
from src.core.guard_stack import make_portfolio_guard
from src.core.kill_switch import KillSwitch
from src.core.margin_checker import MarginChecker
from src.core.order_router import VAR_CACHE_KEY, OrderRequest, OrderRouter
from src.core.paper_broker import PaperBroker
from src.data.bar_aggregator import BarAggregator
from src.data.corporate_actions import CorporateAction, adjust_bars
from src.data.universe_manager import Instrument, UniverseManager
from src.exits.adapters.composite import CompositeStopAdapter
from src.exits.adapters.india_stops import IndiaStopAdapter
from src.exits.adapters.mt5_stops import Mt5StopAdapter
from src.exits.exit_manager import ExitManager
from src.ops.paper_server import create_paper_server
from src.ops.session_guard import SessionGuard
from src.ops.shadow_runner import ShadowRunner, diff_decisions
from src.ops.tca_monitor import TcaMonitor
from src.risk.portfolio_heat import PortfolioHeatManager
from src.strategies import get_signal

CFG = load_config("config/master.yaml")
FLAT_EXITS = {k: v for k, v in CFG.model_extra["exit_manager"].items()
              if not k.endswith("_by_leg")}
TREND = {"trend_state": "STRONG_TREND", "vol_regime": "NORMAL"}


class MemRedis:
    def __init__(self): self.store = {}
    async def get(self, k): return self.store.get(k)
    async def set(self, k, v): self.store[k] = v
    async def setex(self, k, ttl, v): self.store[k] = v
    async def delete(self, k): self.store.pop(k, None)


class PaperMarginAPI:
    def __init__(self, broker): self.broker = broker
    async def available_margin(self): return self.broker.available_margin()
    async def required_margin(self, s, q, p, prod): return q * p
    async def free_margin(self): return self.broker.available_margin()
    async def equity(self): return self.broker.equity()
    async def margin_required(self, s, lots): return lots * self.broker.last_price.get(s, 0.0)


def trending_bars(n=80, start=1000.0, step=6.0):
    """Rising series strong enough for tsmom_f (dev>1%, mom63>0)."""
    return [{"date": f"2026-{(k // 28) + 1:02d}-{(k % 28) + 1:02d}",
             "open": start + k * step, "high": start + k * step + 4,
             "low": start + k * step - 4, "close": start + k * step,
             "volume": 10_000} for k in range(n)]


class FullStack:
    """The whole system assembled the way production runtime would."""

    def __init__(self, tmp_path: Path, *, budget=None, session_guard=None,
                 heat_mgr=None, starting_cash=1_000_000.0):
        self.broker = PaperBroker(costs=CFG.execution_costs.india,
                                  impact=CFG.execution_costs.impact_model,
                                  starting_cash=starting_cash)
        app = create_paper_server(self.broker)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://paper")
        redis = MemRedis()
        redis.store[VAR_CACHE_KEY] = "0.005"
        self.exits_log = []

        async def on_exit(sym, tel):
            self.exits_log.append({"symbol": sym, "reason": tel.exit_reason,
                                   "realized_r": tel.realized_r})

        self.exit_mgr = ExitManager(FLAT_EXITS, CompositeStopAdapter(
            india_adapter=IndiaStopAdapter(self.client, apikey="P", algo_id="ALGO-1"),
            mt5_adapter=Mt5StopAdapter(self.client)), on_exit=on_exit)

        self.budget, self.session_guard, self.heat_mgr = budget, session_guard, heat_mgr
        guard_fn = make_portfolio_guard(
            equity_fn=self.broker.equity, risk_limits=CFG.risk_limits,
            budget=budget, session_guard=session_guard, heat_mgr=heat_mgr,
            positions_fn=lambda: self.exit_mgr.positions.values())

        class Conns:
            def __init__(s): s.c = self.client
            def get_openalgo(s): return s.c
            def get_mt5(s): return s.c

        def tradable():
            eq = self.broker.equity()
            return budget.effective(eq) if budget else eq

        ks = KillSwitch(redis=redis, brokers={},
                        sentinel_path=tmp_path / "halt.sentinel",
                        unlock_phrase="GO", auto_trigger_daily_loss_pct=0.03,
                        auto_trigger_var_breach=True, max_var_daily=0.02)

        class NoAnomaly:
            async def entries_paused(self, *a, **kw): return False

        self.router = OrderRouter(
            config=CFG, kill_switch=ks, anomaly_guard=NoAnomaly(),
            margin_checker=MarginChecker(CFG.risk_limits,
                                         india_api=PaperMarginAPI(self.broker),
                                         mt5_api=PaperMarginAPI(self.broker)),
            connections=Conns(), redis=redis, balance_fn=tradable,
            signal_valid_fn=lambda s, d: True, band_check_fn=lambda s, p: True,
            session_open_fn=lambda leg: True, audit_fn=lambda row: None,
            portfolio_guard_fn=guard_fn)

    async def enter(self, symbol, bars, i, atr=15.0):
        """Signal → guard stack → router → fill → ExitManager attach."""
        px = bars[i]["open"]
        self.broker.on_tick(symbol, px)
        direction = get_signal("tsmom_f")(bars, i, TREND)
        if direction is None:
            return None, None
        stop = px - 2 * atr if direction == "buy" else px + 2 * atr
        res = await self.router.route_order(OrderRequest(
            symbol=symbol, direction=direction, entry=px, stop=stop, atr=atr,
            algo_id="ALGO-1", lot_size=1, product="intraday"))
        if res.accepted and res.record.filled_qty > 0:
            await self.exit_mgr.attach(symbol=symbol, direction=direction,
                                       entry=res.record.avg_fill_price,
                                       qty=res.record.filled_qty, atr=atr,
                                       leg="india", lot_size=1)
        return direction, res


# ---------- 1. data layer feeds the strategy correctly ----------

def test_universe_and_corporate_actions_feed_strategy():
    uni = UniverseManager([
        Instrument("RELIANCE", "india", 1, 8_000_000),
        Instrument("ILLIQUID", "india", 1, 1_000),
    ])
    picked = uni.eligible(min_adv_notional=1e9,
                          price_of={"RELIANCE": 1300.0, "ILLIQUID": 1300.0}.get)
    assert picked == ["RELIANCE"]                    # screen feeds the pipeline

    # a 1:1 bonus mid-series: RAW data breaks the signal, ADJUSTED keeps it
    bars = trending_bars(80)
    for b in bars[40:]:
        for k in ("open", "high", "low", "close"):
            b[k] = b[k] / 2                           # post-bonus prices halve
    raw_signal = get_signal("tsmom_f")(bars, 79, TREND)
    adj, log = adjust_bars(bars, [CorporateAction("RELIANCE", bars[40]["date"],
                                                  "bonus", factor=2.0)])
    adj_signal = get_signal("tsmom_f")(adj, 79, TREND)
    assert log[0]["applied"]
    assert raw_signal != "buy"                       # phantom crash kills signal
    assert adj_signal == "buy"                       # adjusted series trends on


# ---------- 2. full entry chain through the ROUTER hook ----------

async def test_signal_guards_router_fill_exit_chain(tmp_path):
    budget = BudgetManager(200_000, min_floor_pct=0.5)
    sg = SessionGuard(profit_bank_pct=0.05, loss_stop_pct=0.05)
    heat = PortfolioHeatManager(max_heat_pct=0.06)
    fs = FullStack(tmp_path, budget=budget, session_guard=sg, heat_mgr=heat)
    budget.attach(fs.broker.equity())
    sg.start_session(fs.broker.equity())

    bars = trending_bars(80)
    direction, res = await fs.enter("RELIANCE", bars, 79)
    assert direction == "buy" and res.accepted
    assert res.checks["portfolio_guard"] == "ok"     # guard stack consulted

    # sizing consumed the BUDGET (2L), not the 10L account:
    qty = res.record.filled_qty
    risk_per_unit = res.checks["size_factors"]["risk_per_unit"]
    assert qty * risk_per_unit <= 200_000 * CFG.risk_limits.max_risk_per_trade_pct * 1.01

    # position is live in the exit engine with a broker-resident stop
    pos = fs.exit_mgr.positions["RELIANCE"]
    assert pos.state == "RISK_ON" and len(fs.broker.open_orders) == 1


# ---------- 3. guard precedence: each layer rejects through the router ----------

async def test_guard_layers_reject_in_order(tmp_path):
    bars = trending_bars(80)

    # (a) budget exhausted blocks FIRST
    b = BudgetManager(200_000)
    fs = FullStack(tmp_path / "a", budget=b)
    b.attach(fs.broker.equity())
    b.baseline_equity += 300_000                     # simulate 3L trading loss
    _, res = await fs.enter("RELIANCE", bars, 79)
    assert not res.accepted and "budget:budget_exhausted" in res.reason

    # (b) session guard blocks after the day is banked
    sg = SessionGuard(profit_bank_pct=0.01)
    fs2 = FullStack(tmp_path / "b", session_guard=sg)
    sg.start_session(1_000_000)
    assert not sg.allows_new_entries(1_020_000)      # +2% day → banked
    _, res2 = await fs2.enter("RELIANCE", bars, 79)
    assert not res2.accepted and "session_guard:day_tripped" in res2.reason

    # (c) heat cap blocks when the book is already loaded
    heat = PortfolioHeatManager(max_heat_pct=0.001)  # tiny cap: next order breaches
    fs3 = FullStack(tmp_path / "c", heat_mgr=heat)
    _, res3 = await fs3.enter("RELIANCE", bars, 79)
    assert not res3.accepted and res3.reason.startswith("portfolio_guard:heat:")


# ---------- 4. lifecycle: aggregator → exit engine → TCA → blotter data ----------

async def test_bar_aggregator_drives_exits_and_tca_records(tmp_path):
    fs = FullStack(tmp_path)
    bars = trending_bars(80)
    _, res = await fs.enter("RELIANCE", bars, 79, atr=15.0)
    assert res.accepted
    entry = res.record.avg_fill_price

    # TCA: model-estimated cost vs the broker's actual charged cost
    tca = TcaMonitor(min_samples=1, drift_alert_bps=1000)
    fill = fs.broker.fills[-1]
    rec = tca.record(symbol="RELIANCE", expected_cost=fill.cost * 0.9,
                     actual_cost=fill.cost, notional=fill.qty * fill.price)
    assert rec.drift_bps > 0 and len(tca.records) == 1

    # ticks → aggregator → sub-bars → exit engine reaches breakeven then trails
    agg = BarAggregator(interval_s=60)
    px, t = entry, 0.0
    for step in range(1, 241):                       # strong run-up: +48 ATR-ish
        t += 15.0
        px = entry + step * 0.5
        fs.broker.on_tick("RELIANCE", px)
        done = agg.on_tick(t, px, 100)
        if done:
            await fs.exit_mgr.on_bar("RELIANCE", done.high, done.low, done.close, TREND)
    pos = fs.exit_mgr.positions["RELIANCE"]
    assert pos.state in ("BREAKEVEN", "TRAILING")
    assert pos.stop > entry                          # profit locked by the trail
    assert pos.partials_taken                        # partial ladder fired
    # blotter rows exist for the gateway (partials booked as real orders)
    assert len(fs.broker.fills) >= 2


# ---------- 5. shadow parity against the live decision stream ----------

async def test_shadow_runner_parity_with_live_intents(tmp_path):
    fs = FullStack(tmp_path)
    bars = trending_bars(80)
    direction, res = await fs.enter("RELIANCE", bars, 79)
    live_intents = [{"date": bars[79]["date"], "symbol": "RELIANCE",
                     "direction": direction}]

    shadow = ShadowRunner(signal_fn=get_signal("tsmom_f"),
                          regime_fn=lambda b, i: TREND)
    await shadow.on_bar("RELIANCE", bars, 79)
    rep = diff_decisions(shadow.intents(), live_intents)
    assert rep.clean and rep.matched == 1            # same logic → same intents

    # divergence detection: a guard blocks live but not shadow
    blocked = ShadowRunner(signal_fn=get_signal("tsmom_f"),
                           regime_fn=lambda b, i: TREND)
    await blocked.on_bar("RELIANCE", bars, 79)
    rep2 = diff_decisions(blocked.intents(), [])     # live traded nothing
    assert not rep2.clean and rep2.missing_live


# ---------- 6. the gateway serves what the run produced ----------

async def test_gateway_panels_serve_run_artifacts(tmp_path):
    from src.ops.cockpit_gateway import create_gateway
    from src.ops.persistence import JsonlAuditLog

    fs = FullStack(tmp_path)
    bars = trending_bars(80)
    await fs.enter("RELIANCE", bars, 79)

    async def snapshot():
        return {"equity": fs.broker.equity()}

    async def trades_fn():
        return [{**e, "sleeve": "tsmom_f"} for e in fs.exits_log]

    async def pnl_fn():
        return [{"date": "2026-03-01", "equity": 1_000_000.0},
                {"date": "2026-03-02", "equity": fs.broker.equity()}]

    ks = KillSwitch(redis=MemRedis(), brokers={},
                    sentinel_path=tmp_path / "gw.sentinel", unlock_phrase="GO",
                    auto_trigger_daily_loss_pct=0.03,
                    auto_trigger_var_breach=True, max_var_daily=0.02)
    app = create_gateway(tokens={"VTOK1234": "viewer"}, kill_switch=ks,
                         audit_log=JsonlAuditLog(tmp_path / "gw.jsonl"),
                         snapshot_fn=snapshot, trades_fn=trades_fn,
                         pnl_history_fn=pnl_fn, ui_dir=None)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")
    H = {"Authorization": "Bearer VTOK1234"}

    state = (await c.get("/state", headers=H)).json()
    assert state["equity"] == pytest.approx(fs.broker.equity())
    hist = (await c.get("/pnl_history", headers=H)).json()
    assert hist[-1]["equity"] == pytest.approx(fs.broker.equity())
    assert (await c.get("/trades", headers=H)).status_code == 200


# ---------- 7. budget changes the HEAT denominator (the subtle interaction) ----------

async def test_heat_cap_measured_against_budget_not_account(tmp_path):
    """6% heat on a 2L budget = Rs.12k, NOT Rs.60k of the 10L account. A book
    carrying Rs.15k of stop-loss exposure is fine account-wise but MUST be
    rejected budget-wise."""
    budget = BudgetManager(200_000)
    heat = PortfolioHeatManager(max_heat_pct=0.06)
    fs = FullStack(tmp_path, budget=budget, heat_mgr=heat)
    budget.attach(fs.broker.equity())

    # preload a synthetic open position carrying Rs.15,000 heat
    from src.exits.exit_manager import ManagedPosition
    fs.exit_mgr.positions["HEAVY"] = ManagedPosition(
        symbol="HEAVY", direction="buy", entry=1000.0, qty=100, atr=10.0,
        leg="india", stop=850.0, r_value=150.0, stop_order_id="S-heavy",
        extreme=1000.0, remaining_qty=100)           # (1000-850)*100 = 15,000

    bars = trending_bars(80)
    _, res = await fs.enter("RELIANCE", bars, 79)
    assert not res.accepted and res.reason.startswith("portfolio_guard:heat:")

    # same book WITHOUT the budget fence: 15k of 10L = 1.5% -> admitted
    heat2 = PortfolioHeatManager(max_heat_pct=0.06)
    fs2 = FullStack(tmp_path / "nofence", heat_mgr=heat2)
    fs2.exit_mgr.positions["HEAVY"] = ManagedPosition(
        symbol="HEAVY", direction="buy", entry=1000.0, qty=100, atr=10.0,
        leg="india", stop=850.0, r_value=150.0, stop_order_id="S-heavy",
        extreme=1000.0, remaining_qty=100)
    _, res2 = await fs2.enter("RELIANCE", bars, 79)
    assert res2.accepted                             # account-wise it's fine


# ---------- 8. multi-day lifecycle: session resets, budget persists ----------

async def test_session_resets_daily_but_budget_carries_losses(tmp_path):
    budget = BudgetManager(200_000, min_floor_pct=0.5)
    sg = SessionGuard(loss_stop_pct=0.005)
    fs = FullStack(tmp_path, budget=budget, session_guard=sg)
    budget.attach(fs.broker.equity())

    # DAY 1: a -0.6% day trips the session guard...
    sg.start_session(1_000_000)
    assert not sg.allows_new_entries(994_000)
    assert sg.state.tripped == "loss_stopped"
    # ...but the budget only shrank by the loss, floor not breached
    assert budget.entries_allowed(994_000) == (True, "ok")
    assert budget.effective(994_000) == pytest.approx(194_000)

    # DAY 2: fresh session trades again; budget remembers day 1's loss
    sg.start_session(994_000)
    assert sg.allows_new_entries(994_000)
    bars = trending_bars(80)
    fs.broker.cash -= 6_000                          # reflect day-1 loss in equity
    _, res = await fs.enter("RELIANCE", bars, 79)
    assert res.accepted
    qty = res.record.filled_qty
    rpu = res.checks["size_factors"]["risk_per_unit"]
    # sized off the SHRUNKEN budget (194k), not the original 200k
    assert qty * rpu <= 194_000 * CFG.risk_limits.max_risk_per_trade_pct * 1.01


# ---------- 9. short entry end-to-end: BUY stop + partial qty sync ----------

async def test_short_chain_buy_stop_and_partial_sync(tmp_path):
    fs = FullStack(tmp_path)
    # falling series: tsmom_f says SELL
    bars = [{"date": b["date"], "open": 2000 - i * 6.0, "high": 2004 - i * 6.0,
             "low": 1996 - i * 6.0, "close": 2000 - i * 6.0, "volume": 10_000}
            for i, b in enumerate(trending_bars(80))]
    direction, res = await fs.enter("RELIANCE", bars, 79)
    assert direction == "sell" and res.accepted

    # the protective stop resting at the broker is a BUY (protects the short)
    resting = list(fs.broker.open_orders.values())
    assert resting and resting[0]["action"] == "BUY"
    entry = res.record.avg_fill_price
    qty0 = res.record.filled_qty

    # profitable move DOWN reaches +1R -> partial buys back, stop qty syncs
    r_val = fs.exit_mgr.positions["RELIANCE"].r_value
    px = entry - 1.2 * r_val
    fs.broker.on_tick("RELIANCE", px)
    await fs.exit_mgr.on_bar("RELIANCE", px + 1, px - 1, px, TREND)
    pos = fs.exit_mgr.positions["RELIANCE"]
    assert pos.partials_taken                        # partial fired
    assert pos.remaining_qty < qty0
    live = [o for o in fs.broker.open_orders.values()]
    assert live and live[0]["quantity"] <= pos.remaining_qty + 1e-9
    # net position is still SHORT the remaining qty (no phantom flip)
    assert fs.broker.positions["RELIANCE"]["qty"] == pytest.approx(-pos.remaining_qty)


# ---------- 10. restart recovery preserves the NEW position fields ----------

async def test_snapshot_restore_preserves_profit_lock_state(tmp_path):
    """Restart mid-trade: profit_locked and partials must survive the
    snapshot round-trip, and the restored manager must keep TIGHT-trailing
    (a lost profit_locked flag would silently widen the trail after a crash
    recovery). Old snapshots without the field must still load."""
    from src.exits.exit_manager import ExitManager as EM

    cfg = {k: v for k, v in FLAT_EXITS.items()}
    cfg["partials"] = []
    cfg["profit_lock"] = {"at_r": 1.0, "lock_r": 0.5, "trail_k": 0.3}

    class Spy:
        async def place_stop(self, *a, **kw): return "S1"
        async def modify_stop(self, *a, **kw): pass
        async def cancel_stop(self, *a, **kw): pass
        async def replace_stop(self, *a, **kw): return "S1"
        async def exit_market(self, *a, **kw): pass

    mgr = EM(cfg, Spy())
    pos = await mgr.attach(symbol="X", direction="buy", entry=100.0, qty=100,
                           atr=1.5, leg="india")                  # R = 3
    await mgr.on_bar("X", 104.0, 103.0, 103.5, TREND)             # +1R -> lock
    assert pos.profit_locked

    snap = mgr.to_snapshot()
    restored = EM.from_snapshot(snap, cfg, Spy())
    rpos = restored.positions["X"]
    assert rpos.profit_locked is True                 # survived the restart
    assert rpos.stop == pytest.approx(pos.stop)
    actions = await restored.on_bar("X", 110.0, 109.0, 109.5, TREND)
    assert any(a == "trail:0.3xATR" for a in actions) # STILL tight-trailing

    # legacy snapshot (pre-profit_lock field) loads with safe default
    legacy = {sym: {k: v for k, v in f.items() if k != "profit_locked"}
              for sym, f in snap.items()}
    old = EM.from_snapshot(legacy, cfg, Spy())
    assert old.positions["X"].profit_locked is False
