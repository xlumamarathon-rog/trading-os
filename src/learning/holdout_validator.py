"""MODULE 25 — Holdout validation with bootstrap significance (v2 statistics).

Rule passes only if: sharpe_delta > 0 AND drawdown not worse AND stationary-
bootstrap p-value < 0.1. Each candidate consumes the holdout once per quarter
(leak control) — enforced by consumed-registry.
"""
from __future__ import annotations

import random
import statistics
import time
from dataclasses import dataclass

P_THRESHOLD = 0.10
QUARTER_SECONDS = 90 * 24 * 3600


@dataclass
class ValidationResult:
    passed: bool
    sharpe_delta: float
    dd_delta: float
    p_value: float
    reason: str


def sharpe(returns: list) -> float:
    if len(returns) < 2:
        return 0.0
    sd = statistics.pstdev(returns)
    return (sum(returns) / len(returns)) / sd if sd else 0.0


def max_drawdown(returns: list) -> float:
    equity, peak, mdd = 1.0, 1.0, 0.0
    for r in returns:
        equity *= 1 + r
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak)
    return mdd


def bootstrap_p_value(delta_series: list, n_boot: int = 500, seed: int = 7) -> float:
    """H0: mean(delta) <= 0. Circular block bootstrap on per-period deltas."""
    if not delta_series:
        return 1.0
    observed = sum(delta_series) / len(delta_series)
    if observed <= 0:
        return 1.0
    rng = random.Random(seed)
    n, block = len(delta_series), max(1, len(delta_series) // 10)
    centered = [d - observed for d in delta_series]
    hits = 0
    for _ in range(n_boot):
        sample = []
        while len(sample) < n:
            start = rng.randrange(n)
            sample.extend(centered[(start + i) % n] for i in range(block))
        boot_mean = sum(sample[:n]) / n
        if boot_mean >= observed:
            hits += 1
    return hits / n_boot


class HoldoutValidator:
    def __init__(self, backtest_fn) -> None:
        """backtest_fn(rule|None) -> list[period returns] on the SAME holdout window."""
        self.backtest_fn = backtest_fn
        self._consumed: dict[str, float] = {}

    async def test(self, candidate_rule: dict) -> ValidationResult:
        rid = candidate_rule.get("id", repr(sorted(candidate_rule.items())))
        last = self._consumed.get(rid)
        if last is not None and time.time() - last < QUARTER_SECONDS:
            return ValidationResult(False, 0, 0, 1.0, "holdout_already_consumed_this_quarter")
        self._consumed[rid] = time.time()

        with_rule = await self.backtest_fn(candidate_rule)
        without_rule = await self.backtest_fn(None)
        s_delta = sharpe(with_rule) - sharpe(without_rule)
        dd_delta = max_drawdown(without_rule) - max_drawdown(with_rule)   # >=0 is good
        deltas = [a - b for a, b in zip(with_rule, without_rule)]
        p = bootstrap_p_value(deltas)
        passed = s_delta > 0 and dd_delta >= 0 and p < P_THRESHOLD
        reason = "ok" if passed else f"sharpe_delta={s_delta:.3f}, dd_delta={dd_delta:.3f}, p={p:.3f}"
        return ValidationResult(passed, s_delta, dd_delta, p, reason)
