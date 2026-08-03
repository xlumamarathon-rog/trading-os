"""MODULE 5 — VaR/ES worker (spec §Phase 2). Own math (Volara replaced, v2).

Historical-simulation VaR + Expected Shortfall + EWMA vol forecast, computed in
a background loop and cached in Redis. The execution path ONLY reads the cache
(order_router.VAR_CACHE_KEY). Kupiec proportion-of-failures test validates the
VaR model on replay data (GATE G3).

`arch` GARCH hook: used when installed (spec stack); EWMA (RiskMetrics-style)
is the tested in-repo fallback so the worker never silently lacks a vol number.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from typing import Optional, Sequence

VAR95_KEY = "portfolio:var:95"
VAR99_KEY = "portfolio:var:99"
ES95_KEY = "portfolio:es:95"
HEARTBEAT_KEY = "heartbeat:var_worker"

# Kupiec LR is chi-square(1); 95% critical value (statistical constant, not a tunable)
CHI2_95_DF1 = 3.841


def historical_var(returns: Sequence[float], confidence: float) -> float:
    """Positive loss fraction at the given confidence (e.g. 0.02 = 2% VaR)."""
    if not returns:
        raise ValueError("empty returns")
    losses = sorted(-r for r in returns)  # losses as positive numbers, ascending
    # ceil(alpha*n)-th order statistic (0-based index ceil(alpha*n), clamped):
    # with exactly 5% tail losses at n=100, VaR95 lands ON the tail, not before it.
    idx = min(len(losses) - 1, max(0, math.ceil(confidence * len(losses))))
    return max(0.0, losses[idx])


def expected_shortfall(returns: Sequence[float], confidence: float) -> float:
    """Mean loss beyond VaR (always >= VaR)."""
    var = historical_var(returns, confidence)
    tail = [-r for r in returns if -r >= var]
    return sum(tail) / len(tail) if tail else var


def ewma_vol_forecast(returns: Sequence[float], lam: float = 0.94) -> float:
    """RiskMetrics EWMA daily sigma forecast (fallback when `arch` missing)."""
    if not returns:
        raise ValueError("empty returns")
    var = returns[0] ** 2
    for r in returns[1:]:
        var = lam * var + (1 - lam) * r ** 2
    return math.sqrt(var)


def garch_or_ewma_forecast(returns: Sequence[float]) -> tuple[float, str]:
    try:
        from arch import arch_model  # type: ignore

        fitted = arch_model([r * 100 for r in returns], vol="GARCH", p=1, q=1).fit(disp="off")
        sigma = float(fitted.forecast(horizon=1).variance.iloc[-1, 0]) ** 0.5 / 100.0
        return sigma, "garch"
    except ImportError:
        return ewma_vol_forecast(returns), "ewma_fallback"


def kupiec_pof(breaches: int, days: int, alpha: float) -> tuple[float, bool]:
    """Kupiec proportion-of-failures LR test. passed=True ⇒ VaR model not rejected."""
    if days <= 0 or breaches < 0 or breaches > days:
        raise ValueError("invalid kupiec inputs")
    p = alpha
    x, n = breaches, days
    if x == 0:
        lr = -2 * n * math.log(1 - p)
    else:
        phat = x / n
        lr = -2 * (
            (n - x) * math.log(1 - p) + x * math.log(p)
            - (n - x) * math.log(1 - phat) - x * math.log(phat)
        )
    return lr, lr < CHI2_95_DF1


def portfolio_returns(weights: dict[str, float], returns_by_symbol: dict[str, Sequence[float]]) -> list[float]:
    symbols = [s for s in weights if s in returns_by_symbol]
    if not symbols:
        raise ValueError("no overlapping symbols")
    n = min(len(returns_by_symbol[s]) for s in symbols)
    return [sum(weights[s] * returns_by_symbol[s][i] for s in symbols) for i in range(n)]


@dataclass
class VarSnapshot:
    var_95: float
    var_99: float
    es_95: float
    vol_forecast: float
    vol_model: str
    at: float


class VarWorker:
    def __init__(self, redis, ttl_seconds: int, positions_fn, returns_fn) -> None:
        self.redis = redis
        self.ttl = ttl_seconds
        self.positions_fn = positions_fn      # -> {symbol: weight}
        self.returns_fn = returns_fn          # -> {symbol: [daily returns]}
        self.last: Optional[VarSnapshot] = None

    async def refresh_once(self) -> VarSnapshot:
        weights = await self.positions_fn()
        rets = await self.returns_fn()
        port = portfolio_returns(weights, rets)
        vol, model = garch_or_ewma_forecast(port)
        snap = VarSnapshot(
            var_95=historical_var(port, 0.95),
            var_99=historical_var(port, 0.99),
            es_95=expected_shortfall(port, 0.95),
            vol_forecast=vol,
            vol_model=model,
            at=time.time(),
        )
        await self.redis.setex(VAR95_KEY, self.ttl, str(snap.var_95))
        await self.redis.setex(VAR99_KEY, self.ttl, str(snap.var_99))
        await self.redis.setex(ES95_KEY, self.ttl, str(snap.es_95))
        await self.redis.setex(HEARTBEAT_KEY, self.ttl * 2, json.dumps({"at": snap.at}))  # R9
        self.last = snap
        return snap

    async def run_forever(self, interval_seconds: Optional[int] = None) -> None:
        interval = interval_seconds or self.ttl
        while True:
            await self.refresh_once()
            await asyncio.sleep(interval)
