"""Research branch wcc-williams-aug2026 — signal math for the three
Williams adaptations. Includes a hard no-lookahead check: a signal must be
invariant to ANY mutation of bars[i:] (the engine contract)."""
import pytest

from src.strategies.signals import SIGNALS, sig_gsv, sig_oops, sig_vbo


def bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


FLAT = [bar(100, 101, 99, 100) for _ in range(10)]


# ---------------------------------------------------------------- vbo

def test_vbo_buy_on_close_beyond_prior_range():
    bars = FLAT[:5] + [bar(100, 103.5, 99.9, 103.0)]  # closed > open + 1.0*(101-99)
    assert sig_vbo(bars + [bar(103, 104, 102, 103)], len(bars) + 0, {}) == "buy"


def test_vbo_sell_mirror():
    bars = FLAT[:5] + [bar(100, 100.1, 96.5, 97.0)]   # closed < open - range
    assert sig_vbo(bars + [bar(97, 98, 96, 97)], len(bars), {}) == "sell"


def test_vbo_quiet_day_no_signal():
    bars = FLAT[:6]
    assert sig_vbo(bars + [bar(100, 101, 99, 100)], len(bars), {}) is None


# ---------------------------------------------------------------- oops

def test_oops_buy_gap_down_reversal():
    prior = bar(100, 101, 99, 100)
    yday = bar(98.0, 100.2, 97.8, 99.6)               # opened < 99, closed > 99
    bars = FLAT[:4] + [prior, yday]
    assert sig_oops(bars + [bar(99.6, 100, 99, 99.8)], len(bars), {}) == "buy"


def test_oops_sell_gap_up_reversal():
    prior = bar(100, 101, 99, 100)
    yday = bar(102.0, 102.2, 100.4, 100.6)            # opened > 101, closed < 101
    bars = FLAT[:4] + [prior, yday]
    assert sig_oops(bars + [bar(100.6, 101, 100, 100.5)], len(bars), {}) == "sell"


def test_oops_gap_that_never_recovers_is_no_signal():
    prior = bar(100, 101, 99, 100)
    yday = bar(98.0, 98.6, 97.0, 97.4)                # gapped down, STAYED down
    bars = FLAT[:4] + [prior, yday]
    assert sig_oops(bars + [bar(97.4, 98, 97, 97.5)], len(bars), {}) is None


# ---------------------------------------------------------------- gsv

def test_gsv_buy_on_swing_expansion():
    # window: down-closes with H-O = 1.0 -> SZMA(BuySwing)=1.0; v=1.8
    window = [bar(100, 101, 98.5, 99) for _ in range(4)]
    yday = bar(100, 102.5, 99.9, 102.0)               # close > open + 1.8*1.0
    bars = FLAT[:4] + window + [yday]
    assert sig_gsv(bars + [bar(102, 103, 101, 102)], len(bars), {}) == "buy"


def test_gsv_no_signal_inside_swing_envelope():
    window = [bar(100, 101, 98.5, 99) for _ in range(4)]
    yday = bar(100, 101.5, 99.9, 101.0)               # close < open + 1.8
    bars = FLAT[:4] + window + [yday]
    assert sig_gsv(bars + [bar(101, 102, 100, 101)], len(bars), {}) is None


# ------------------------------------------------- contract invariants

CANDIDATES = ["vbo", "oops", "gsv"]


@pytest.mark.parametrize("name", CANDIDATES)
def test_registered_and_short_history_safe(name):
    fn = SIGNALS[name]
    for i in range(0, 3):
        assert fn(FLAT[:i] + [FLAT[0]], i, {}) is None


@pytest.mark.parametrize("name", CANDIDATES)
def test_no_lookahead_bars_i_and_beyond_never_matter(name):
    """The engine contract: decisions use bars[:i] ONLY. Mutating bars[i:]
    into absurdity must not change the verdict."""
    fn = SIGNALS[name]
    base = (FLAT[:4] + [bar(100, 101, 99, 100), bar(98.0, 100.2, 97.8, 99.6),
                        bar(100, 103.5, 99.9, 103.0)])
    i = len(base) - 0
    bars_a = base + [bar(103, 104, 102, 103)]
    bars_b = base + [bar(1e9, 1e9, -1e9, -1e9)]       # absurd future
    assert fn(bars_a, i, {}) == fn(bars_b, i, {})
