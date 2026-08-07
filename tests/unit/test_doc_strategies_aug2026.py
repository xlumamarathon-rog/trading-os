"""Unit tests for the two document-sourced entry signals (Aug 2026 research):
martin_luke (high-momentum breakout) and 18ma (qualified MA breakout).

Contract checks only — no-lookahead, correct buy/sell/None on crafted bars.
Real performance is measured separately via scripts/research_replay.py.
"""
import pytest

from src.strategies import SIGNALS, get_signal
from src.strategies import signals as S

REGIME = {"trend_state": "STRONG_TREND", "vol_regime": "NORMAL"}


def test_registry_has_both_new_strategies():
    assert "martin_luke" in SIGNALS and "18ma" in SIGNALS


# ---------- martin_luke ----------

def _martin_buy_bars():
    """Rising, high-ADR (~6%) series; last 3 bars carry an inside day then a
    range-expansion breakout close above the prior bar's high."""
    bars = []
    px = 100.0
    for k in range(57):
        px *= 1.02
        bars.append({"date": f"d{k}", "open": px * 0.99, "high": px * 1.03,
                     "low": px * 0.97, "close": px})
    b57 = bars[56]
    # bar 57: normal continuation
    bars.append({"date": "d57", "open": b57["close"], "high": b57["close"] * 1.03,
                 "low": b57["close"] * 0.985, "close": b57["close"] * 1.01})
    # bar 58: INSIDE day vs bar 57 (range fully within)
    b58ref = bars[57]
    bars.append({"date": "d58", "open": b58ref["close"], "high": b58ref["high"] - 0.5,
                 "low": b58ref["low"] + 0.5, "close": b58ref["close"]})
    # bar 59: breakout — close above bar 58's high
    bars.append({"date": "d59", "open": bars[58]["close"], "high": bars[58]["high"] * 1.05,
                 "low": bars[58]["low"], "close": bars[58]["high"] * 1.04})
    # bar 60: the entry bar (never read by the signal)
    bars.append({"date": "d60", "open": bars[59]["close"], "high": bars[59]["close"] * 1.02,
                 "low": bars[59]["close"] * 0.99, "close": bars[59]["close"] * 1.01})
    return bars


def test_martin_luke_fires_buy_on_uptrend_breakout():
    bars = _martin_buy_bars()
    assert get_signal("martin_luke")(bars, 60, REGIME) == "buy"


def test_martin_luke_blocked_by_adr_filter(monkeypatch):
    bars = _martin_buy_bars()
    monkeypatch.setattr(S, "MARTIN_ADR_MIN", 99.0)   # no instrument clears 99% ADR
    assert get_signal("martin_luke")(bars, 60, REGIME) is None


def test_martin_luke_no_lookahead():
    bars = _martin_buy_bars()
    # replacing the entry bar (index 60) with garbage must not change the call
    wild = dict(bars[60], high=9e9, low=-9e9, close=9e9)
    mutated = bars[:60] + [wild]
    assert (get_signal("martin_luke")(bars, 60, REGIME)
            == get_signal("martin_luke")(mutated, 60, REGIME) == "buy")


# ---------- 18ma ----------

def _flat_then(*tail):
    """18 flat bars at 100 (18MA ~ 100), then the crafted tail bars."""
    bars = [{"date": f"f{k}", "open": 100, "high": 101, "low": 99, "close": 100}
            for k in range(18)]
    bars.extend(tail)
    return bars


def test_18ma_fires_buy_on_qualified_breakout():
    # two qualifying days with LOW above the 18MA (~100), then a close > true high
    bars = _flat_then(
        {"date": "q1", "open": 105, "high": 108, "low": 105, "close": 106},   # idx 18
        {"date": "q2", "open": 106, "high": 109, "low": 106, "close": 107},   # idx 19
        {"date": "br", "open": 108, "high": 111, "low": 108, "close": 110},   # idx 20 close > 109
        {"date": "en", "open": 110, "high": 112, "low": 109, "close": 111},   # idx 21 entry bar
    )
    assert get_signal("18ma")(bars, 21, REGIME) == "buy"


def test_18ma_fires_sell_on_qualified_breakdown():
    bars = _flat_then(
        {"date": "q1", "open": 95, "high": 95, "low": 92, "close": 93},       # high < 18MA
        {"date": "q2", "open": 94, "high": 94, "low": 91, "close": 92},
        {"date": "br", "open": 92, "high": 92, "low": 89, "close": 90},       # close < true low 91
        {"date": "en", "open": 90, "high": 91, "low": 88, "close": 89},
    )
    assert get_signal("18ma")(bars, 21, REGIME) == "sell"


def test_18ma_no_trade_without_breakout():
    bars = _flat_then(
        {"date": "q1", "open": 105, "high": 108, "low": 105, "close": 106},
        {"date": "q2", "open": 106, "high": 109, "low": 106, "close": 107},
        {"date": "no", "open": 107, "high": 108.5, "low": 106, "close": 108},  # 108 < true high 109
        {"date": "en", "open": 108, "high": 109, "low": 107, "close": 108},
    )
    assert get_signal("18ma")(bars, 21, REGIME) is None


def test_18ma_no_lookahead():
    bars = _flat_then(
        {"date": "q1", "open": 105, "high": 108, "low": 105, "close": 106},
        {"date": "q2", "open": 106, "high": 109, "low": 106, "close": 107},
        {"date": "br", "open": 108, "high": 111, "low": 108, "close": 110},
        {"date": "en", "open": 110, "high": 112, "low": 109, "close": 111},
    )
    wild = dict(bars[21], high=9e9, low=-9e9, close=-9e9)
    mutated = bars[:21] + [wild]
    assert get_signal("18ma")(mutated, 21, REGIME) == "buy"


def test_obv_gate_skipped_when_volume_absent():
    # OHLC-only bars -> obv_rising returns None -> gate does not block the buy
    assert S.obv_rising([{"close": 1} for _ in range(20)], 15, 10) is None
