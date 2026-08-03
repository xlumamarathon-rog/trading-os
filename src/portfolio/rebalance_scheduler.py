"""MODULE 15 — Drift-based rebalancer with liquidity/expiry/event/cost guards (v2)."""
from __future__ import annotations

from dataclasses import dataclass

DRIFT_THRESHOLD = 0.02


@dataclass
class RebalanceTrade:
    symbol: str
    direction: str
    drift_pct: float
    skipped_reason: str = ""


def plan_rebalance(current: dict, target: dict, *, liquidity_ok, near_expiry,
                   event_locked, trade_cost_fn, expected_benefit_fn,
                   drift_threshold: float = DRIFT_THRESHOLD) -> list:
    """Pure planning — the router executes. Every skip carries a named reason."""
    trades = []
    for symbol in sorted(set(current) | set(target)):
        drift = target.get(symbol, 0.0) - current.get(symbol, 0.0)
        if abs(drift) < drift_threshold:
            continue
        t = RebalanceTrade(symbol, "buy" if drift > 0 else "sell", drift)
        if not liquidity_ok(symbol, abs(drift)):
            t.skipped_reason = "illiquid"
        elif near_expiry(symbol):
            t.skipped_reason = "near_fo_expiry"
        elif event_locked(symbol):
            t.skipped_reason = "event_lockout"
        elif trade_cost_fn(symbol, abs(drift)) >= expected_benefit_fn(symbol, abs(drift)):
            t.skipped_reason = "cost_exceeds_benefit"
        trades.append(t)
    return trades
