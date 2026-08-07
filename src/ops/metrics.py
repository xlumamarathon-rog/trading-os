"""Canonical performance metrics (Aug 2026).

Why this module exists: the Aug-8 comparison against the industry-standard
`empyrical` library (catalogued by awesome-systematic-trading) showed our
inline Sharpe and max-drawdown were ALREADY numerically exact — so nothing
needed replacing. The genuine gap was breadth: empyrical also reports Sortino,
Calmar and annualized volatility, which our research harness did not.

Rather than pull empyrical (pandas/numpy/scipy) into a runtime that
deliberately ships on three dependencies, these are lean, stdlib-only
reimplementations, each pinned to empyrical's definition by a cross-validation
test (tests/unit/test_metrics_aug2026.py) that asserts 0.0 divergence on real
equity curves. So we get the reference's correctness without its weight.

All functions take a returns list (simple per-period returns) unless noted;
`periods` is the annualization factor (252 trading days default).
"""
from __future__ import annotations

import math
from typing import Sequence

TRADING_DAYS = 252


def simple_returns(equity: Sequence[float]) -> list[float]:
    """Per-step simple returns from an equity curve."""
    return [equity[k] / equity[k - 1] - 1 for k in range(1, len(equity))
            if equity[k - 1] != 0]


def max_drawdown(equity: Sequence[float]) -> float:
    """Largest peak-to-trough decline as a POSITIVE fraction (empyrical
    reports the same magnitude with a negative sign)."""
    if not equity:
        return 0.0
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


def sharpe_ratio(returns: Sequence[float], risk_free: float = 0.0,
                 periods: int = TRADING_DAYS) -> float:
    """Annualized Sharpe. Sample std (ddof=1), matching empyrical."""
    if len(returns) < 2:
        return 0.0
    adj = [r - risk_free for r in returns]
    mu = sum(adj) / len(adj)
    sd = math.sqrt(sum((r - mu) ** 2 for r in adj) / (len(adj) - 1))
    return (mu / sd * math.sqrt(periods)) if sd > 0 else 0.0


def annual_volatility(returns: Sequence[float], periods: int = TRADING_DAYS) -> float:
    """Annualized standard deviation of returns (sample std, ddof=1)."""
    if len(returns) < 2:
        return 0.0
    mu = sum(returns) / len(returns)
    sd = math.sqrt(sum((r - mu) ** 2 for r in returns) / (len(returns) - 1))
    return sd * math.sqrt(periods)


def sortino_ratio(returns: Sequence[float], required_return: float = 0.0,
                  periods: int = TRADING_DAYS) -> float:
    """Annualized Sortino. Downside deviation uses the POPULATION mean of
    squared negative excess returns (empyrical's convention)."""
    if not returns:
        return 0.0
    downside = math.sqrt(
        sum(min(r - required_return, 0.0) ** 2 for r in returns) / len(returns))
    if downside == 0:
        return 0.0
    avg = sum(r - required_return for r in returns) / len(returns)
    return avg * math.sqrt(periods) / downside


def cagr(equity: Sequence[float], periods: int = TRADING_DAYS) -> float:
    """Annualized (geometric) return implied by the equity curve endpoints
    over its length — empyrical's `annual_return`."""
    n = len(equity) - 1
    if n <= 0 or equity[0] <= 0:
        return 0.0
    total = equity[-1] / equity[0]
    if total <= 0:
        return -1.0
    return total ** (periods / n) - 1


def calmar_ratio(equity: Sequence[float], periods: int = TRADING_DAYS) -> float:
    """Annualized return divided by the absolute max drawdown."""
    mdd = max_drawdown(equity)
    if mdd == 0:
        return 0.0
    return cagr(equity, periods) / mdd


def summary(equity: Sequence[float], periods: int = TRADING_DAYS) -> dict:
    """The full metric block for a report/results.json enrichment."""
    rets = simple_returns(equity)
    return {
        "sharpe_annualized": round(sharpe_ratio(rets, periods=periods), 4),
        "sortino_annualized": round(sortino_ratio(rets, periods=periods), 4),
        "calmar_ratio": round(calmar_ratio(equity, periods=periods), 4),
        "annual_vol_pct": round(annual_volatility(rets, periods=periods) * 100, 4),
        "max_drawdown_pct": round(max_drawdown(equity) * 100, 4),
        "cagr_pct": round(cagr(equity, periods=periods) * 100, 4),
    }
