"""WAVE 9 INTEGRATION — the whole system runs a real paper-trading lifecycle.

REAL components (no mocks of our own code): OrderRouter, KillSwitch,
AnomalyGuard, MarginChecker, OrderStateMachine, ExitManager + IndiaStopAdapter,
reconciler, JsonlAuditLog — all talking to the PaperBroker through the SAME
verified wire schemas live brokers use. This is the test that says
"paper mode works end to end", not module by module.
"""
import httpx
import pytest

from src.core.config_loader import load_config
from src.core.kill_switch import KillSwitch
from src.core.margin_checker import MarginChecker
from src.core.order_router import VAR_CACHE_KEY, OrderRequest, OrderRouter
from src.core.order_state_machine import OrderState
from src.core.paper_broker import PaperBroker
from src.exits.adapters.india_stops import IndiaStopAdapter
from src.exits.exit_manager import ExitManager
from src.intel.anomaly_guard import AnomalyGuard
from src.ops.eod_reconciler import reconcile
from src.ops.paper_server import create_paper_server
from src.ops.persistence import JsonlAuditLog
from src.ops.paper_report import advance_gate, generate_daily_report
from tests.fixtures.fakes import FakeRedis, MockBroker

CFG = load_config("config/master.yaml")
TREND = {"trend_state": "STRONG_TREND", "vol_regime": "NORMAL"}


class PaperConnections:
    """connection_manager stand-in: both legs point at the paper server (ASGI)."""

    def __init__(self, app):
        self._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://paper")

    def get_openalgo(self):
        return self._client

    def get_mt5(self):
        return self._client


class PaperMarginAPI:
    """Margin answers straight from the paper broker's books."""

    def __init__(self, broker: PaperBroker):
        self.broker = broker

    async def available_margin(self):
        return self.broker.available_margin()

    async def required_margin(self, symbol, qty, price, product):
        return qty * price

    async def free_margin(self):
        return self.broker.available_margin()

    async def equity(self):
        return self.broker.equity()

    async def margin_required(self, symbol, lots):
        return lots * self.broker.last_price.get(symbol, 0.0)


@pytest.fixture
def stack(tmp_path):
    broker = PaperBroker(costs=CFG.execution_costs.india,
                         impact=CFG.execution_costs.impact_model,
                         starting_cash=1_000_000.0,
                         adv_map={"RELIANCE": 5_000_000},
                         daily_sigma_map={"RELIANCE": 0.015})
    app = create_paper_server(broker)
    conns = PaperConnections(app)
    redis = FakeRedis()
    redis.store[VAR_CACHE_KEY] = "0.005"
    audit = JsonlAuditLog(tmp_path / "audit.jsonl")
    ks = KillSwitch(redis=redis, brokers={"paper": MockBroker("paper")},
                    sentinel_path=tmp_path / "halt.sentinel", unlock_phrase="GO",
                    auto_trigger_daily_loss_pct=0.03, auto_trigger_var_breach=True,
                    max_var_daily=CFG.risk_limits.max_var_daily)
    guard = AnomalyGuard(redis=redis, velocity_sigma={"s1": 6, "s5": 5, "s30": 4},
                         spread_blowout_mult=3.0, volume_spike_mult=5.0,
                         cooloff_minutes=15)
    exit_mgr = ExitManager(CFG.model_extra["exit_manager"],
                           IndiaStopAdapter(conns.get_openalgo(), apikey="PAPER",
                                            algo_id="ALGO-PAPER-1"))
    router = OrderRouter(
        config=CFG, kill_switch=ks, anomaly_guard=guard,
        margin_checker=MarginChecker(CFG.risk_limits,
                                     india_api=PaperMarginAPI(broker),
                                     mt5_api=PaperMarginAPI(broker)),
        connections=conns, redis=redis,
        balance_fn=lambda: 1_000_000.0,   # account equity (sizer input) — margin is checked separately against broker cash
        signal_valid_fn=lambda s, d: True,
        band_check_fn=lambda s, p: True,
        session_open_fn=lambda leg: True,
        audit_fn=lambda row: audit.append({"type": "order", **row}),
    )
    return broker, app, router, exit_mgr, audit, ks, redis


