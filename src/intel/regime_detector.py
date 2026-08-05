"""MODULE 34 — Regime Detector (spec §Phase 2, NEW in v2).

Per-symbol market state consumed by position_sizer, exit_manager, rebalancer:
  vol_regime   LOW / NORMAL / HIGH / SHOCK   (realized-vol percentile vs history)
  trend_state  STRONG_TREND / WEAK_TREND / RANGE   (ADX + EMA alignment)
  hurst        >0.55 trending / <0.45 mean-reverting / else random-ish
  gex_regime   amplify / dampen / unknown    (from MODULE 39)

Stdlib-only math (numpy optional elsewhere). Estimators are intentionally
simple + labeled-window tested — sophistication belongs in research, not the
live reflex path.
"""
from __future__ import annotations

import json
import logging
import math
import statistics
from dataclasses import dataclass, asdict
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

REGIME_KEY_PREFIX = "regime:"

VOL_REGIMES = ("LOW", "NORMAL", "HIGH", "SHOCK")
TREND_STATES = ("STRONG_TREND", "WEAK_TREND", "RANGE")

# Classification boundaries (percentiles of realized vol vs own history).
VOL_PCTL_BOUNDS = {"LOW": 0.25, "NORMAL": 0.75, "HIGH": 0.95}  # above 0.95 => SHOCK
ADX_STRONG = 25.0
ADX_WEAK = 15.0
HURST_TREND = 0.55
HURST_MEANREV = 0.45


def realized_vol(returns: Sequence[float]) -> float:
    if len(returns) < 2:
        raise ValueError("need >=2 returns")
    return statistics.pstdev(returns)


def rolling_vol_series(returns: Sequence[float], window: int) -> list[float]:
    return [realized_vol(returns[i - window:i]) for i in range(window, len(returns) + 1)]


def percentile_of(value: float, history: Sequence[float]) -> float:
    """Mid-rank percentile: ties count half — a value equal to ALL of history
    sits at 0.5, not 1.0 (avoids false SHOCK on flat vol histories)."""
    if not history:
        return 0.5
    strictly_below = sum(1 for h in history if h < value)
    ties = sum(1 for h in history if h == value)
    return (strictly_below + 0.5 * ties) / len(history)


def ema(values: Sequence[float], period: int) -> float:
    if not values:
        raise ValueError("empty values")
    k = 2.0 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> float:
    """Simplified Wilder ADX — enough to separate trend from chop."""
    n = len(closes)
    if n < period + 2:
        raise ValueError("insufficient bars for ADX")
    plus_dm, minus_dm, tr = [], [], []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                      abs(lows[i] - closes[i - 1])))
    dxs = []
    for i in range(period, len(tr) + 1):
        tr_sum = sum(tr[i - period:i])
        if tr_sum == 0:
            continue
        pdi = 100 * sum(plus_dm[i - period:i]) / tr_sum
        mdi = 100 * sum(minus_dm[i - period:i]) / tr_sum
        if pdi + mdi == 0:
            continue
        dxs.append(100 * abs(pdi - mdi) / (pdi + mdi))
    if not dxs:
        return 0.0
    tail = dxs[-period:] if len(dxs) >= period else dxs
    return sum(tail) / len(tail)


def hurst_exponent(series: Sequence[float], max_lag: int = 20) -> float:
    """Variance-of-lagged-differences estimator: Var(lag) ~ lag^(2H)."""
    if len(series) < max_lag * 3:
        raise ValueError("series too short for hurst")
    lags = range(2, max_lag)
    xs, ys = [], []
    for lag in lags:
        diffs = [series[i + lag] - series[i] for i in range(len(series) - lag)]
        sd = statistics.pstdev(diffs)
        if sd > 0:
            xs.append(math.log(lag))
            ys.append(math.log(sd))
    if len(xs) < 3:
        return 0.5
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return max(0.0, min(1.0, num / den if den else 0.5))


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> float:
    if len(closes) < period + 1:
        raise ValueError("insufficient bars for ATR")
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(1, len(closes))]
    return sum(trs[-period:]) / period


@dataclass
class RegimeState:
    symbol: str
    vol_regime: str
    vol_percentile: float
    trend_state: str
    adx_value: float
    hurst: float
    atr_value: float
    gex_regime: str = "unknown"
    session: str = "unknown"
    trend_direction: str = "FLAT"  # UP / DOWN / FLAT — ADX is direction-blind;
                                   # short strategies need the sign (Aug 2026)

    def as_dict(self) -> dict:
        return asdict(self)


def classify_vol(current_vol: float, vol_history: Sequence[float]) -> tuple[str, float]:
    pctl = percentile_of(current_vol, vol_history)
    if pctl > VOL_PCTL_BOUNDS["HIGH"]:
        return "SHOCK", pctl
    if pctl > VOL_PCTL_BOUNDS["NORMAL"]:
        return "HIGH", pctl
    if pctl < VOL_PCTL_BOUNDS["LOW"]:
        return "LOW", pctl
    return "NORMAL", pctl


def classify_trend(adx_value: float, closes: Sequence[float],
                   fast: int = 20, slow: int = 50) -> str:
    aligned = ema(list(closes), fast) > ema(list(closes), slow)
    rising = closes[-1] > closes[-min(len(closes), fast)]
    if adx_value >= ADX_STRONG and (aligned == rising or adx_value >= ADX_STRONG * 1.5):
        return "STRONG_TREND"
    if adx_value >= ADX_WEAK:
        return "WEAK_TREND"
    return "RANGE"


def classify_direction(closes: Sequence[float], fast: int = 20, slow: int = 50,
                       flat_band: float = 0.002) -> str:
    """Sign of the trend, symmetric by construction: EMA-fast vs EMA-slow
    with a small dead band. ADX (strength) says HOW MUCH; this says WHICH WAY
    — downtrends are DOWN, not 'RANGE' (the long-bias trap)."""
    f, s = ema(list(closes), fast), ema(list(closes), slow)
    if s == 0:
        return "FLAT"
    dev = (f - s) / abs(s)
    if dev > flat_band:
        return "UP"
    if dev < -flat_band:
        return "DOWN"
    return "FLAT"


class RegimeDetector:
    def __init__(self, redis, vol_window: int, hurst_window: int, adx_period: int) -> None:
        self.redis = redis
        self.vol_window = vol_window
        self.hurst_window = hurst_window
        self.adx_period = adx_period

    async def classify(self, symbol: str, highs: Sequence[float], lows: Sequence[float],
                       closes: Sequence[float], gex_regime: str = "unknown",
                       session: str = "unknown") -> RegimeState:
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
        vols = rolling_vol_series(returns, min(self.vol_window, max(2, len(returns) // 3)))
        vol_regime, pctl = classify_vol(vols[-1], vols[:-1] or vols)
        adx_val = adx(highs, lows, closes, self.adx_period)
        state = RegimeState(
            symbol=symbol,
            vol_regime=vol_regime,
            vol_percentile=pctl,
            trend_state=classify_trend(adx_val, closes),
            trend_direction=classify_direction(closes),
            adx_value=adx_val,
            hurst=hurst_exponent(list(closes[-self.hurst_window:])),
            atr_value=atr(highs, lows, closes),
            gex_regime=gex_regime,
            session=session,
        )
        try:
            await self.redis.set(REGIME_KEY_PREFIX + symbol, json.dumps(state.as_dict()))
        except Exception as exc:  # noqa: BLE001 — R5: consumers read fail-closed; log the loss
            logger.error("regime publish failed for %s: %s", symbol, exc)
        return state
