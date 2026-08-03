"""MODULE 43 — Scheduled-event lockouts (spec §Phase 2, NEW — Tier 1).

Most "sudden" crashes are scheduled (RBI/Fed/CPI/budget/expiry). The calendar
locks NEW entries in affected symbols T-pre..T+post and tells the exit engine
to tighten. market_wide events affect every symbol.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CalendarEvent:
    name: str
    ts: float                      # epoch seconds of the event
    affected: list = field(default_factory=lambda: ["market_wide"])


class EventCalendar:
    def __init__(self, pre_lockout_min: float, post_resume_min: float) -> None:
        self.pre = pre_lockout_min * 60.0
        self.post = post_resume_min * 60.0
        self.events: list[CalendarEvent] = []

    def add_event(self, name: str, ts: float, affected: Optional[list] = None) -> None:
        self.events.append(CalendarEvent(name, ts, affected or ["market_wide"]))

    def _relevant(self, symbol: str):
        for ev in self.events:
            if "market_wide" in ev.affected or symbol in ev.affected:
                yield ev

    def lockout_active(self, symbol: str, now: float) -> bool:
        return any(ev.ts - self.pre <= now <= ev.ts + self.post for ev in self._relevant(symbol))

    def minutes_to_next(self, symbol: str, now: float) -> Optional[float]:
        upcoming = [ev.ts - now for ev in self._relevant(symbol) if ev.ts >= now]
        return min(upcoming) / 60.0 if upcoming else None

    def session_check(self, symbol: str, now: float) -> bool:
        """Router pre-check: True = OK to enter (no lockout)."""
        return not self.lockout_active(symbol, now)
