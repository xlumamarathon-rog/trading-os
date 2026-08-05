"""MODULE 48 — Daily Session Guard (Aug 2026).

Anti-overtrading discipline at the DAY level, beneath the kill switch:

  bank-the-day   once today's P&L ≥ +profit_bank_pct, stop taking NEW entries
                 (a good day banked is a good day kept)
  cut-the-day    once today's P&L ≤ −loss_stop_pct, stop taking NEW entries
                 (the kill switch at 3% is the airbag; this is the seatbelt)

Open positions are NEVER touched — stops, trails and partials keep managing
them. Only new risk is refused. State resets at the session boundary.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SessionGuardState:
    day_start_equity: float
    tripped: str = ""              # "" | "profit_banked" | "loss_stopped"
    days_banked: int = 0
    days_loss_stopped: int = 0


class SessionGuard:
    def __init__(self, *, profit_bank_pct: float = 0.0,
                 loss_stop_pct: float = 0.0) -> None:
        if profit_bank_pct < 0 or loss_stop_pct < 0:
            raise ValueError("guard percentages must be >= 0")
        self.profit_bank_pct = profit_bank_pct
        self.loss_stop_pct = loss_stop_pct
        self.state: SessionGuardState | None = None

    @property
    def enabled(self) -> bool:
        return self.profit_bank_pct > 0 or self.loss_stop_pct > 0

    def start_session(self, equity: float) -> None:
        prev = self.state
        self.state = SessionGuardState(
            day_start_equity=equity,
            days_banked=prev.days_banked if prev else 0,
            days_loss_stopped=prev.days_loss_stopped if prev else 0)

    def allows_new_entries(self, equity: float) -> bool:
        """Call before routing any NEW entry. Fail-closed on bad state."""
        if not self.enabled:
            return True
        if self.state is None or self.state.day_start_equity <= 0:
            return False
        if self.state.tripped:
            return False
        day_pnl = equity / self.state.day_start_equity - 1
        if self.profit_bank_pct > 0 and day_pnl >= self.profit_bank_pct:
            self.state.tripped = "profit_banked"
            self.state.days_banked += 1
            return False
        if self.loss_stop_pct > 0 and day_pnl <= -self.loss_stop_pct:
            self.state.tripped = "loss_stopped"
            self.state.days_loss_stopped += 1
            return False
        return True
