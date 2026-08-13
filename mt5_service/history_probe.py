"""History-depth probing for the MT5 bridge (Aug 2026).

Answers "how much intraday history do we actually OWN on the forex leg?" —
the gate for any MT5 intraday backtest (orderflow research, ledger
2026-08-13). MQL5's SeriesInfoInteger(SERIES_SERVER_FIRSTDATE) is NOT exposed
by the MetaTrader5 Python API, so the honest equivalent is a bisection over
calendar days: find the earliest day for which the server returns data, once
for M1 bars and once for real ticks. Broker retention is a per-server fact
(typical retail: years of M1, months of ticks) — measure it, never assume it.

The search logic is pure and lives here so it is testable off-Windows; the
aiomql-bound probe callables live in aiomql_impl.Mt5Aiomql.history_depth.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable

UTC = dt.timezone.utc
FLOOR = dt.date(2000, 1, 1)          # no retail server predates this


def earliest_available(has_data: Callable[[dt.date], bool],
                       lo: dt.date = FLOOR,
                       hi: dt.date | None = None) -> dt.date | None:
    """Bisect for the earliest date where has_data(day) is True.

    Contract: availability is monotone (False ... False True ... True) — a
    server that has data for day D has it for every later day. Probes are
    expensive (first tick pulls sync from the server), so this makes
    O(log2(days)) ≈ 14 calls for a 2000..today span.
    Returns None when even `hi` has no data (dead symbol / disconnected).
    """
    hi = hi or dt.datetime.now(UTC).date() - dt.timedelta(days=1)
    if lo > hi:
        return None
    if not has_data(hi):
        return None
    if has_data(lo):
        return lo
    # invariant: has_data(hi) is True, has_data(lo) is False
    while (hi - lo).days > 1:
        mid = lo + (hi - lo) / 2
        if has_data(mid):
            hi = mid
        else:
            lo = mid
    return hi


async def earliest_available_async(has_data,
                                   lo: dt.date = FLOOR,
                                   hi: dt.date | None = None) -> dt.date | None:
    """Async twin of earliest_available — identical invariants, awaitable
    probe (the aiomql copy_* calls are async). Kept beside the sync version
    so both share this module's tests via the same boundary cases."""
    hi = hi or dt.datetime.now(UTC).date() - dt.timedelta(days=1)
    if lo > hi:
        return None
    if not await has_data(hi):
        return None
    if await has_data(lo):
        return lo
    while (hi - lo).days > 1:
        mid = lo + (hi - lo) / 2
        if await has_data(mid):
            hi = mid
        else:
            lo = mid
    return hi


def day_bounds_epoch(day: dt.date) -> tuple[int, int]:
    """UTC [start, end) epoch seconds for one calendar day."""
    start = dt.datetime(day.year, day.month, day.day, tzinfo=UTC)
    return int(start.timestamp()), int(start.timestamp()) + 86_400
