"""MODULE 32 — Walk-forward validation (rolling window, majority rule)."""
from __future__ import annotations

from dataclasses import dataclass

MIN_SEGMENTS = 5


@dataclass
class WalkForwardResult:
    passed: bool
    segments: list
    profitable_fraction: float


async def walk_forward_test(pattern: dict, start_year: int, end_year: int,
                            rediscover_fn, backtest_fn,
                            window_years: int = 20, step_years: int = 1) -> WalkForwardResult:
    """rediscover_fn(pattern, train_end_year) -> bool (found using ONLY data <= train_end).
    backtest_fn(pattern, test_start, test_end) -> sharpe for that forward segment."""
    segments = []
    train_end = start_year + window_years
    while train_end < end_year:
        test_start, test_end = train_end, min(train_end + step_years, end_year)
        if await rediscover_fn(pattern, train_end):
            sharpe = await backtest_fn(pattern, test_start, test_end)
            segments.append({"train_end": train_end, "sharpe": sharpe})
        train_end += step_years
    if len(segments) < MIN_SEGMENTS:
        return WalkForwardResult(False, segments, 0.0)
    profitable = sum(1 for s in segments if s["sharpe"] > 0) / len(segments)
    return WalkForwardResult(profitable > 0.5, segments, profitable)