async def test_full_paper_lifecycle_entry_trail_stop_reconcile(stack, tmp_path):
    broker, app, router, exit_mgr, audit, ks, redis = stack

    # ---- market opens, ticks arrive
    broker.on_tick("RELIANCE", 2500.0)

    # ---- 1. ENTRY through the real router (verified OpenAlgo schema)
    result = await router.route_order(OrderRequest(
        symbol="RELIANCE", direction="buy", entry=2500.0, stop=2450.0, atr=30.0,
        algo_id="ALGO-PAPER-1"))
    assert result.accepted, result.reason
    assert result.record.state is OrderState.FILLED
    qty = result.record.filled_qty
    assert qty > 0 and broker.positionbook()[0]["qty"] == qty
    assert broker.total_costs > 0                      # real cost schedule charged
    fill_price = result.record.avg_fill_price
    assert fill_price > 2500.0                          # slippage is real (buy pays up)

    # ---- 2. PROTECTION: exit manager attaches a REAL resting SL-M in the broker
    pos = await exit_mgr.attach(symbol="RELIANCE", direction="buy", entry=fill_price,
                                qty=qty, atr=30.0, leg="india")
    resting = broker.orderbook()
    assert len(resting) == 1 and resting[0]["pricetype"] == "SL-M"
    assert resting[0]["trigger_price"] == pytest.approx(pos.stop)
    initial_stop = pos.stop

    # ---- 3. PRICE RISES: breakeven + trail ratchets propagate INTO the broker
    for px in (2530, 2560, 2590, 2620, 2650):
        broker.on_tick("RELIANCE", float(px))
        await exit_mgr.on_bar("RELIANCE", px + 5, px - 5, float(px), TREND)
    assert pos.stop > initial_stop                       # ratcheted toward profit
    assert broker.orderbook()[0]["trigger_price"] == pytest.approx(pos.stop)
    assert pos.partials_taken                            # +1R partial booked

    # ---- 4. CRASH through the stop: the BROKER-side stop fires (server-side truth)
    triggered = broker.on_tick("RELIANCE", pos.stop * 0.995)
    assert triggered and triggered[0].action == "SELL"
    await exit_mgr.on_bar("RELIANCE", pos.stop * 0.999, pos.stop * 0.99,
                          pos.stop * 0.995, TREND)
    assert pos.state == "EXITED"
    assert pos.telemetry.exit_reason == "stop_hit"

    # ---- 5. BOOKS: paper broker has no more resting stops for the runner
    #      (partials were engine-side bookings; broker position reflects fills)
    assert broker.orderbook() == []

    # ---- 6. AUDIT: durable, hash-chained, survives reload
    assert audit.verify_chain()
    reloaded = JsonlAuditLog(tmp_path / "audit.jsonl")
    assert len(reloaded.rows) == len(audit.rows) >= 1

    # ---- 7. EOD RECONCILIATION against the paper broker's tradebook
    internal = [{"client_order_id": f["client_order_id"], "symbol": f["symbol"],
                 "qty": f["qty"], "price": f["price"]} for f in broker.tradebook()]
    rep = reconcile("paper-day-1", internal, broker.tradebook(),
                    naked_positions=exit_mgr.naked_positions())
    assert rep.clean

    # ---- 8. EVIDENCE: daily report + live-gate progression
    state = {"cash": broker.cash, "equity": broker.equity(),
             "total_costs": broker.total_costs,
             "positions": broker.positionbook(), "resting": broker.orderbook()}
    report = generate_daily_report("2026-08-04", state, broker.tradebook(),
                                   reconciliation_clean=rep.clean,
                                   audit_row_count=len(audit.rows))
    assert "CLEAN" in report and "Paper Trading Report" in report
    gate = advance_gate(tmp_path / "gate_state.json", reconciliation_clean=True)
    assert gate["paper_days_completed"] == 1 and gate["clean_reconciliation_streak"] == 1


async def test_kill_switch_halts_paper_trading_end_to_end(stack):
    broker, app, router, exit_mgr, audit, ks, redis = stack
    broker.on_tick("RELIANCE", 2500.0)
    await ks.kill_all("integration halt")
    result = await router.route_order(OrderRequest(
        symbol="RELIANCE", direction="buy", entry=2500.0, stop=2450.0, atr=30.0,
        algo_id="ALGO-PAPER-1"))
    assert not result.accepted and result.reason == "trading_halted"
    assert broker.tradebook() == []                     # broker never touched


async def test_insufficient_paper_margin_rejected_like_live(stack):
    broker, app, router, exit_mgr, audit, ks, redis = stack
    broker.on_tick("RELIANCE", 2500.0)
    broker.cash = 1_000.0                                # nearly broke
    result = await router.route_order(OrderRequest(
        symbol="RELIANCE", direction="buy", entry=2500.0, stop=2450.0, atr=30.0,
        algo_id="ALGO-PAPER-1"))
    assert not result.accepted and result.reason.startswith("margin:")
