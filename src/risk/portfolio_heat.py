"""MODULE 46 — Portfolio Heat Manager (Aug 2026).

Caps TOTAL concurrent open risk across every managed position. Per-order
checks (VaR, margin, per-trade risk) all passed while an 8-symbol book
silently accumulated 16% aggregate heat — this module is the missing
portfolio-level gate.

Heat of one position = remaining_qty · |entry − stop| / equity   (the amount
lost if the stop is hit from here, as a fraction of current equity; stops
that have ratcheted past breakeven contribute ZERO heat).

Contract: check BEFORE routing a new entry. Fail-closed: if equity or the
proposed risk is invalid, the answer is "no".
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HeatCheck:
    allowed: bool
    current_heat: float          # fraction of equity at risk before this order
    proposed_heat: float         # heat if this order is admitted
    cap: float
    reason: str


def position_heat(*, direction: str, entry: float, stop: float,
                  remaining_qty: float) -> float:
    """Currency-at-risk for one open position (0 when the stop is past
    breakeven — a ratcheted stop can only bank profit, not lose)."""
    if remaining_qty <= 0:
        return 0.0
    loss_per_unit = (entry - stop) if direction == "buy" else (stop - entry)
    return max(0.0, loss_per_unit * remaining_qty)


class PortfolioHeatManager:
    def __init__(self, *, max_heat_pct: float = 0.06) -> None:
        if max_heat_pct <= 0:
            raise ValueError("max_heat_pct must be positive")
        self.max_heat_pct = max_heat_pct

    def current_heat(self, positions, equity: float) -> float:
        """positions: iterable of objects/dicts with direction, entry, stop,
        remaining_qty (ExitManager.ManagedPosition works as-is)."""
        if equity <= 0:
            return float("inf")
        total = 0.0
        for p in positions:
            g = (lambda k: p.get(k)) if isinstance(p, dict) else (lambda k: getattr(p, k))
            if g("state") == "EXITED":
                continue
            total += position_heat(direction=g("direction"), entry=g("entry"),
                                   stop=g("stop"), remaining_qty=g("remaining_qty"))
        return total / equity

    def check(self, *, positions, equity: float,
              proposed_risk: float) -> HeatCheck:
        """proposed_risk: currency amount the NEW order would put at risk
        (qty · |entry − stop|). Fail-closed on invalid inputs."""
        import math
        if equity <= 0 or not math.isfinite(equity) \
                or proposed_risk < 0 or not math.isfinite(proposed_risk):
            return HeatCheck(False, 1.0, 1.0, self.max_heat_pct, "invalid_inputs")
        cur = self.current_heat(positions, equity)
        prop = cur + proposed_risk / equity
        if prop > self.max_heat_pct + 1e-12:
            return HeatCheck(False, round(cur, 6), round(prop, 6),
                             self.max_heat_pct,
                             f"portfolio_heat {prop:.4f} > cap {self.max_heat_pct}")
        return HeatCheck(True, round(cur, 6), round(prop, 6),
                         self.max_heat_pct, "ok")
