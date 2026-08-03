"""ROBUSTNESS SUITE — adversarial inputs, poison data, concurrency, invariants.

These tests attack the system the way production will: corrupt feeds, NaN from
a bad vendor row, concurrent triggers, random event sequences. Every failure
here is a live-trading incident prevented.
"""
import asyncio
import math
import random

import pytest

from src.core.config_loader import load_config
from src.core.order_state_machine import (
    IllegalTransition,
    OrderState,
    OrderStateMachine,
)
from src.core.paper_broker import PaperBroker
from src.core.position_sizer import calculate_position_size
from src.core.transaction_cost_model import india_trade_cost, impact_cost
from src.exits.exit_manager import ExitManager
from src.intel.regime_detector import adx, hurst_exponent, rolling_vol_series
from src.intel.tick_feed import TickFeedWorker
from src.ops.snapshot import SnapshotBuilder
from src.risk.var_worker import historical_var, kupiec_pof
from tests.fixtures.fakes import FakeRedis, MockBroker

CFG = load_config("config/master.yaml")
NAN, INF = float("nan"), float("inf")


# ================= position sizer: NaN/inf must NEVER produce an order =================

@pytest.mark.parametrize("bad", [NAN, INF, -INF])
@pytest.mark.parametrize("field", ["entry", "stop", "atr", "balance", "current_var"])
def test_sizer_rejects_non_finite_inputs(bad, field):
    kw = dict(entry=100.0, stop=98.0, atr=1.5, balance=1_000_000.0,
              current_var=0.005, risk=CFG.risk_limits)
    kw[field] = bad
    r = calculate_position_size(**kw)
    assert r.qty == 0.0, f"NaN/inf in {field} produced qty={r.qty} — would send a broken order"
    assert math.isfinite(r.notional)


def test_sizer_fuzz_never_emits_non_finite_or_overcap():
    rng = random.Random(99)
    pool = [NAN, INF, -INF, 0.0, -1.0, 1e-12, 1e12]
    for _ in range(800):
        def val(base):
            return rng.choice(pool) if rng.random() < 0.25 else base * rng.uniform(0.1, 10)
        entry, stop, atr = val(100.0), val(98.0), val(1.5)
        balance, cur_var = val(1_000_000.0), val(0.005)
        r = calculate_position_size(
            entry=entry, stop=stop, atr=atr, balance=balance, current_var=cur_var,
            risk=CFG.risk_limits, lot_size=rng.choice([1, 25, 0.01]),
            p_win=rng.choice([None, val(0.6)]), payoff_ratio=rng.choice([None, val(1.5)]))
        assert math.isfinite(r.qty) and r.qty >= 0
        assert math.isfinite(r.notional)
        if r.qty > 0:
            # any positive size implies ALL inputs were finite and sane; cap vs ACTUAL balance
            assert math.isfinite(balance) and balance > 0
            assert r.notional <= balance * CFG.risk_limits.max_position_pct * (1 + 1e-9)


# ================= cost model: corrupt quantities must be LOUD =================

def test_cost_model_rejects_non_finite():
    with pytest.raises(ValueError):
        india_trade_cost(CFG.execution_costs.india, "buy", NAN, 100.0)
    with pytest.raises(ValueError):
        india_trade_cost(CFG.execution_costs.india, "buy", 10, INF)
    assert impact_cost(CFG.execution_costs.impact_model, NAN, 100, 1e6, 0.02) == 0.0


# ================= exit engine: corrupt bars must not move stops =================

class NullAdapter:
    async def place_stop(self, *a):
        return "S1"

    async def modify_stop(self, *a):
        pass

    async def exit_market(self, *a):
        pass


async def test_exit_manager_skips_corrupt_bars():
    mgr = ExitManager(CFG.model_extra["exit_manager"], NullAdapter())
    pos = await mgr.attach(symbol="X", direction="buy", entry=100.0, qty=10,
                           atr=1.5, leg="india")
    stop_before = pos.stop
    for high, low, close in [(NAN, 99, 100), (105, NAN, 104), (104, 103, NAN),
                             (99, 103, 101),        # high < low — corrupt feed
                             (INF, 90, 100)]:
        actions = await mgr.on_bar("X", high, low, close,
                                   {"trend_state": "STRONG_TREND", "vol_regime": "NORMAL"})
        assert actions == ["skipped:corrupt_bar"], f"bar {(high, low, close)} was processed!"
    assert pos.stop == stop_before and pos.state == "RISK_ON"


# ================= tick feed: poison ticks must not kill the spine =================

async def test_tick_feed_survives_poison_ticks():
    redis = FakeRedis()
    from src.intel.anomaly_guard import AnomalyGuard
    guard = AnomalyGuard(redis=redis, velocity_sigma={"s1": 6, "s5": 5, "s30": 4},
                         spread_blowout_mult=3, volume_spike_mult=5, cooloff_minutes=15)
    mgr = ExitManager(CFG.model_extra["exit_manager"], NullAdapter())
    sb = SnapshotBuilder(mode="paper")

    async def stream():
        yield {"symbol": "X", "price": 100.0, "ts": 1}
        yield {"symbol": "X", "price": 0.0, "ts": 2}          # poison
        yield {"symbol": "X", "price": NAN, "ts": 3}          # poison
        yield {"symbol": "X", "price": -5.0, "ts": 4}         # poison
        for i in range(9):
            yield {"symbol": "X", "price": 100.5 + i * 0.1, "ts": 5 + i}

    worker = TickFeedWorker(stream_factory=stream, guard=guard, exit_mgr=mgr,
                            snapshot=sb, redis=redis, sub_bar_ticks=5)
    await worker.run()
    assert worker.processed == 10                              # 13 in, 3 poison skipped
    assert worker.bad_ticks == 3
    assert "X" in sb.candles                                   # spine kept producing


