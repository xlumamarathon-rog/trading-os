"""MODULE 58 — Market Clock (Aug 2026).

Per-leg session calendar: the single authority on "is this market open?".

Why this exists (found via operator video review, 2026-08-10): the config has
declared `trading_hours` since MODULE 18, but NOTHING consumed it — the paper
runtime happily entered india-leg trades at 21:00 IST with the NSE closed.
The OrderRouter has carried an (unwired) `session_open_fn` precheck hook for
exactly this since MODULE 21. This module is what finally plugs it.

Design rules:
  - ENTRIES are gated. EXITS ARE NEVER GATED — open positions must always be
    manageable (stops, trails, kill switch operate regardless of the clock).
  - Fail-open on unknown legs (crypto-style 24/7) so a new leg cannot be
    silently bricked; fail-closed on india calendar parse errors.
  - Pure stdlib. zoneinfo when available; a fixed +05:30 offset fallback is
    CORRECT for India (no DST has ever applied to IST).
  - Config-driven: hours + weekend days + holiday list live in master.yaml.
    Zero magic numbers here (spec §3).

Legs: "india" (NSE cash), "mt5_forex" (24/5), "mt5_crypto" (24/7).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

try:  # pragma: no cover - environment dependent
    from zoneinfo import ZoneInfo
    _IST: dt.tzinfo = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    _IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")

UTC = dt.timezone.utc


def _parse_hhmm(s: str) -> dt.time:
    h, m = s.strip().split(":")
    return dt.time(int(h), int(m))


class MarketClock:
    """Answers is_open / next_open / next_close per leg.

    cfg_hours is the `trading_hours` mapping from master.yaml:
      india:  {open, close, timezone, weekdays?, holidays?}
      forex:  {week_open_utc?, week_close_utc?}   (defaults Mon 00:00 → Fri 21:00)
      crypto: always open
    """

    def __init__(self, cfg_hours: Optional[dict] = None) -> None:
        cfg_hours = cfg_hours or {}
        india = cfg_hours.get("india") or {}
        self.india_open = _parse_hhmm(india.get("open", "09:15"))
        self.india_close = _parse_hhmm(india.get("close", "15:30"))
        # Mon=0 .. Sun=6; NSE trades Mon-Fri
        self.india_weekdays = set(india.get("weekdays", [0, 1, 2, 3, 4]))
        self.india_holidays: set[dt.date] = set()
        for h in india.get("holidays", []) or []:
            try:
                self.india_holidays.add(dt.date.fromisoformat(str(h)[:10]))
            except ValueError:
                # A malformed holiday entry must not silently open the market
                # on a day the operator meant to close: refuse to construct.
                raise ValueError(f"market_clock: bad holiday date {h!r}")
        forex = cfg_hours.get("forex") or {}
        # FX CFD week: Monday 00:00 UTC through Friday 21:00 UTC by default.
        self.fx_week_open_dow = int(forex.get("week_open_dow", 0))     # Monday
        self.fx_week_open = _parse_hhmm(forex.get("week_open_utc", "00:00"))
        self.fx_week_close_dow = int(forex.get("week_close_dow", 4))   # Friday
        self.fx_week_close = _parse_hhmm(forex.get("week_close_utc", "21:00"))

    # ---------------- india ----------------

    def _india_day_ok(self, d: dt.date) -> bool:
        return d.weekday() in self.india_weekdays and d not in self.india_holidays

    def _india_open_at(self, now_utc: dt.datetime) -> bool:
        ist = now_utc.astimezone(_IST)
        if not self._india_day_ok(ist.date()):
            return False
        t = ist.time()
        return self.india_open <= t < self.india_close

    def _india_next_open(self, now_utc: dt.datetime) -> dt.datetime:
        ist = now_utc.astimezone(_IST)
        for add in range(0, 370):  # scan just over a year of days
            d = ist.date() + dt.timedelta(days=add)
            if not self._india_day_ok(d):
                continue
            candidate = dt.datetime.combine(d, self.india_open, tzinfo=_IST)
            if candidate > ist:
                return candidate.astimezone(UTC)
        raise RuntimeError("market_clock: no india session in the next year")

    def _india_next_close(self, now_utc: dt.datetime) -> Optional[dt.datetime]:
        if not self._india_open_at(now_utc):
            return None
        ist = now_utc.astimezone(_IST)
        return dt.datetime.combine(ist.date(), self.india_close,
                                   tzinfo=_IST).astimezone(UTC)

    # ---------------- forex (24/5 week window, UTC) ----------------

    def _fx_open_at(self, now_utc: dt.datetime) -> bool:
        dow, t = now_utc.weekday(), now_utc.time()
        o_dow, c_dow = self.fx_week_open_dow, self.fx_week_close_dow
        if dow < o_dow or dow > c_dow:
            return False
        if dow == o_dow and t < self.fx_week_open:
            return False
        if dow == c_dow and t >= self.fx_week_close:
            return False
        return True

    def _fx_next_open(self, now_utc: dt.datetime) -> dt.datetime:
        for add in range(0, 8):
            d = now_utc.date() + dt.timedelta(days=add)
            if d.weekday() != self.fx_week_open_dow:
                continue
            candidate = dt.datetime.combine(d, self.fx_week_open, tzinfo=UTC)
            if candidate > now_utc:
                return candidate
        raise RuntimeError("market_clock: no fx open in the next week")

    # ---------------- public API ----------------

    def is_open(self, leg: str, now: Optional[dt.datetime] = None) -> bool:
        now_utc = (now or dt.datetime.now(UTC)).astimezone(UTC)
        if leg == "india":
            return self._india_open_at(now_utc)
        if leg == "mt5_forex":
            return self._fx_open_at(now_utc)
        # mt5_crypto and anything unknown: 24/7 (fail-open by design)
        return True

    async def session_open_fn(self, leg: str) -> bool:
        """Router precheck contract (order_router `session_open_fn`):
        return False to refuse NEW entries on this leg right now."""
        return self.is_open(leg)

    def status(self, now: Optional[dt.datetime] = None) -> dict:
        """Cockpit payload: one row per leg with open flag + next boundary."""
        now_utc = (now or dt.datetime.now(UTC)).astimezone(UTC)
        rows = {}
        india_open = self._india_open_at(now_utc)
        rows["india"] = {
            "open": india_open,
            "label": "NSE 09:15–15:30 IST",
            "next_open_utc": (None if india_open
                              else self._india_next_open(now_utc).isoformat()),
            "next_close_utc": ((c := self._india_next_close(now_utc))
                               and c.isoformat()),
        }
        fx_open = self._fx_open_at(now_utc)
        rows["mt5_forex"] = {
            "open": fx_open,
            "label": "FX 24/5 (UTC week)",
            "next_open_utc": (None if fx_open
                              else self._fx_next_open(now_utc).isoformat()),
            "next_close_utc": None,
        }
        rows["mt5_crypto"] = {"open": True, "label": "Crypto 24/7",
                              "next_open_utc": None, "next_close_utc": None}
        return {"now_utc": now_utc.isoformat(), "legs": rows}
