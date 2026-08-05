"""MODULE 49-53 tests — corporate actions, universe manager, order slicer,
TCA monitor, bar aggregator, and the router's portfolio-guard hook."""
import pytest

from src.core.order_slicer import plan_slices
from src.data.bar_aggregator import BarAggregator
from src.data.corporate_actions import CorporateAction, adjust_bars
from src.data.universe_manager import Instrument, UniverseManager
from src.ops.tca_monitor import TcaMonitor


# ---------- MODULE 49: corporate actions ----------

def bars_around(dates_prices):
    return [{"date": d, "open": p, "high": p * 1.01, "low": p * 0.99,
             "close": p, "volume": 1000} for d, p in dates_prices]


def test_bonus_back_adjusts_prices_and_volume():
    bars = bars_around([("2026-01-01", 2000.0), ("2026-01-02", 2010.0),
                        ("2026-01-03", 1005.0)])          # 1:1 bonus on Jan 3
    adj, log = adjust_bars(bars, [CorporateAction("X", "2026-01-03", "bonus", factor=2.0)])
    assert log[0]["applied"] and log[0]["factor"] == 2.0
    assert adj[0]["close"] == pytest.approx(1000.0)       # pre-ex halved
    assert adj[1]["close"] == pytest.approx(1005.0)
    assert adj[2]["close"] == pytest.approx(1005.0)       # ex-date untouched
    assert adj[0]["volume"] == pytest.approx(2000)        # volume doubled
    # continuity: no phantom 50% crash left in the series
    assert abs(adj[2]["close"] / adj[1]["close"] - 1) < 0.01
    assert bars[0]["close"] == 2000.0                     # input not mutated


def test_dividend_adjustment_uses_close_before():
    bars = bars_around([("2026-01-01", 100.0), ("2026-01-02", 95.0)])
    adj, log = adjust_bars(bars, [CorporateAction("X", "2026-01-02", "dividend", amount=5.0)])
    assert log[0]["factor"] == pytest.approx(100 / 95)
    assert adj[0]["close"] == pytest.approx(95.0)


def test_action_outside_range_skipped():
    bars = bars_around([("2026-01-01", 100.0)])
    _, log = adjust_bars(bars, [CorporateAction("X", "2030-01-01", "split", factor=2.0)])
    assert log[0]["applied"] is False


def test_invalid_actions_raise():
    with pytest.raises(ValueError):
        CorporateAction("X", "2026-01-01", "split", factor=1.0).price_factor(100)
    with pytest.raises(ValueError):
        CorporateAction("X", "2026-01-01", "dividend", amount=200).price_factor(100)


# ---------- MODULE 50: universe manager ----------

def make_universe():
    return UniverseManager([
        Instrument("RELIANCE", "india", 1, 8_000_000),
        Instrument("MICROCAP", "india", 1, 10_000),
        Instrument("EURUSD", "mt5_forex", 1000, 1e12, half_spread=5e-05),
        Instrument("NEWCOIN", "mt5_crypto", 0.01, 1e9, listed="2026-06-01"),
    ])


def test_liquidity_screen_drops_illiquid():
    u = make_universe()
    px = {"RELIANCE": 1300.0, "MICROCAP": 50.0, "EURUSD": 1.15, "NEWCOIN": 10.0}
    got = u.eligible(min_adv_notional=1e8, price_of=px.get)
    assert "RELIANCE" in got and "MICROCAP" not in got


def test_lifecycle_bounds_respected():
    u = make_universe()
    assert "NEWCOIN" not in u.eligible(date="2026-01-01")
    assert "NEWCOIN" in u.eligible(date="2026-07-01")


def test_meta_for_matches_replay_contract():
    u = make_universe()
    meta = u.meta_for(["EURUSD"])
    assert meta["EURUSD"]["leg"] == "mt5_forex"
    assert meta["EURUSD"]["half_spread"] == 5e-05


def test_liquidity_screen_fails_closed_without_prices():
    u = make_universe()
    assert u.eligible(min_adv_notional=1e8) == []


# ---------- MODULE 51: order slicer ----------

def test_large_order_twap_sliced_and_lot_floored():
    plan = plan_slices(qty=100_000, adv=8_000_000, lot_size=1,
                       max_participation_pct=0.05)
    assert plan.n_slices > 1
    assert sum(plan.slices) <= 100_000
    assert all(s == int(s) for s in plan.slices)          # integer lots
    assert plan.participation_pct <= 0.06