# ================= order state machine: random-sequence invariant fuzz =================

def test_osm_fuzz_invariants_hold_over_random_sequences():
    rng = random.Random(1234)
    for _ in range(300):
        osm = OrderStateMachine()
        rec = osm.create("X", "buy", 100, "india")
        filled_seen = 0.0
        for _ in range(rng.randint(1, 12)):
            op = rng.choice(["sent", "ack", "fill", "reject", "cancel", "timeout"])
            try:
                if op == "sent":
                    osm.mark_sent(rec)
                elif op == "ack":
                    osm.on_ack(rec, "B1")
                elif op == "fill":
                    osm.on_fill(rec, rng.choice([10, 40, 100, 150]), 100.0)
                elif op == "reject":
                    osm.on_reject(rec, "r")
                elif op == "cancel":
                    osm.on_cancel(rec)
                else:
                    osm.on_timeout(rec)
            except (IllegalTransition, ValueError):
                pass                                           # refusals are the contract
            # INVARIANTS — must hold after every event, legal or refused:
            assert 0 <= rec.filled_qty <= rec.requested_qty + 1e-9
            assert rec.filled_qty >= filled_seen               # monotone, never un-fills
            filled_seen = rec.filled_qty
            if rec.is_terminal:
                state_at_terminal = rec.state
                # terminal is absorbing: any further event must refuse or no-op
                try:
                    osm.on_fill(rec, 1, 100.0)
                except (IllegalTransition, ValueError):
                    pass
                assert rec.state is state_at_terminal


# ================= paper broker: money conservation invariant =================

def test_paper_broker_conserves_money_exactly():
    """cash + position value == starting cash − costs (zero-slippage config)."""
    b = PaperBroker(costs=CFG.execution_costs.india, impact=CFG.execution_costs.impact_model,
                    starting_cash=1_000_000.0,
                    adv_map={"X": 1e15},                       # slippage → 0
                    daily_sigma_map={"X": 0.0},
                    mt5_cost_map={})
    b.on_tick("X", 100.0)
    rng = random.Random(5)
    held = 0
    for _ in range(120):
        if held > 0 and rng.random() < 0.5:
            qty = rng.randint(1, held)
            b.place_order({"symbol": "X", "action": "SELL", "quantity": qty,
                           "pricetype": "MARKET", "product": "MIS"})
            held -= qty
        else:
            qty = rng.randint(1, 50)
            r = b.place_order({"symbol": "X", "action": "BUY", "quantity": qty,
                               "pricetype": "MARKET", "product": "MIS"})
            if r["status"] == "success":
                held += qty
    position_value = held * 100.0
    assert b.cash + position_value == pytest.approx(1_000_000.0 - b.total_costs, abs=1e-6)


# ================= concurrency: kill switch + router under parallel fire =================

async def test_concurrent_kill_all_is_safe(tmp_path):
    from src.core.kill_switch import KillSwitch
    ks = KillSwitch(redis=FakeRedis(),
                    brokers={"india": MockBroker("india",
                                                 orders=[{"id": f"O{i}"} for i in range(10)],
                                                 positions=[{"id": f"P{i}"} for i in range(5)])},
                    sentinel_path=tmp_path / "h.sentinel", unlock_phrase="GO",
                    auto_trigger_daily_loss_pct=0.03, auto_trigger_var_breach=True,
                    max_var_daily=0.02)
    r1, r2, r3 = await asyncio.gather(ks.kill_all("a"), ks.kill_all("b"), ks.kill_all("c"))
    assert await ks.is_halted()
    total_cancels = len(r1.orders_cancelled) + len(r2.orders_cancelled) + len(r3.orders_cancelled)
    assert total_cancels == 10                                 # each order cancelled EXACTLY once
    total_closes = len(r1.positions_closed) + len(r2.positions_closed) + len(r3.positions_closed)
    assert total_closes == 5


async def test_router_under_concurrent_load(tmp_path):
    from tests.unit.test_order_router import build_router, req
    router, ctx = build_router()
    results = await asyncio.gather(*[router.route_order(req()) for _ in range(20)])
    accepted = [r for r in results if r.accepted]
    assert len(accepted) == 20
    ids = [r.record.client_order_id for r in accepted]
    assert len(set(ids)) == 20                                 # unique client ids under load
    assert len(ctx["audits"]) == 20                            # every order audited


# ================= math edge cases locked =================

def test_math_edges_never_crash():
    assert historical_var([0.01], 0.95) == 0.0                 # single gain day
    lr, ok = kupiec_pof(0, 250, 0.05)
    assert math.isfinite(lr)
    flat = [100.0] * 300
    assert hurst_exponent(flat) == 0.5                         # constant series → neutral
    assert adx([100.0] * 40, [100.0] * 40, [100.0] * 40) == 0.0
    assert rolling_vol_series([0.0] * 50, 10)[-1] == 0.0
