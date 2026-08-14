"""EXIT-STYLE experiment mechanism (configs #33-34). Invariants:

  - signal exits are CAUSAL: the verdict for bar i reads bars[:i] only —
    mutating bars[i:] must never change it (same contract as entries)
  - "reverse" wants out only on a genuine OPPOSITE signal
  - "ma5" is the Connors exit: prev close beyond SMA5(completed), mirrored
    for shorts
  - off by default; unknown modes fail loud
"""
import sys

import pytest

sys.argv = ["rr", "tsmom"]
from scripts.research_replay import signal_exit_wanted  # noqa: E402

REG = {"trend_state": "RANGE", "vol_regime": "NORMAL", "trend_direction": "FLAT"}


def bars_up(n=40, px=100.0):
    out = []
    for k in range(n):
        drift = 0.006 if k % 4 else -0.002
        o = px
        c = px * (1 + drift)
        out.append({"date": f"d{k}", "open": o, "high": max(o, c) * 1.001,
                    "low": min(o, c) * 0.999, "close": c})
        px = c
    return out


class TestCausality:
    def test_future_bars_cannot_change_the_verdict(self):
        bars = bars_up()
        i = 30
        verdict = signal_exit_wanted("ma5", None, bars, i, REG, "buy")
        bars[i]["close"] *= 5.0                 # absurd future
        bars[i + 1]["close"] *= 0.1
        assert signal_exit_wanted("ma5", None, bars, i, REG, "buy") == verdict

    def test_reverse_mode_is_causal_via_signal_contract(self):
        # entry signals are already lookahead-proven; reverse reuses them
        calls = []

        def spy_signal(bars, i, regime):
            calls.append(i)
            return "sell"

        bars = bars_up()
        assert signal_exit_wanted("reverse", spy_signal, bars, 30, REG, "buy")
        assert calls == [30]                    # decision index, bars[:i] contract


class TestModes:
    def test_reverse_only_on_opposite(self):
        bars = bars_up()
        assert signal_exit_wanted("reverse", lambda *a: "sell", bars, 30, REG, "buy")
        assert not signal_exit_wanted("reverse", lambda *a: "buy", bars, 30, REG, "buy")
        assert not signal_exit_wanted("reverse", lambda *a: None, bars, 30, REG, "buy")

    def test_ma5_long_exit_and_short_mirror(self):
        bars = bars_up()
        i = 30
        s5 = sum(b["close"] for b in bars[i - 5:i]) / 5
        bars[i - 1]["close"] = s5 * 1.01
        assert signal_exit_wanted("ma5", None, bars, i, REG, "buy")
        assert not signal_exit_wanted("ma5", None, bars, i, REG, "sell")
        bars[i - 1]["close"] = s5 * 0.99
        assert not signal_exit_wanted("ma5", None, bars, i, REG, "buy")
        assert signal_exit_wanted("ma5", None, bars, i, REG, "sell")

    def test_off_and_unknown(self):
        bars = bars_up()
        assert not signal_exit_wanted("", None, bars, 30, REG, "buy")
        with pytest.raises(ValueError):
            signal_exit_wanted("nope", None, bars, 30, REG, "buy")