def test_small_order_single_slice():
    plan = plan_slices(qty=100, adv=8_000_000, lot_size=1)
    assert plan.n_slices == 1 and plan.slices == [100]


def test_slicer_fails_closed_on_garbage():
    assert plan_slices(qty=-5, adv=1e6).slices == []
    assert plan_slices(qty=100, adv=0).slices == []
    assert plan_slices(qty=0.005, adv=1e6, lot_size=0.01).reason == "below_one_lot"


# ---------- MODULE 52: TCA monitor ----------

def test_tca_flags_persistent_cost_drift():
    alerts = []
    tca = TcaMonitor(window=20, drift_alert_bps=2.0, min_samples=5,
                     alert_fn=alerts.append)
    for _ in range(6):                                     # model says 10, reality 60
        tca.record(symbol="X", expected_cost=10, actual_cost=60, notional=100_000)
    assert tca.rolling_drift_bps() == pytest.approx(5.0)
    assert alerts and alerts[0]["type"] == "tca_drift"


def test_tca_quiet_when_model_accurate():
    tca = TcaMonitor(min_samples=3)
    for _ in range(5):
        tca.record(symbol="X", expected_cost=50, actual_cost=51, notional=100_000)
    assert tca.alerts == []


def test_tca_alert_failure_never_raises():
    def boom(_): raise RuntimeError("pager down")
    tca = TcaMonitor(min_samples=1, drift_alert_bps=0.1, alert_fn=boom)
    tca.record(symbol="X", expected_cost=0, actual_cost=100, notional=10_000)  # no raise
    assert tca.alerts


# ---------- MODULE 53: bar aggregator ----------

def test_ticks_aggregate_into_time_aligned_bars():
    agg = BarAggregator(interval_s=60)
    assert agg.on_tick(0, 100) is None
    assert agg.on_tick(10, 105) is None
    assert agg.on_tick(20, 95) is None
    done = agg.on_tick(61, 99)                             # next bucket → emit
    assert (done.open, done.high, done.low, done.close) == (100, 105, 95, 95)
    assert agg.flush().open == 99


def test_stale_ticks_never_rewrite_history():
    agg = BarAggregator(interval_s=60)
    agg.on_tick(61, 100)
    assert agg.on_tick(5, 500) is None                     # stale: dropped
    assert agg.flush().high == 100


# ---------- router portfolio-guard hook ----------

async def test_router_portfolio_guard_rejects_before_sizing():
    from pathlib import Path
    from src.core.config_loader import load_config
    from src.core.kill_switch import KillSwitch
    from src.core.margin_checker import MarginChecker
    from src.core.order_router import VAR_CACHE_KEY, OrderRequest, OrderRouter

    class MemRedis:
        def __init__(self): self.store = {}
        async def get(self, k): return self.store.get(k)
        async def set(self, k, v): self.store[k] = v
        async def setex(self, k, ttl, v): self.store[k] = v
        async def delete(self, k): self.store.pop(k, None)

    class API:
        async def available_margin(self): return 1e9
        async def required_margin(self, *a): return 0.0
        async def free_margin(self): return 1e9
        async def equity(self): return 1e9
        async def margin_required(self, *a): return 0.0

    cfg = load_config("config/master.yaml")
    redis = MemRedis()
    redis.store[VAR_CACHE_KEY] = "0.005"
    ks = KillSwitch(redis=redis, brokers={}, sentinel_path=Path("/tmp/pg_halt.sentinel"),
                    unlock_phrase="X", auto_trigger_daily_loss_pct=0.03,
                    auto_trigger_var_breach=True, max_var_daily=0.02)

    async def guard(req):
        return False, "portfolio_heat 0.08 > cap 0.06"

    class NoAnomaly:
        async def entries_paused(self, *a, **kw): return False

    router = OrderRouter(config=cfg, kill_switch=ks, anomaly_guard=NoAnomaly(),
                         margin_checker=MarginChecker(cfg.risk_limits, india_api=API(), mt5_api=API()),
                         connections=None, redis=redis, balance_fn=lambda: 1_000_000.0,
                         signal_valid_fn=lambda s, d: True, band_check_fn=lambda s, p: True,
                         session_open_fn=lambda leg: True, audit_fn=lambda row: None,
                         portfolio_guard_fn=guard)
    res = await router.route_order(OrderRequest(
        symbol="RELIANCE", direction="buy", entry=1300.0, stop=1270.0, atr=15.0,
        algo_id="ALGO-1", product="intraday"))
    assert not res.accepted
    assert res.reason.startswith("portfolio_guard:")
    assert "portfolio_guard" in res.checks
