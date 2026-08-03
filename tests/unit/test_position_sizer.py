"""MODULE 3 tests — spec acceptance: edge cases + property test (cap invariant)."""
import random

import pytest

from src.core.config_loader import load_config
from src.core.position_sizer import calculate_position_size, kelly_fraction

RISK = load_config("config/master.yaml").risk_limits


def base_kwargs(**over):
    kw = dict(entry=100.0, stop=98.0, atr=1.5, balance=1_000_000.0, current_var=0.005, risk=RISK)
    kw.update(over)
    return kw


def test_normal_sizing_positive_and_capped():
    r = calculate_position_size(**base_kwargs())
    assert r.reason == "ok" and r.qty > 0
    assert r.notional <= 1_000_000 * RISK.max_position_pct * (1 + 1e-9)


def test_stop_at_entry_is_zero():
    r = calculate_position_size(**base_kwargs(stop=100.0))
    assert r.qty == 0 and r.reason == "stop_at_entry"


def test_var_at_limit_is_zero():
    r = calculate_position_size(**base_kwargs(current_var=RISK.max_var_daily))
    assert r.qty == 0 and r.reason == "var_at_limit"


def test_var_headroom_scales_down():
    full = calculate_position_size(**base_kwargs(current_var=0.0)).qty
    half = calculate_position_size(**base_kwargs(current_var=RISK.max_var_daily / 2)).qty
    assert 0 < half < full
    assert half == pytest.approx(full * 0.5, rel=0.05)


def test_gap_survival_bounds_size():
    """With a huge ATR, a 3×ATR gap must still respect the daily loss budget."""
    r = calculate_position_size(**base_kwargs(atr=50.0, current_var=0.0))
    worst = 2.0 + RISK.gap_assumption_atr * 50.0
    assert r.qty * worst <= 1_000_000 * RISK.max_daily_loss_pct * (1 + 1e-9)


def test_kelly_negative_edge_zero():
    r = calculate_position_size(**base_kwargs(p_win=0.5, payoff_ratio=1.0))
    assert r.qty == 0 and r.reason == "no_edge_kelly"


def test_kelly_positive_edge_scales_but_never_exceeds_cap():
    r = calculate_position_size(**base_kwargs(p_win=0.60, payoff_ratio=1.5, current_var=0.0))
    assert r.reason == "ok" and r.qty > 0
    assert r.factors["kelly_clamped"] <= RISK.kelly_cap
    assert r.notional <= 1_000_000 * RISK.max_position_pct * (1 + 1e-9)


def test_kelly_fraction_math():
    assert kelly_fraction(0.6, 1.5) == pytest.approx(0.6 - 0.4 / 1.5)
    assert kelly_fraction(0.5, 1.0) == pytest.approx(0.0)
    assert kelly_fraction(0.9, 0.0) == 0.0


def test_cost_gate_zeroes_uneconomic_trade():
    r = calculate_position_size(
        **base_kwargs(),
        expected_profit_per_unit=0.01,          # 1 paisa expected per share
        cost_fn=lambda q: 1_000.0,              # flat huge cost
    )
    assert r.qty == 0 and r.reason == "no_net_edge_after_costs"


def test_lot_flooring():
    r = calculate_position_size(**base_kwargs(lot_size=75))
    assert r.reason == "ok"
    assert r.qty % 75 == 0


def test_invalid_inputs():
    assert calculate_position_size(**base_kwargs(entry=-5)).reason == "invalid_inputs"
    assert calculate_position_size(**base_kwargs(balance=0)).reason == "invalid_inputs"


def test_property_never_exceeds_position_cap_and_never_negative():
    """Spec acceptance: never exceeds max_position_pct regardless of inputs."""
    rng = random.Random(42)
    for _ in range(500):
        balance = rng.uniform(10_000, 10_000_000)
        entry = rng.uniform(1, 50_000)
        stop = entry * rng.uniform(0.80, 1.20)
        r = calculate_position_size(
            entry=entry,
            stop=stop,
            atr=rng.uniform(0, entry * 0.1),
            balance=balance,
            current_var=rng.uniform(0, RISK.max_var_daily * 1.5),
            risk=RISK,
            lot_size=rng.choice([1, 1, 1, 25, 75]),
            p_win=rng.choice([None, rng.uniform(0.3, 0.8)]),
            payoff_ratio=rng.choice([None, rng.uniform(0.5, 3.0)]),
        )
        assert r.qty >= 0
        assert r.notional <= balance * RISK.max_position_pct * (1 + 1e-9)
