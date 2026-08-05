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

from typing import Callable, Optional


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


SIGNALS: dict[str, Callable] = {
    "baseline": sig_baseline, "tsmom": sig_tsmom, "tsmom_f": sig_tsmom_f,
    "donchian": sig_donchian, "rsi2": sig_rsi2, "improved": sig_improved,
    "improved2": sig_improved2, "improved3": sig_improved3,
    "accurate": sig_accurate, "accurate_ls": sig_accurate_ls,
}


def get_signal(name: str) -> Callable:
    if name not in SIGNALS:
        raise KeyError(f"unknown strategy '{name}' — known: {sorted(SIGNALS)}")
    return SIGNALS[name]
