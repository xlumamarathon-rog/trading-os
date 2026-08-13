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


# ---------------------------------------------------------------------------
# World-best research candidates (research/worldbest-aug2026, 2026-08-13).
# Sources: Connors & Alvarez "Short Term Trading Strategies That Work" (2008)
# + "High Probability ETF Trading" (2009) with post-publication replications;
# Pagonidis "The IBS Effect" (NAAIM 2014); McConnell & Xu TOM (FAJ 2008) +
# India studies (Maher & Parikh 2013). Full provenance in the ledger.
# HONEST ADAPTATIONS (all documented, all disclosed):
#   - Connors enters/exits ON THE CLOSE with no stops; this engine enters at
#     next OPEN and exits via the certified ExitManager (stops/partials/
#     trails/time). Published 70-88% win rates are an artifact of the
#     hold-until-recovery exit — expect materially different numbers here.
#     The question under test: do these ENTRY rules add edge in OUR system.
#   - The published 200-day MA regime filter cannot exist on a 6-month
#     dataset (about 65 warmup bars) -> SMA(50) stands in for it.
#   - Connors' RSI is WILDER-smoothed (not the engine's Cutler rsi()) —
#     implemented faithfully below.


def wilder_rsi(bars, i, n=2) -> Optional[float]:
    """Wilder-smoothed RSI of closes over bars[:i] (last completed bar)."""
    lo = max(0, i - n - 60)                    # 60 extra bars to converge
    closes = [b["close"] for b in bars[lo:i]]
    if len(closes) < n + 2:
        return None
    gains, losses = [], []
    for k in range(1, len(closes)):
        d = closes[k] - closes[k - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for k in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[k]) / n
        al = (al * (n - 1) + losses[k]) / n
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def sig_rsi2c(bars, i, regime):
    """Connors RSI(2)<10 pullback (STSTW ch.9; published 83.6% win on SPX
    1995-2007 with close entry/exit). Long above the regime MA; the book's
    mirror short below it (RSI2>90)."""
    if i < 55:
        return None
    s = sma(bars, i, 50)
    r = wilder_rsi(bars, i, 2)
    if s is None or r is None:
        return None
    c = bars[i - 1]["close"]
    if c > s and r < 10:
        return "buy"
    if c < s and r > 90:
        return "sell"
    return None


def sig_dbl7(bars, i, regime):
    """Connors Double 7s (STSTW ch.10; published 80.4% win on SPY): above
    the regime MA, buy when yesterday closed at a 7-day closing low
    (Method 1 per Alvarez: inclusive). Long only, as published."""
    if i < 55:
        return None
    s = sma(bars, i, 50)
    if s is None:
        return None
    c = bars[i - 1]["close"]
    if c > s and c <= min(b["close"] for b in bars[i - 7:i]):
        return "buy"
    return None


def sig_crsi(bars, i, regime):
    """Connors Cumulative RSI (STSTW ch.9; X=2 Y=35 published 88% win on
    SPY): above regime MA, buy when RSI(2) summed over the last 2
    completed bars is under 35."""
    if i < 56:
        return None
    s = sma(bars, i, 50)
    r1 = wilder_rsi(bars, i, 2)
    r2 = wilder_rsi(bars, i - 1, 2)
    if s is None or r1 is None or r2 is None:
        return None
    if bars[i - 1]["close"] > s and (r1 + r2) < 35:
        return "buy"
    return None


def sig_rsi4x(bars, i, regime):
    """Connors RSI 25/75 (High Probability ETF Trading; published 79.5%
    win long): RSI(4)<25 above the regime MA; mirror short RSI(4)>75
    below it."""
    if i < 55:
        return None
    s = sma(bars, i, 50)
    r = wilder_rsi(bars, i, 4)
    if s is None or r is None:
        return None
    c = bars[i - 1]["close"]
    if c > s and r < 25:
        return "buy"
    if c < s and r > 75:
        return "sell"
    return None


def sig_ibs(bars, i, regime):
    """Internal Bar Strength (Pagonidis, NAAIM 2014; published 56-60%
    next-day up probability on equity ETFs, close entry): IBS =
    (C-L)/(H-L) of the last completed bar. Buy washouts (<0.2), sell
    blowoffs (>0.8). As published: no MA filter. Known caveats inherited:
    ~25% open-entry haircut, US-equity-centric evidence."""
    if i < 2:
        return None
    b = bars[i - 1]
    rng = b["high"] - b["low"]
    if rng <= 0:
        return None
    ibs = (b["close"] - b["low"]) / rng
    if ibs < 0.2:
        return "buy"
    if ibs > 0.8:
        return "sell"
    return None


def sig_tom(bars, i, regime):
    """Turn-of-month (McConnell & Xu FAJ 2008; 31 of 35 countries incl.
    India per Maher & Parikh 2013): institutional month-end flows. Buy in
    the entry window approximated by calendar day >= 25 of the month
    (published: 5th-last trading day close; our engine enters next open).
    Long only; the certified exits handle the rest."""
    if i < 2:
        return None
    date = str(bars[i - 1].get("date", ""))
    if len(date) < 10:
        return None
    try:
        dom = int(date[8:10])
    except ValueError:
        return None
    if dom >= 25:
        return "buy"
    return None


SIGNALS: dict[str, Callable] = {
    "baseline": sig_baseline, "tsmom": sig_tsmom, "tsmom_f": sig_tsmom_f,
    "donchian": sig_donchian, "rsi2": sig_rsi2, "improved": sig_improved,
    "improved2": sig_improved2, "improved3": sig_improved3,
    "accurate": sig_accurate, "accurate_ls": sig_accurate_ls,
    "vbo": sig_vbo, "oops": sig_oops, "gsv": sig_gsv,
    "rsi2c": sig_rsi2c, "dbl7": sig_dbl7, "crsi": sig_crsi,
    "rsi4x": sig_rsi4x, "ibs": sig_ibs, "tom": sig_tom,
}


def get_signal(name: str) -> Callable:
    if name not in SIGNALS:
        raise KeyError(f"unknown strategy '{name}' — known: {sorted(SIGNALS)}")
    return SIGNALS[name]
