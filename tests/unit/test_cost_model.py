"""MODULE 40 tests — hand-computed schedules + square-root-law properties.

NOTE (acceptance): reproducing REAL broker contract notes to ≤1% requires the
operator's actual notes — tracked as PENDING-USER-DATA in progress.md. These
tests pin the math to the configured schedule exactly.
"""
import math

import pytest

from src.core.config_loader import load_config
from src.core.transaction_cost_model import (
    impact_cost,
    impact_fraction,
    india_round_trip_cost,
    india_trade_cost,
    mt5_trade_cost,
    net_edge,
)

CFG = load_config("config/master.yaml")
IN = CFG.execution_costs.india
IMP = CFG.execution_costs.impact_model


def test_delivery_buy_hand_computed():
    # 100 shares @ 2500 = 250,000 notional
    bd = india_trade_cost(IN, "buy", 100, 2500.0, "delivery")
    assert bd.notional == 250_000
    assert bd.components["brokerage"] == pytest.approx(20.0)
    assert bd.components["stt"] == pytest.approx(250_000 * 0.001)          # 250
    assert bd.components["exchange_txn"] == pytest.approx(250_000 * 0.0000345)
    assert bd.components["stamp_duty"] == pytest.approx(250_000 * 0.00015)  # buy only
    assert bd.components["gst"] == pytest.approx(0.18 * (20.0 + 250_000 * 0.0000345))
    assert bd.total == pytest.approx(20 + 250 + 8.625 + 37.5 + 5.1525)


def test_intraday_sell_stt_and_no_stamp():
    bd = india_trade_cost(IN, "sell", 100, 2500.0, "intraday")
    assert bd.components["stt"] == pytest.approx(250_000 * 0.00025)
    assert bd.components["stamp_duty"] == 0.0


def test_intraday_buy_has_no_stt():
    bd = india_trade_cost(IN, "buy", 100, 2500.0, "intraday")
    assert bd.components["stt"] == 0.0
    assert bd.components["stamp_duty"] > 0


def test_round_trip_sums_both_sides():
    rt = india_round_trip_cost(IN, 100, 2500.0, 2550.0, "delivery")
    buy = india_trade_cost(IN, "buy", 100, 2500.0, "delivery").total
    sell = india_trade_cost(IN, "sell", 100, 2550.0, "delivery").total
    assert rt == pytest.approx(buy + sell)


def test_zero_and_invalid_inputs():
    assert india_trade_cost(IN, "buy", 0, 2500.0).total == 0.0
    assert impact_cost(IMP, 0, 100, 1e6, 0.02) == 0.0
    assert impact_cost(IMP, 100, 100, 0, 0.02) == 0.0
    with pytest.raises(ValueError):
        india_trade_cost(IN, "short", 10, 100.0)
    with pytest.raises(ValueError):
        india_trade_cost(IN, "buy", 10, 100.0, product="bo")


def test_sqrt_law_scaling():
    """4× the quantity ⇒ exactly 2× the impact FRACTION (√ law)."""
    f1 = impact_fraction(IMP, 50_000, 5_000_000, 0.02)
    f4 = impact_fraction(IMP, 200_000, 5_000_000, 0.02)
    assert f4 == pytest.approx(2 * f1)
    # and the hand value: 0.7 * 0.02 * sqrt(0.01) = 0.0014
    assert f1 == pytest.approx(0.7 * 0.02 * math.sqrt(50_000 / 5_000_000))


def test_impact_cost_monotone_in_qty():
    prev = 0.0
    for q in (1_000, 10_000, 50_000, 200_000):
        c = impact_cost(IMP, q, 250.0, 5_000_000, 0.02)
        assert c > prev
        prev = c


def test_mt5_spread_and_swap():
    bd = mt5_trade_cost(lots=2.0, spread_points=1.0, point_value_per_lot=10.0,
                        swap_cost_per_lot_per_day=5.0, hold_days=3)
    assert bd.components["spread"] == pytest.approx(20.0)
    assert bd.components["swap"] == pytest.approx(30.0)
    assert mt5_trade_cost(0, 1, 10).total == 0.0


def test_net_edge_gate():
    assert net_edge(expected_profit=500.0, total_cost=321.0) > 0
    assert net_edge(expected_profit=300.0, total_cost=321.0) < 0
