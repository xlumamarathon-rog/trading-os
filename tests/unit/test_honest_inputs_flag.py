"""Aug 2026 execution audit, BUG-1 mitigation: the HONEST_INPUTS research
flag. Invariants:

  - default (flag off) leaves the harness exactly as certified — the flag
    resolves False and the risk inputs still include bar i (the documented
    legacy behaviour, kept so certified results stay byte-identical)
  - HONEST_INPUTS=1 resolves True, and lag-1 risk inputs are CAUSAL: mutating
    the fill bar's own H/L/C must not move the ATR or the regime used
  - the fetcher now keeps Yahoo's volume as an inert extra key (hygiene —
    audit §5): bars with and without "volume" flow through atr14 identically
"""
import json
import os
import subprocess
import sys

IMPORT_SNIPPET = (
    "import sys; sys.argv = ['rr', 'tsmom']; "
    "import scripts.research_replay as rr; print(rr.HONEST_INPUTS)"
)


def _flag_value(env_value):
    env = dict(os.environ)
    env.pop("HONEST_INPUTS", None)
    if env_value is not None:
        env["HONEST_INPUTS"] = env_value
    out = subprocess.run([sys.executable, "-c", IMPORT_SNIPPET],
                         capture_output=True, text=True, env=env,
                         cwd=".", check=True)
    return out.stdout.strip()


def test_flag_defaults_off():
    assert _flag_value(None) == "False"


def test_flag_enables():
    assert _flag_value("1") == "True"


def _mk_bars(n=40, base=100.0):
    bars = []
    px = base
    for k in range(n):
        bars.append({"date": f"2026-01-{k+1:02d}", "open": px,
                     "high": px * 1.01, "low": px * 0.99, "close": px * 1.005})
        px *= 1.002
    return bars


def test_lag1_inputs_are_causal():
    sys.argv = ["rr", "tsmom"]
    from scripts.research_replay import atr14, real_regime

    bars = _mk_bars()
    i = 30
    a_before = atr14(bars, i - 1)
    reg_before = real_regime(bars, i - 1)
    # mutate the FILL bar (i) violently — lag-1 inputs must not notice
    bars[i]["high"] *= 3.0
    bars[i]["low"] *= 0.3
    bars[i]["close"] *= 1.5
    assert atr14(bars, i - 1) == a_before
    assert real_regime(bars, i - 1) == reg_before
    # while the legacy (flag-off) inputs DO include bar i — the documented
    # lookahead the flag exists to remove
    assert atr14(bars, i) != a_before


def test_volume_key_is_inert_for_atr():
    sys.argv = ["rr", "tsmom"]
    from scripts.research_replay import atr14

    bars = _mk_bars()
    plain = atr14(bars, 30)
    with_vol = json.loads(json.dumps(bars))
    for b in with_vol:
        b["volume"] = 123456
    assert atr14(with_vol, 30) == plain
