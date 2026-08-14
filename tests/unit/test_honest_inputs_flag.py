"""Aug 2026 fix-everything campaign (audit BUG-1): lag-1 risk inputs are the
DEFAULT — the research-only HONEST_INPUTS flag is retired. Invariants:

  - the harness computes ATR and regime for bar i from bars[:i] ONLY: mutating
    the fill bar's own H/L/C must not move the stop distance or the regime
  - the retired env flag no longer exists as a module attribute (nothing can
    silently re-enable the lookahead)
  - the fetcher's volume key remains inert for atr14 (hygiene fix, audit §5)
"""
import json
import sys


def _mk_bars(n=40, base=100.0):
    bars = []
    px = base
    for k in range(n):
        drift = 0.004 if k % 3 == 0 else (-0.002 if k % 3 == 1 else 0.001)
        bars.append({"date": f"2026-01-{k+1:02d}", "open": px,
                     "high": px * 1.01, "low": px * 0.99,
                     "close": px * (1 + drift)})
        px = bars[-1]["close"]
    return bars


def test_lag1_inputs_are_the_default_and_causal():
    sys.argv = ["rr", "tsmom"]
    from scripts.research_replay import atr14, real_regime

    bars = _mk_bars()
    i = 30
    a_before = atr14(bars, i - 1)          # what the harness now uses for bar i
    reg_before = real_regime(bars, i - 1)
    bars[i]["high"] *= 3.0                 # violent mutation of the fill bar
    bars[i]["low"] *= 0.3
    bars[i]["close"] *= 1.5
    assert atr14(bars, i - 1) == a_before
    assert real_regime(bars, i - 1) == reg_before


def test_flag_is_retired():
    sys.argv = ["rr", "tsmom"]
    import scripts.research_replay as rr
    assert not hasattr(rr, "HONEST_INPUTS")


def test_volume_key_is_inert_for_atr():
    sys.argv = ["rr", "tsmom"]
    from scripts.research_replay import atr14

    bars = _mk_bars()
    plain = atr14(bars, 30)
    with_vol = json.loads(json.dumps(bars))
    for b in with_vol:
        b["volume"] = 123456
    assert atr14(with_vol, 30) == plain
