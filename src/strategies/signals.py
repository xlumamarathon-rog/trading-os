"""MODULE 47 — Strategy Engine signal registry (Aug 2026).

The validated entry signals, promoted from scripts/research_replay.py into
production code. Every function has the same contract:

    signal(bars, i, regime) -> "buy" | "sell" | None

  bars    list of {"date","open","high","low","close"} dicts, oldest first
  i       index of the CURRENT bar (decisions use data up to bars[i-1] only —
          no lookahead; entries execute at bars[i]["open"])
  regime  {"trend_state": ..., "vol_regime": ..., ["trend_direction": ...]}

Validation status (real replays, walk-forward MODULE 32, holdout MODULE 25):
  tsmom / tsmom_f  walk-forward PASS on crypto (10/11 half-year segments
                   profitable 2020→2026); india edge is regime-specific
  accurate         high win rate (67-75%) in trending/normal regimes; loses
                   in crashes — pair with a trend sleeve, never solo
  baseline         the certified demo signal (SMA20, long-only)
"""
from __future__ import annotations

import os
from typing import Callable, Optional

# Martin-Luke ADR gate (percent). Faithful default is the doc's 5% "horse
# center"; overridable so the same signal can be probed on lower-volatility
# instruments (forex/large-cap india rarely clear 5%). Research-only knob.
MARTIN_ADR_MIN = float(os.environ.get("MARTIN_ADR_MIN", "5.0"))


# ---------- indicator helpers (no lookahead: use bars[:i] history) ----------

def sma(bars, i, n=20) -> Optional[float]:
    if i < n:
        return None
    return sum(b["close"] for b in bars[i - n:i]) / n


def mom(bars, i, n=63) -> Optional[float]:
    if i - 1 - n < 0:
        return None
    return bars[i - 1]["close"] / bars[i - 1 - n]["close"] - 1


def rsi(bars, i, n=2) -> Optional[float]:
    if i - 1 - n < 0:
        return None
    gains = losses = 0.0
    for k in range(i - n, i):
        d = bars[k]["close"] - bars[k - 1]["close"]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if gains + losses == 0:
        return 50.0
    return 100.0 * gains / (gains + losses)


def donchian(bars, i, n=20):
    if i - 1 - n < 0:
        return None, None
    window = bars[i - 1 - n:i - 1]
    return max(b["high"] for b in window), min(b["low"] for b in window)


def ema(bars, i, n) -> Optional[float]:
    """EMA of closes through bars[i-1] (no lookahead). Seeded with the SMA of
    the first n closes, then iterated forward."""
    if i < n:
        return None
    k = 2.0 / (n + 1)
    e = sum(b["close"] for b in bars[:n]) / n
    for j in range(n, i):
        e = bars[j]["close"] * k + e * (1 - k)
    return e


def adr_pct(bars, i, n=20) -> Optional[float]:
    """Average Daily Range as a percent of price over the last n COMPLETED
    bars (Martin Luke's 'horse center' filter). No lookahead."""
    if i < n:
        return None
    vals = []
    for k in range(i - n, i):
        c = bars[k]["close"]
        if c > 0:
            vals.append((bars[k]["high"] - bars[k]["low"]) / c)
    return 100.0 * sum(vals) / len(vals) if vals else None


def has_inside_day(bars, i, lookback=3) -> bool:
    """True if any of the last `lookback` completed bars is an inside day
    (range fully within the prior bar's range) — the volatility-contraction
    tell Martin Luke wants ahead of a breakout."""
    for k in range(max(1, i - lookback), i):
        if bars[k]["high"] <= bars[k - 1]["high"] and bars[k]["low"] >= bars[k - 1]["low"]:
            return True
    return False


