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


# ---------------------------------------------------------------------------
# WCC-lineage research candidates (research/wcc-williams-aug2026, 2026-08-12).
# Sources: Larry Williams' PUBLISHED rules (ledger 2026-08-12 research entry).
# HONEST DEVIATION NOTE: Williams' originals are intraday STOP-ENTRY systems
# (buy stop at Open_t + offset, filled mid-bar). This engine's certified
# contract enters at bars[i]["open"] using bars[:i] only — so each candidate
# is the close-confirmation daily-bar ADAPTATION: it enters at TODAY's open
# when YESTERDAY's close proves the published trigger fired and held. Exits
# are the standard certified ExitManager (chandelier/breakeven/partials),
# NOT Williams' first-profitable-open bailout — the question under test is
# whether these ENTRY rules add edge inside OUR system, apples-to-apples
# with the other ten sleeves. UNFILTERED base rules only (Radge 2015: the
# base rules survived 17y out-of-sample; the book's day-filters did not).


def sig_vbo(bars, i, regime):
    """Volatility breakout (The Definitive Guide to Futures Trading):
    published trigger Open_t ± k·Range_{t-1}, k published 0.9–1.1 → k=1.0.
    Adaptation: yesterday closed beyond ITS open ± k·(range of the day
    before) → enter at today's open in that direction."""
    if i < 3:
        return None
    k = 1.0
    y, p = bars[i - 1], bars[i - 2]
    rng = p["high"] - p["low"]
    if rng <= 0:
        return None
    if y["close"] > y["open"] + k * rng:
        return "buy"
    if y["close"] < y["open"] - k * rng:
        return "sell"
    return None


def sig_oops(bars, i, regime):
    """Oops! gap reversal (Long-Term Secrets ch.7 p.113): open gaps below
    the prior day's low → buy stop AT the prior low (mirror for gap-up).
    Adaptation: yesterday gapped open beyond the prior day's extreme and
    closed back through it (the published stop would have filled and
    finished onside) → enter at today's open with the reversal."""
    if i < 3:
        return None
    y, p = bars[i - 1], bars[i - 2]
    if y["open"] < p["low"] and y["close"] > p["low"]:
        return "buy"
    if y["open"] > p["high"] and y["close"] < p["high"]:
        return "sell"
    return None


def sig_gsv(bars, i, regime):
    """Greatest Swing Value (Long-Term Secrets ch.8, fully specified):
    BuySwing_t = H−O on down closes, SellSwing_t = O−L on up closes;
    trigger at Open + v·SZMA(swing, n) (SZMA = mean skipping zeros).
    Inputs n=4, v=1.8 (the commonly published Sierra defaults — Williams
    left them free). Adaptation: yesterday closed beyond its open +
    v·SZMA computed STRICTLY on the n bars before yesterday."""
    n, v = 4, 1.8
    if i < n + 3:
        return None
    window = bars[i - 1 - n:i - 1]
    y = bars[i - 1]
    buy_swings = [b["high"] - b["open"] for b in window if b["close"] < b["open"]]
    sell_swings = [b["open"] - b["low"] for b in window if b["close"] > b["open"]]
    if buy_swings:
        gsv_b = sum(buy_swings) / len(buy_swings)
        if gsv_b > 0 and y["close"] > y["open"] + v * gsv_b:
            return "buy"
    if sell_swings:
        gsv_s = sum(sell_swings) / len(sell_swings)
        if gsv_s > 0 and y["close"] < y["open"] - v * gsv_s:
            return "sell"
    return None


SIGNALS: dict[str, Callable] = {
    "baseline": sig_baseline, "tsmom": sig_tsmom, "tsmom_f": sig_tsmom_f,
    "donchian": sig_donchian, "rsi2": sig_rsi2, "improved": sig_improved,
    "improved2": sig_improved2, "improved3": sig_improved3,
    "accurate": sig_accurate, "accurate_ls": sig_accurate_ls,
    "vbo": sig_vbo, "oops": sig_oops, "gsv": sig_gsv,
}


def get_signal(name: str) -> Callable:
    if name not in SIGNALS:
        raise KeyError(f"unknown strategy '{name}' — known: {sorted(SIGNALS)}")
    return SIGNALS[name]
