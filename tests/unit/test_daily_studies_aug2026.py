"""Daily-OHLC study script (configs #29-32). Invariants:

  - sigma is CAUSAL: mutating bar i must not change the threshold used on
    bar i (the HONEST_INPUTS principle applied to a study)
  - both-breach days are skipped and counted, never guessed
  - fills at psi, exit at close, gross math correct for long and short
  - India cost model reproduces the config schedule; MT5 cost = full
    spread + two commissions
  - gap study demeans per symbol and the fade signs are Q1 long / Q5 short
  - bootstrap is seeded (reproducible)
"""
import math
import statistics

from scripts.study_daily_orb_gap import (bootstrap_ci,
                                         india_intraday_cost_pct,
                                         mt5_cost_pct, orb_trades,
                                         trailing_sigma)
from src.core.config_loader import load_config

CFG = load_config("config/master.yaml")


def mk_bars(n=40, px=100.0):
    """Deterministic bars with REAL variance (constant drift would give
    sigma=0 and no thresholds)."""
    bars = []
    for k in range(n):
        drift = 0.004 if k % 3 == 0 else (-0.002 if k % 3 == 1 else 0.001)
        o = px
        c = px * (1 + drift)
        # ALL bars dated before the report window — tests move exactly one
        # bar into the window, so exactly one bar can trade
        bars.append({"date": f"2026-01-{min(k + 1, 31):02d}",
                     "open": o, "high": max(o, c) * 1.002,
                     "low": min(o, c) * 0.998, "close": c})
        px = c
    return bars


class TestSigmaCausality:
    def test_bar_i_cannot_move_its_own_threshold(self):
        bars = mk_bars()
        i = 30
        s = trailing_sigma(bars, i)
        bars[i]["close"] *= 3.0
        bars[i]["high"] *= 3.0
        assert trailing_sigma(bars, i) == s

    def test_insufficient_history_returns_none(self):
        assert trailing_sigma(mk_bars(10), 9) is None


class TestOrbMechanics:
    def _bars_with_breakout(self, up=True):
        bars = mk_bars(30)
        b = bars[-1]
        b["date"] = "2026-03-01"                     # inside report window
        sig = trailing_sigma(bars, len(bars) - 1)
        psi = b["open"] * (1 + 1.0 * sig) if up else b["open"] * (1 - 1.0 * sig)
        if up:
            b["high"] = psi * 1.001
            b["close"] = psi * 1.0005
            b["low"] = b["open"] * 0.999
        else:
            b["low"] = psi * 0.999
            b["close"] = psi * 0.9995
            b["high"] = b["open"] * 1.001
        return bars, psi

    def test_long_fill_at_psi_exit_at_close(self):
        bars, psi = self._bars_with_breakout(up=True)
        trades, both = orb_trades("X", {"leg": "india"}, bars, 1.0, 0.0)
        assert both == 0 and len(trades) == 1
        t = trades[0]
        assert t["side"] == "long"
        assert math.isclose(t["gross"], bars[-1]["close"] / psi - 1, rel_tol=1e-9)

    def test_short_fill_and_sign(self):
        bars, psi = self._bars_with_breakout(up=False)
        trades, _ = orb_trades("X", {"leg": "india"}, bars, 1.0, 0.0)
        assert trades[0]["side"] == "short"
        assert trades[0]["gross"] > 0                # close fell below psi_dn

    def test_both_breach_skipped_and_counted(self):
        bars, psi = self._bars_with_breakout(up=True)
        b = bars[-1]
        sig = trailing_sigma(bars, len(bars) - 1)
        b["low"] = b["open"] * (1 - 1.0 * sig) * 0.999   # breach BOTH sides
        trades, both = orb_trades("X", {"leg": "india"}, bars, 1.0, 0.0)
        assert both == 1 and len(trades) == 0


class TestCostModels:
    def test_india_schedule_reproduced(self):
        n = 50_000
        c = CFG.execution_costs.india
        expected = (2 * c.brokerage_flat
                    + 2 * n * c.exchange_txn_pct
                    + n * c.stt_intraday_sell_pct
                    + n * c.stamp_duty_pct
                    + c.gst_pct * (2 * c.brokerage_flat
                                   + 2 * n * c.exchange_txn_pct)) / n
        assert math.isclose(india_intraday_cost_pct(CFG, n), expected)
        # flat brokerage means % cost FALLS with notional
        assert india_intraday_cost_pct(CFG, 500_000) < expected

    def test_mt5_cost_full_spread_plus_commissions(self):
        meta = {"half_spread": 0.00005, "commission_pct": 0.000035}
        got = mt5_cost_pct(meta, 1.10)
        assert math.isclose(got, 2 * 0.00005 / 1.10 + 2 * 0.000035)


class TestBootstrap:
    def test_seeded_and_reproducible(self):
        vals = [0.001, -0.002, 0.003, 0.0005, -0.001] * 8
        assert bootstrap_ci(vals) == bootstrap_ci(vals)

    def test_ci_brackets_mean(self):
        vals = [0.01] * 30 + [-0.005] * 10
        lo, hi = bootstrap_ci(vals)
        m = statistics.mean(vals)
        assert lo < m < hi
