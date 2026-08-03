"""MODULE 26 — Live failure triggers -> async diagnosis (never blocks execution)."""
from __future__ import annotations


def consecutive_losses(recent_trades: list) -> int:
    n = 0
    for t in reversed(recent_trades):
        if t["pnl"] < 0:
            n += 1
        else:
            break
    return n


def should_trigger(recent_trades: list, daily_loss_breached: bool,
                   strategy_drawdown: float, historical_avg_drawdown: float,
                   consecutive_threshold: int = 3, dd_mult: float = 2.0) -> tuple:
    if daily_loss_breached:
        return True, "daily_loss_breached"
    if consecutive_losses(recent_trades) >= consecutive_threshold:
        return True, f"{consecutive_threshold}_consecutive_losses"
    if historical_avg_drawdown > 0 and strategy_drawdown > dd_mult * historical_avg_drawdown:
        return True, "drawdown_2x_historical"
    return False, ""
