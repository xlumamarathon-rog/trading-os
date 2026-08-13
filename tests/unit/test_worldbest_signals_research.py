"""research/worldbest-aug2026 — six documented high-win-rate candidates.
Trigger checks + the hard no-lookahead invariant."""
import pytest

from src.strategies.signals import (SIGNALS, sig_crsi, sig_dbl7, sig_ibs,
                                    sig_rsi2c, sig_rsi4x, sig_tom, wilder_rsi)


def bar(o, h, l, c, date="2026-06-10"):
    return {"open": o, "high": h, "low": l, "close": c, "date": date}


def trending_up(n, start=100.0, step=0.6):
    out, px = [], start
    for _ in range(n):
        out.append(bar(px, px + 1.0, px - 0.4, px + step))
        px += step
    return out


def test_wilder_rsi_extremes():
    up = trending_up(30)
    assert wilder_rsi(up, len(up), 2) > 90        # relentless gains
    down = [bar(100 - k, 101 - k, 99 - k, 100 - k - 0.5) for k in range(30)]
    assert wilder_rsi(down, len(down), 2) < 10


def test_rsi2c_buys_washout_in_uptrend():
    bars = trending_up(60)
    for k in range(3):                             # 3 hard down days
        last = bars[-1]["close"]
        bars.append(bar(last, last + 0.2, last - 3.2, last - 3.0))
    i = len(bars)
    assert bars[i - 1]["close"] > 100              # still above SMA50
    assert sig_rsi2c(bars + [bar(1, 1, 1, 1)], i, {}) == "buy"


def test_dbl7_buys_seven_day_low_in_uptrend():
    bars = trending_up(60)
    for k in range(7):                             # 7 gentle down closes
        last = bars[-1]["close"]
        bars.append(bar(last, last + 0.5, last - 1.0, last - 0.8))
    i = len(bars)
    assert sig_dbl7(bars + [bar(1, 1, 1, 1)], i, {}) == "buy"


def test_crsi_fires_on_two_deep_rsi_days():
    bars = trending_up(60)
    for k in range(3):
        last = bars[-1]["close"]
        bars.append(bar(last, last + 0.2, last - 3.2, last - 3.0))
    i = len(bars)
    assert sig_crsi(bars + [bar(1, 1, 1, 1)], i, {}) == "buy"


def test_rsi4x_short_side_below_regime_ma():
    bars = [bar(200 - k, 201 - k, 199 - k, 200 - k - 0.5) for k in range(60)]
    for k in range(4):                             # counter-trend pop
        last = bars[-1]["close"]
        bars.append(bar(last, last + 3.4, last - 0.2, last + 3.2))
    i = len(bars)
    assert sig_rsi4x(bars + [bar(1, 1, 1, 1)], i, {}) == "sell"


def test_ibs_buckets():
    weak = [bar(100, 105, 95, 95.5)]               # closed on the low: IBS .05
    assert sig_ibs(weak + [weak[0]] + [bar(1, 1, 1, 1)], 2, {}) == "buy"
    strong = [bar(100, 105, 95, 104.6)]            # closed on the high
    assert sig_ibs(strong + [strong[0]] + [bar(1, 1, 1, 1)], 2, {}) == "sell"
    flat = [bar(100, 100, 100, 100)]               # zero range -> no signal
    assert sig_ibs(flat + [flat[0]] + [bar(1, 1, 1, 1)], 2, {}) is None


def test_tom_window_by_calendar():
    inside = [bar(1, 2, 0.5, 1, "2026-06-26")] * 2
    outside = [bar(1, 2, 0.5, 1, "2026-06-15")] * 2
    assert sig_tom(inside + [bar(1, 1, 1, 1)], 2, {}) == "buy"
    assert sig_tom(outside + [bar(1, 1, 1, 1)], 2, {}) is None


CANDS = ["rsi2c", "dbl7", "crsi", "rsi4x", "ibs", "tom"]


@pytest.mark.parametrize("name", CANDS)
def test_no_lookahead(name):
    """Mutating bars[i:] into absurdity must never change the verdict."""
    fn = SIGNALS[name]
    base = trending_up(60)
    for k in range(3):
        last = base[-1]["close"]
        base.append(bar(last, last + 0.2, last - 3.2, last - 3.0, "2026-06-26"))
    i = len(base)
    a = fn(base + [bar(1, 2, 0.5, 1)], i, {})
    b = fn(base + [bar(1e9, 1e9, -1e9, -1e9)], i, {})
    assert a == b


@pytest.mark.parametrize("name", CANDS)
def test_short_history_safe(name):
    fn = SIGNALS[name]
    for i in range(0, 3):
        assert fn([bar(1, 2, 0.5, 1)] * (i + 1), i, {}) in (None, "buy", "sell")
