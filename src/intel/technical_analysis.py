"""MODULE 56 — Technical Analysis toolkit (Aug 2026).

The gap OpenBB's obb.technical.* made obvious: our signal engine only had
SMA/EMA/RSI/ATR/momentum/donchian. This adds the standard studies an analyst
expects — MACD, Bollinger Bands, Stochastic, Wilder RSI, ADX, OBV — as a
clean-room, stdlib-only implementation.

Deliberately NOT built on OpenBB: OpenBB is AGPL-3.0 (network copyleft) and
drags pandas/scipy — both wrong for a lean runtime heading toward a live,
possibly client-facing path. These are permissive, dependency-free, and
computed with no lookahead (each function reads only the history it is given).

Every result carries provenance in the OpenBB "OBBject" spirit: a `Study`
bundles the value with the source tag and any warnings, so a consumer can
audit degraded/'insufficient data' reads instead of trusting a bare number.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

SOURCE = "trading_os.technical_analysis"


@dataclass
class Study:
    name: str
    values: dict
    source: str = SOURCE
    warnings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.warnings


# ---------- primitives ----------

def ema_series(values: Sequence[float], n: int) -> list[float]:
    """Full EMA series (len == len(values) once seeded), None-padded until the
    SMA seed at index n-1."""
    if n <= 0 or len(values) < n:
        return []
    k = 2.0 / (n + 1)
    seed = sum(values[:n]) / n
    out = [seed]
    for v in values[n:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def sma(values: Sequence[float], n: int) -> Optional[float]:
    if len(values) < n or n <= 0:
        return None
    return sum(values[-n:]) / n


def stdev(values: Sequence[float], n: int) -> Optional[float]:
    if len(values) < n or n <= 1:
        return None
    window = values[-n:]
    mu = sum(window) / n
    return math.sqrt(sum((x - mu) ** 2 for x in window) / n)   # population (BB convention)


def true_ranges(highs, lows, closes) -> list[float]:
    tr = []
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i],
                      abs(highs[i] - closes[i - 1]),
                      abs(lows[i] - closes[i - 1])))
    return tr


def wilder_smooth(values: Sequence[float], n: int) -> list[float]:
    """Wilder's RMA (used by ta-lib / OpenBB). Seed = SMA of first n."""
    if len(values) < n or n <= 0:
        return []
    seed = sum(values[:n]) / n
    out = [seed]
    for v in values[n:]:
        out.append((out[-1] * (n - 1) + v) / n)
    return out


# ---------- studies ----------

