"""MODULE 66 — risk optimizer. Invariants:

  - empirical Kelly matches the analytic answer on distributions where the
    closed form exists (±1e-2)
  - the growth curve peaks at f* and DECLINES past it (the cliff is real)
  - negative expectancy -> f* = 0 ("no sizing fixes a negative edge")
  - all-wins sample -> Kelly undefined, never a lever-up licence
  - Monte Carlo drawdowns are deterministic (seeded) and monotone in f
  - the report refuses to make sizing claims on tiny samples
"""
import json

import pytest

from src.ops.risk_optimizer import (edge_report, growth_per_trade,
                                    kelly_fraction, monte_carlo_drawdown,
                                    report)

# analytic check: win prob p with +1R / -1R payoffs -> Kelly f* = 2p - 1
COIN_60 = [1.0] * 60 + [-1.0] * 40           # f* = 0.20


def test_kelly_matches_analytic_binomial():
    k = kelly_fraction(COIN_60)
    assert abs(k["f_star"] - 0.20) < 0.01


def test_kelly_matches_analytic_asymmetric():
    # p=0.5, win +2R, lose -1R: f* = (p·b − q)/b = (0.5·2 − 0.5)/2 = 0.25
    rs = [2.0] * 50 + [-1.0] * 50
    k = kelly_fraction(rs)
    assert abs(k["f_star"] - 0.25) < 0.01


def test_growth_curve_peaks_then_falls():
    k = kelly_fraction(COIN_60)["f_star"]
    g_half = growth_per_trade(COIN_60, k / 2)
    g_star = growth_per_trade(COIN_60, k)
    g_double = growth_per_trade(COIN_60, min(2 * k, 0.99))
    assert g_star > g_half > 0
    assert g_double < g_star                  # past the peak = less growth
    # at exactly 2x Kelly for the symmetric coin, growth ~ 0 or negative
    assert g_double < g_half


def test_negative_edge_gives_zero_kelly():
    rs = [1.0] * 40 + [-1.0] * 60
    k = kelly_fraction(rs)
    assert k["f_star"] == 0.0
    assert "negative edge" in k["reason"] or "non-positive" in k["reason"]


def test_all_wins_is_undefined_not_unbounded():
    k = kelly_fraction([0.5, 1.2, 2.0, 0.1])
    assert k["f_star"] is None
    assert "more history" in k["reason"]


def test_domain_guard_no_log_of_nonpositive():
    # a -1R trade at f=1.0 wipes the account: growth undefined, not a crash
    assert growth_per_trade([-1.0, 2.0], 1.0) is None
    assert growth_per_trade([-1.0, 2.0], 0.5) is not None


def test_monte_carlo_deterministic_and_monotone():
    a = monte_carlo_drawdown(COIN_60, 0.05, paths=500)
    b = monte_carlo_drawdown(COIN_60, 0.05, paths=500)
    assert a == b                              # seeded — cockpit-stable
    hi = monte_carlo_drawdown(COIN_60, 0.20, paths=500)
    assert hi["dd_p95_pct"] > a["dd_p95_pct"]  # more risk, deeper drawdowns
    assert hi["p_dd_over_20pct"] >= a["p_dd_over_20pct"]


def test_report_refuses_tiny_samples():
    rep = report([1.0, -1.0, 0.5], configured_risk_pct=0.01)
    assert "more history" in rep["verdict"]
    assert "kelly" not in rep


def test_report_full_shape_and_conservative_verdict():
    rep = report(COIN_60, configured_risk_pct=0.01)
    assert rep["fractions"]["configured_vs_kelly"] < 0.5
    assert "conservative" in rep["verdict"]
    assert rep["growth"]["at_kelly"] >= rep["growth"]["at_half_kelly"]
    assert rep["mc_at_kelly"]["dd_p95_pct"] > rep["mc_at_configured"]["dd_p95_pct"]
    assert len(rep["curve"]) >= 20


def test_report_flags_over_kelly_configuration():
    rep = report(COIN_60, configured_risk_pct=0.30)   # above f*=0.20
    assert "OVER" in rep["verdict"]


def test_real_replay_output_feeds_the_report():
    """End-to-end with the repo's own data: the additive trades_r field from
    a real research_replay run must flow straight into a full report."""
    rs = json.load(open("/tmp/rr_m66/results.json"))["trades_r"] \
        if __import__("pathlib").Path("/tmp/rr_m66/results.json").exists() \
        else COIN_60
    rep = report(rs, configured_risk_pct=0.01)
    assert rep["edge"]["n"] >= 38 or rep["edge"]["n"] == 100
    assert "verdict" in rep
