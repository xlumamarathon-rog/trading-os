"""Tests for the OpenBB-gap modules: technical_analysis + fundamental_analysis.

Deterministic checks plus, where available, a cross-check of Wilder RSI against
pandas-ta/ta-lib-style reference (skips cleanly if not installed).
"""
import pytest

from src.intel import fundamental_analysis as FA
from src.intel import technical_analysis as TA


# ---------- technical: primitives & studies ----------

def test_ema_series_tracks_constant():
    assert TA.ema_series([5.0] * 30, 10)[-1] == pytest.approx(5.0)


def test_bollinger_on_flat_series_collapses_bands():
    b = TA.bollinger_bands([100.0] * 25, n=20).values
    assert b["upper"] == pytest.approx(100.0) and b["lower"] == pytest.approx(100.0)
    assert b["pct_b"] == pytest.approx(0.5)


def test_macd_line_positive_in_uptrend():
    closes = [100 + i for i in range(60)]           # steady uptrend
    m = TA.macd(closes).values
    # fast EMA sits above slow in an uptrend -> MACD line > 0. (Histogram ~0 on a
    # PERFECTLY linear ramp: constant slope = no momentum acceleration.)
    assert m["macd"] is not None and m["macd"] > 0
    assert m["histogram"] == pytest.approx(0.0, abs=1e-9)


def test_macd_histogram_turns_positive_on_acceleration():
    closes = [100 + i for i in range(40)] + [140 + 3 * i for i in range(20)]  # slope steepens
    assert TA.macd(closes).values["histogram"] > 0


def test_macd_insufficient_history_warns():
    s = TA.macd([1, 2, 3])
    assert not s.ok and s.values["macd"] is None


def test_wilder_rsi_all_gains_is_100():
    closes = [100 + i for i in range(30)]
    assert TA.wilder_rsi(closes, 14).values["rsi"] == pytest.approx(100.0)


def test_wilder_rsi_midrange_on_alternating():
    # alternating +1/-1 steps -> avg gain == avg loss -> RSI 50
    closes = [100.0]
    for i in range(40):
        closes.append(closes[-1] + (1 if i % 2 == 0 else -1))
    rsi = TA.wilder_rsi(closes, 14).values["rsi"]
    assert 45 <= rsi <= 55


def test_stochastic_at_top_of_range_is_high():
    highs = [10 + i for i in range(20)]
    lows = [9 + i for i in range(20)]
    closes = [10 + i for i in range(20)]            # closing at the highs
    k = TA.stochastic(highs, lows, closes).values["k"]
    assert k is not None and k > 90


def test_adx_strong_in_clean_trend():
    highs = [100 + i for i in range(40)]
    lows = [99 + i for i in range(40)]
    closes = [99.5 + i for i in range(40)]
    a = TA.adx(highs, lows, closes).values["adx"]
    assert a is not None and a > 40                 # persistent one-way move -> high ADX


def test_obv_accumulates_on_up_days_and_needs_volume():
    closes = [10, 11, 10, 12]
    vols = [100, 200, 150, 300]
    # +200 (up) -150 (down) +300 (up) = 350
    assert TA.obv(closes, vols).values["obv"] == pytest.approx(350)
    assert not TA.obv([1, 2, 3], []).ok           # mismatched -> warned, not crash


def test_analyze_bundle_has_provenance_and_read():
    bars = [{"open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100 + i,
             "volume": 1000} for i in range(60)]
    out = TA.analyze(bars)
    assert out["source"] == TA.SOURCE
    assert out["read"] in ("bullish", "bearish", "neutral")
    assert "macd" in out["studies"] and "warnings" in out["studies"]["macd"]


@pytest.mark.parametrize("n", [14])
def test_wilder_rsi_matches_reference_if_available(n):
    ta = pytest.importorskip("pandas_ta")
    import pandas as pd
    closes = [100, 102, 101, 105, 107, 106, 110, 108, 112, 115,
              113, 118, 120, 119, 123, 121, 125, 128, 126, 130]
    ours = TA.wilder_rsi(closes, n).values["rsi"]
    ref = ta.rsi(pd.Series(closes), length=n).dropna().iloc[-1]
    assert ours == pytest.approx(float(ref), abs=1e-6)


# ---------- fundamental ----------

_FIN = {
    "price": 100.0, "eps": 5.0, "book_value_per_share": 40.0,
    "net_income": 200.0, "equity": 1000.0, "total_assets": 2500.0,
    "total_debt": 600.0, "current_assets": 800.0, "current_liabilities": 400.0,
    "gross_profit": 500.0, "revenue": 1200.0, "ebit": 350.0,
    "interest_expense": 50.0, "free_cash_flow": 90.0, "market_cap": 1800.0,
}


def test_ratios_are_correct():
    r = FA.ratios_from(_FIN)
    assert r["pe"] == pytest.approx(20.0)
    assert r["pb"] == pytest.approx(2.5)
    assert r["roe"] == pytest.approx(0.20)
    assert r["debt_to_equity"] == pytest.approx(0.60)
    assert r["current_ratio"] == pytest.approx(2.0)
    assert r["interest_coverage"] == pytest.approx(7.0)


def test_missing_inputs_yield_none_not_zero():
    r = FA.ratios_from({"price": 100.0})            # no eps
    assert r["pe"] is None and r["roe"] is None


def test_health_score_strong_company_scores_high():
    rep = FA.analyze("GOOD", _FIN)
    assert rep.score is not None and rep.score >= 80
    assert rep.coverage == 6 and "strong_roe" in rep.flags


def test_health_score_weak_company_scores_low():
    weak = dict(_FIN, net_income=-50.0, total_debt=3000.0, ebit=10.0,
                free_cash_flow=-20.0, current_assets=200.0)
    rep = FA.analyze("WEAK", weak)
    assert rep.score is not None and rep.score <= 30
    assert any(f.startswith("weak_") for f in rep.flags)


def test_analyze_via_provider_protocol():
    class FakeProvider:
        def fetch(self, symbol): return _FIN
    p = FakeProvider()
    assert isinstance(p, FA.FundamentalProvider)     # runtime Protocol check
    rep = FA.analyze("AAPL", provider=p)
    assert rep.score is not None and rep.ratios["pe"] == pytest.approx(20.0)


def test_analyze_fails_soft_without_data():
    assert FA.analyze("X").warnings == ["no_data_and_no_provider"]

    class Boom:
        def fetch(self, s): raise RuntimeError("feed down")
    rep = FA.analyze("X", provider=Boom())
    assert rep.score is None and rep.warnings[0].startswith("provider_error:")