def macd(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Study:
    w = []
    if len(closes) < slow + signal:
        w.append("insufficient_history")
        return Study("macd", {"macd": None, "signal": None, "histogram": None}, warnings=w)
    ef, es = ema_series(closes, fast), ema_series(closes, slow)
    # align the two EMA series to the same tail length
    tail = min(len(ef), len(es))
    macd_line = [ef[-tail + i] - es[-tail + i] for i in range(tail)]
    sig = ema_series(macd_line, signal)
    if not sig:
        w.append("insufficient_history")
        return Study("macd", {"macd": None, "signal": None, "histogram": None}, warnings=w)
    m, s = macd_line[-1], sig[-1]
    return Study("macd", {"macd": m, "signal": s, "histogram": m - s})


def bollinger_bands(closes: Sequence[float], n: int = 20, k: float = 2.0) -> Study:
    mid, sd = sma(closes, n), stdev(closes, n)
    if mid is None or sd is None:
        return Study("bollinger", {"upper": None, "mid": None, "lower": None, "pct_b": None},
                     warnings=["insufficient_history"])
    upper, lower = mid + k * sd, mid - k * sd
    last = closes[-1]
    pct_b = (last - lower) / (upper - lower) if upper != lower else 0.5
    return Study("bollinger", {"upper": upper, "mid": mid, "lower": lower,
                               "pct_b": pct_b, "bandwidth": (upper - lower) / mid if mid else None})


def wilder_rsi(closes: Sequence[float], n: int = 14) -> Study:
    """Wilder's smoothed RSI — the industry/OpenBB standard (distinct from the
    signal engine's Cutler/SMA RSI, which is kept for signal back-compat)."""
    if len(closes) < n + 1:
        return Study("wilder_rsi", {"rsi": None}, warnings=["insufficient_history"])
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag, al = wilder_smooth(gains, n), wilder_smooth(losses, n)
    avg_gain, avg_loss = ag[-1], al[-1]
    if avg_loss == 0:
        return Study("wilder_rsi", {"rsi": 100.0})
    rs = avg_gain / avg_loss
    return Study("wilder_rsi", {"rsi": 100.0 - 100.0 / (1.0 + rs)})


def stochastic(highs, lows, closes, k: int = 14, d: int = 3) -> Study:
    if len(closes) < k + d:
        return Study("stochastic", {"k": None, "d": None}, warnings=["insufficient_history"])
    ks = []
    for end in range(k, len(closes) + 1):
        hh = max(highs[end - k:end])
        ll = min(lows[end - k:end])
        c = closes[end - 1]
        ks.append(100.0 * (c - ll) / (hh - ll) if hh != ll else 50.0)
    d_val = sum(ks[-d:]) / d
    return Study("stochastic", {"k": ks[-1], "d": d_val})


def adx(highs, lows, closes, n: int = 14) -> Study:
    if len(closes) < 2 * n + 1:
        return Study("adx", {"adx": None, "plus_di": None, "minus_di": None},
                     warnings=["insufficient_history"])
    tr = true_ranges(highs, lows, closes)
    plus_dm, minus_dm = [], []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
    atr = wilder_smooth(tr, n)
    sp = wilder_smooth(plus_dm, n)
    sm = wilder_smooth(minus_dm, n)
    tail = min(len(atr), len(sp), len(sm))
    dx = []
    for i in range(tail):
        a = atr[-tail + i]
        if a == 0:
            dx.append(0.0); continue
        pdi = 100.0 * sp[-tail + i] / a
        mdi = 100.0 * sm[-tail + i] / a
        denom = pdi + mdi
        dx.append(100.0 * abs(pdi - mdi) / denom if denom else 0.0)
    adx_series = wilder_smooth(dx, n)
    if not adx_series:
        return Study("adx", {"adx": None, "plus_di": None, "minus_di": None},
                     warnings=["insufficient_history"])
    a = atr[-1]
    return Study("adx", {"adx": adx_series[-1],
                         "plus_di": 100.0 * sp[-1] / a if a else None,
                         "minus_di": 100.0 * sm[-1] / a if a else None})


def obv(closes: Sequence[float], volumes: Sequence[float]) -> Study:
    if len(closes) < 2 or len(volumes) != len(closes):
        return Study("obv", {"obv": None}, warnings=["insufficient_or_mismatched_data"])
    total = 0.0
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            total += volumes[i]
        elif closes[i] < closes[i - 1]:
            total -= volumes[i]
    return Study("obv", {"obv": total})


# ---------- one-call bundle ----------

def analyze(bars: Sequence[dict]) -> dict:
    """Compute the standard study set from OHLC(V) bars and add a naive
    bull/bear/neutral read. bars: list of {open,high,low,close,[volume]}.
    Returns provenance-tagged studies (OBBject spirit)."""
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    vols = [b.get("volume", 0.0) for b in bars]
    studies = {
        "macd": macd(closes),
        "bollinger": bollinger_bands(closes),
        "wilder_rsi": wilder_rsi(closes),
        "stochastic": stochastic(highs, lows, closes),
        "adx": adx(highs, lows, closes),
        "obv": obv(closes, vols),
    }
    # naive scorecard: count bullish vs bearish tells that are actually available
    score = 0
    r = studies["wilder_rsi"].values.get("rsi")
    if r is not None:
        score += 1 if r < 30 else (-1 if r > 70 else 0)
    h = studies["macd"].values.get("histogram")
    if h is not None:
        score += 1 if h > 0 else -1
    pb = studies["bollinger"].values.get("pct_b")
    if pb is not None:
        score += 1 if pb < 0.0 else (-1 if pb > 1.0 else 0)
    read = "bullish" if score > 0 else ("bearish" if score < 0 else "neutral")
    return {
        "source": SOURCE,
        "read": read,
        "score": score,
        "studies": {k: {"values": s.values, "warnings": s.warnings} for k, s in studies.items()},
    }