def obv_rising(bars, i, n=10) -> Optional[bool]:
    """On-Balance-Volume slope over the last n bars. Needs a 'volume' key;
    returns None when the dataset is OHLC-only (as this repo's data is), so
    callers can degrade gracefully rather than silently gate everything out."""
    if i < n + 1 or "volume" not in bars[i - 1]:
        return None
    obv = 0.0
    series = []
    for k in range(i - n, i):
        d = bars[k]["close"] - bars[k - 1]["close"]
        obv += bars[k].get("volume", 0.0) * (1 if d > 0 else (-1 if d < 0 else 0))
        series.append(obv)
    return series[-1] >= series[0]


# ---------- signals ----------

def sig_baseline(bars, i, regime):
    s20 = sma(bars, i, 20)
    if s20 and bars[i - 1]["close"] > s20:
        return "buy"
    return None


def sig_tsmom(bars, i, regime):
    s50 = sma(bars, i, 50)
    m = mom(bars, i, 63)
    if s50 is None or m is None:
        return None
    c = bars[i - 1]["close"]
    if c > s50 and m > 0:
        return "buy"
    if c < s50 and m < 0:
        return "sell"
    return None


def sig_tsmom_f(bars, i, regime):
    """TSMOM + symmetric not-in-chop filter: only when price is meaningfully
    away from SMA20 in EITHER direction (>1%). No SHOCK entries."""
    if regime.get("vol_regime") == "SHOCK":
        return None
    s20 = sma(bars, i, 20)
    if s20 is None:
        return None
    dev = (bars[i - 1]["close"] - s20) / s20
    if abs(dev) < 0.01:
        return None
    return sig_tsmom(bars, i, regime)


def sig_donchian(bars, i, regime):
    if regime.get("vol_regime") == "SHOCK":
        return None
    hi, lo = donchian(bars, i, 20)
    if hi is None:
        return None
    c = bars[i - 1]["close"]
    if c > hi:
        return "buy"
    if c < lo:
        return "sell"
    return None


def sig_rsi2(bars, i, regime):
    s50 = sma(bars, i, 50)
    r = rsi(bars, i, 2)
    if s50 is None or r is None:
        return None
    if bars[i - 1]["close"] > s50 and r < 10:
        return "buy"
    return None


def sig_improved(bars, i, regime):
    if regime.get("vol_regime") == "SHOCK":
        return None
    s20, s50 = sma(bars, i, 20), sma(bars, i, 50)
    m = mom(bars, i, 63)
    if s20 is None or s50 is None or m is None:
        return None
    c = bars[i - 1]["close"]
    if c > s20 > s50 and m > 0:
        return "buy"
    if c < s20 < s50 and m < 0:
        return "sell"
    return None


def sig_improved2(bars, i, regime):
    if regime.get("vol_regime") == "SHOCK":
        return None
    s20 = sma(bars, i, 20)
    m = mom(bars, i, 21)
    if s20 is None or m is None:
        return None
    if bars[i - 1]["close"] > s20 and m > 0:
        return "buy"
    return None


def sig_improved3(bars, i, regime):
    if regime.get("vol_regime") == "SHOCK":
        return None
    s20, s50 = sma(bars, i, 20), sma(bars, i, 50)
    m = mom(bars, i, 21)
    if s20 is None or s50 is None or m is None:
        return None
    c = bars[i - 1]["close"]
    if c > s20 and c > s50 and m > 0:
        return "buy"
    return None


def sig_accurate(bars, i, regime):
    """Trend-aligned PULLBACK entry — the high-win-rate sleeve."""
    if regime.get("vol_regime") == "SHOCK" or regime.get("trend_state") == "RANGE":
        return None
    s50 = sma(bars, i, 50)
    m = mom(bars, i, 21)
    r = rsi(bars, i, 2)
    if s50 is None or m is None or r is None:
        return None
    c = bars[i - 1]["close"]
    if c > s50 and m > 0 and r < 25:
        return "buy"
    return None


def sig_accurate_ls(bars, i, regime):
    d = sig_accurate(bars, i, regime)
    if d:
        return d
    if regime.get("vol_regime") == "SHOCK":
        return None
    s50 = sma(bars, i, 50)
    m = mom(bars, i, 21)
    r = rsi(bars, i, 2)
    if s50 is None or m is None or r is None:
        return None
    c = bars[i - 1]["close"]
    if c < s50 and m < 0 and r > 75:
        return "sell"
    return None


