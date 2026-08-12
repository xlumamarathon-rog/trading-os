"""MODULE 69 — prop-firm guard + challenge math. Invariants:

  - the firm's day rolls at ITS server reset hour, not ours
  - equity marks include floating P&L; daily budget anchors to day-start
  - OUR soft line (60% of budget) refuses entries BEFORE the firm's line
  - a breach latches — the exam is over, no un-breaching
  - trailing vs static drawdown compute from the right base
  - the challenge Monte Carlo is deterministic, and the risk sweep has a
    peak: too little risk times out, too much busts
  - guard-stack integration: prop refusals surface as "prop:..." reasons
"""
import datetime as dt

import pytest

from src.core.guard_stack import make_portfolio_guard
from src.ops.prop_rules import (PropGuard, PropRules, challenge_monte_carlo,
                                optimal_challenge_risk)

UTC = dt.timezone.utc
RULES = PropRules(initial_balance=10_000, max_daily_loss_pct=0.05,
                  max_total_dd_pct=0.10, profit_target_pct=0.10,
                  min_trading_days=4, soft_fraction=0.60,
                  day_reset_utc_hour=21)


def t(day, hour):
    return dt.datetime(2026, 8, day, hour, 0, tzinfo=UTC)


class TestDailyAnchor:
    def test_day_rolls_at_firm_reset_not_utc_midnight(self):
        g = PropGuard(RULES)
        g.on_equity(10_000, t(11, 10))
        assert g.state.day_key == g._day_key(t(11, 20, ))
        # 20:59 UTC same firm-day; 21:01 UTC = NEW firm day
        k1 = g._day_key(t(11, 20))
        k2 = g._day_key(t(11, 22))
        assert k1 != k2

    def test_daily_budget_anchors_to_day_start_equity(self):
        g = PropGuard(RULES)
        g.on_equity(10_400, t(11, 22))          # new firm day starts at 10,400
        st = g.on_equity(10_100, t(12, 10))     # -300 on the day
        assert st["breached"] == ""
        assert st["daily_budget_left"] == pytest.approx(10_400 * 0.05 - 300, abs=1)

    def test_soft_stop_fires_before_the_firm_line(self):
        g = PropGuard(RULES)
        g.on_equity(10_000, t(11, 10))
        # -350 = 70% of the 500 daily budget: soft stop, NOT breached
        ok, why = g.allows_new_entries(9_650, t(11, 12))
        assert not ok and why == "prop:daily_soft_stop"
        assert g.state.breached == ""

    def test_breach_latches(self):
        g = PropGuard(RULES)
        g.on_equity(10_000, t(11, 10))
        st = g.on_equity(9_490, t(11, 12))      # -510 > 500 daily budget
        assert st["breached"] == "daily_loss_breached"
        st = g.on_equity(10_500, t(12, 10))     # recovery does NOT un-breach
        assert st["breached"] == "daily_loss_breached"
        ok, why = g.allows_new_entries(10_500, t(12, 11))
        assert not ok and why == "prop:daily_loss_breached"


class TestTotalDrawdown:
    def test_static_dd_from_initial_balance(self):
        g = PropGuard(RULES)
        g.on_equity(10_800, t(11, 10))          # profits first
        # new firm day anchors the DAILY budget lower, so the TOTAL line
        # is what bites (a single-day -1,750 would breach daily first —
        # which the daily tests already cover)
        g.on_equity(9_100, t(11, 22))           # new firm day @ 9,100
        st = g.on_equity(9_050, t(12, 10))      # -950 from initial: inside 1000
        assert st["breached"] == ""
        st = g.on_equity(8_990, t(12, 11))      # -1010 from initial: breach
        assert st["breached"] == "max_drawdown_breached"

    def test_trailing_dd_from_high_water(self):
        g = PropGuard(PropRules(initial_balance=10_000, trailing_dd=True,
                                max_total_dd_pct=0.10, max_daily_loss_pct=0.99,
                                soft_fraction=0.99))
        g.on_equity(11_000, t(11, 10))          # high-water 11,000
        st = g.on_equity(9_950, t(11, 12))      # -1050 from HW > 1100? no: 1050<1100
        assert st["breached"] == ""
        st = g.on_equity(9_890, t(11, 13))      # -1110 from HW: breach
        assert st["breached"] == "max_drawdown_breached"


class TestProgress:
    def test_target_and_min_days(self):
        g = PropGuard(RULES)
        g.record_trade(t(11, 10)); g.record_trade(t(11, 12))   # same firm day
        g.record_trade(t(12, 10))
        st = g.on_equity(11_050, t(12, 11))
        assert st["target_reached"] is True
        assert st["traded_days"] == 2           # 2 distinct firm days
        assert st["min_trading_days"] == 4      # not yet enough days


# ------------------------------------------------------------ challenge math

COIN = [1.0] * 58 + [-1.0] * 42                  # 58% +1R/-1R — a real edge


class TestChallengeMath:
    def test_deterministic(self):
        a = challenge_monte_carlo(COIN, rules=RULES, risk_pct=0.01, paths=400)
        b = challenge_monte_carlo(COIN, rules=RULES, risk_pct=0.01, paths=400)
        assert a == b

    def test_too_little_risk_times_out(self):
        mc = challenge_monte_carlo(COIN, rules=RULES, risk_pct=0.0005,
                                   trades_per_day=1.0, max_days=30, paths=400)
        assert mc["p_timeout"] > 0.9             # +10% target unreachable

    def test_too_much_risk_busts(self):
        mc = challenge_monte_carlo(COIN, rules=RULES, risk_pct=0.10,
                                   trades_per_day=2.0, max_days=60, paths=400)
        assert mc["p_bust"] > 0.4                # firm lines eat the account

    def test_sweep_finds_an_interior_peak(self):
        out = optimal_challenge_risk(COIN, rules=RULES, trades_per_day=2.0,
                                     max_days=60, paths=500)
        best = out["best"]
        curve = {c["risk_pct"]: c["p_pass"] for c in out["curve"]}
        assert best["p_pass"] == max(curve.values())
        # the peak is interior: the extremes are strictly worse
        assert curve[0.0025] < best["p_pass"]
        assert curve[0.06] < best["p_pass"]

    def test_negative_edge_cannot_pass(self):
        bad = [1.0] * 40 + [-1.0] * 60
        out = optimal_challenge_risk(bad, rules=RULES, trades_per_day=2.0,
                                     max_days=60, paths=400)
        assert out["best"]["p_pass"] < 0.25      # no sizing rescues a bad edge


# ------------------------------------------------------------ guard stack

class _Risk:
    max_risk_per_trade_pct = 0.01


async def test_prop_layer_refuses_through_the_guard_stack():
    g = PropGuard(RULES)
    # prime on the REAL wall clock — the guard stack calls without `now`,
    # so a hardcoded past date would roll the firm day and reset the budget
    g.on_equity(10_000)

    equity_holder = {"eq": 9_650}                # 70% of daily budget consumed

    async def equity_fn():
        return equity_holder["eq"]

    guard = make_portfolio_guard(equity_fn=equity_fn, risk_limits=_Risk(),
                                 prop_guard=g)
    ok, why = await guard(object())
    assert not ok and why == "prop:daily_soft_stop"
    equity_holder["eq"] = 9_900                  # inside the soft line again
    ok, why = await guard(object())
    assert ok


async def test_guard_stack_without_prop_is_legacy():
    async def equity_fn():
        return 1_000_000.0

    guard = make_portfolio_guard(equity_fn=equity_fn, risk_limits=_Risk())
    ok, why = await guard(object())
    assert ok and why == "ok"
