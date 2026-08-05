"""Guard stack — the canonical composition of the portfolio-level gates
(Aug 2026). This is the ONE function that decides whether a NEW entry may
even reach the position sizer, evaluated in strict precedence order:

    1. BudgetManager      is there ring-fenced capital left to trade?
    2. SessionGuard       has today already been banked / cut?
    3. PortfolioHeatMgr   would this order push aggregate stop-loss
                          exposure past the cap?

Wire the result into OrderRouter(portfolio_guard_fn=...). Every rejection
comes back as (False, "layer:reason") and lands in the router's audited
checks — nothing is silently dropped.

All layers are optional (None = skip): the stack degrades gracefully to
"always allow" when nothing is configured, so legacy assemblies keep their
exact behavior."""
from __future__ import annotations

from typing import Callable, Optional


def make_portfolio_guard(*, equity_fn: Callable,
                         risk_limits,
                         budget=None,
                         session_guard=None,
                         heat_mgr=None,
                         positions_fn: Optional[Callable] = None) -> Callable:
    """Returns async guard(req) -> (ok, reason) for OrderRouter."""

    async def guard(req) -> tuple[bool, str]:
        equity = float(equity_fn())

        if budget is not None:
            ok, why = budget.entries_allowed(equity)
            if not ok:
                return False, f"budget:{why}"

        if session_guard is not None and session_guard.enabled:
            if not session_guard.allows_new_entries(equity):
                return False, "session_guard:day_tripped"

        if heat_mgr is not None:
            tradable = budget.effective(equity) if budget is not None else equity
            positions = list(positions_fn()) if positions_fn else []
            hc = heat_mgr.check(
                positions=positions, equity=tradable,
                proposed_risk=tradable * risk_limits.max_risk_per_trade_pct)
            if not hc.allowed:
                return False, f"heat:{hc.reason}"

        return True, "ok"

    return guard