def sig_martin_luke(bars, i, regime):
    """Martin Luke high-momentum swing breakout (daily approximation).

    Faithful pieces that ARE mechanizable on daily bars:
      - ADR filter: recent 20-bar ADR% >= MARTIN_ADR_MIN ('horse center')
      - trend structure: 9 EMA > 21 EMA > 50 EMA (long) / < < (short) — the
        'Leading tier' relative-strength stack
      - volatility contraction: an inside day within the last 3 bars
      - breakout trigger: prior completed bar closed above the bar-before's
        HIGH (range expansion up) — the daily stand-in for the intraday
        Prior-Day-High break; mirror (close below prior LOW) for shorts
    Intraday tactics (ORH, 5-min entry candle, VWAP), the 5% stop cap and the
    9-EMA trail are NOT modelled here — entries are tested inside the repo's
    standard 2xATR-stop + ExitManager framework, exactly like the controls.
    Volatility-seeking by design: SHOCK is not filtered out."""
    e9, e21, e50 = ema(bars, i, 9), ema(bars, i, 21), ema(bars, i, 50)
    if e9 is None or e21 is None or e50 is None:
        return None
    adr = adr_pct(bars, i, 20)
    if adr is None or adr < MARTIN_ADR_MIN:
        return None
    if not has_inside_day(bars, i, lookback=3):
        return None
    prev, prev2 = bars[i - 1], bars[i - 2]
    if e9 > e21 > e50 and prev["close"] > prev2["high"]:
        return "buy"
    if e9 < e21 < e50 and prev["close"] < prev2["low"]:
        return "sell"
    return None


def sig_18ma(bars, i, regime):
    """18-day MA qualified breakout / breakdown (long & short).

    Mechanized core from the doc:
      - two qualifying days (i-3, i-2) whose LOW is above their own 18-day MA
        (long) / whose HIGH is below it (short)
      - 'true high/low' = the extreme across those two days
      - trigger: the next completed bar (i-1) CLOSES beyond that level — the
        daily stand-in for 'price breaks the true high/low intraday'
      - OBV confirmation is applied ONLY if the data carries volume; this
        repo's bars are OHLC-only, so obv_rising() returns None and the gate
        is skipped (documented limitation, not a silent pass).
    The discretionary 'fundamental setup' precondition and the 18MA-flip
    structural stop are intentionally NOT modelled — this isolates the
    testable technical entry inside the repo's standard risk framework."""
    if i < 21:
        return None
    j1, j2 = i - 3, i - 2
    m1, m2 = sma(bars, j1, 18), sma(bars, j2, 18)
    if m1 is None or m2 is None:
        return None
    trig = bars[i - 1]
    obv = obv_rising(bars, i, 10)          # None when volume absent (this repo)

    if bars[j1]["low"] > m1 and bars[j2]["low"] > m2:
        true_high = max(bars[j1]["high"], bars[j2]["high"])
        if trig["close"] > true_high and obv is not False:
            return "buy"
    if bars[j1]["high"] < m1 and bars[j2]["high"] < m2:
        true_low = min(bars[j1]["low"], bars[j2]["low"])
        if trig["close"] < true_low and obv is not True:
            return "sell"
    return None


SIGNALS: dict[str, Callable] = {
    "baseline": sig_baseline, "tsmom": sig_tsmom, "tsmom_f": sig_tsmom_f,
    "donchian": sig_donchian, "rsi2": sig_rsi2, "improved": sig_improved,
    "improved2": sig_improved2, "improved3": sig_improved3,
    "accurate": sig_accurate, "accurate_ls": sig_accurate_ls,
    "martin_luke": sig_martin_luke, "18ma": sig_18ma,
}


def get_signal(name: str) -> Callable:
    if name not in SIGNALS:
        raise KeyError(f"unknown strategy '{name}' — known: {sorted(SIGNALS)}")
    return SIGNALS[name]
