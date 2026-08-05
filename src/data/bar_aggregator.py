"""MODULE 53 — Intraday bar aggregator (Aug 2026).

Bridges the tick feed to bar-based strategies: ticks in, completed OHLCV
bars out. Strategies are daily-only today; this unlocks intraday timeframes
without touching the strategy contract (bars are the same dict shape).

Time-aligned buckets (a 300s bar covers [k·300, (k+1)·300)); a bar is
emitted when the first tick of the NEXT bucket arrives. flush() force-closes
the open bar (session end)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Bar:
    ts: float                       # bucket start (epoch seconds)
    open: float
    high: float
    low: float
    close: float
    volume: float

    def as_dict(self) -> dict:
        return {"ts": self.ts, "open": self.open, "high": self.high,
                "low": self.low, "close": self.close, "volume": self.volume}


class BarAggregator:
    def __init__(self, interval_s: float = 300.0) -> None:
        if interval_s <= 0:
            raise ValueError("interval must be positive")
        self.interval = interval_s
        self._open: Optional[Bar] = None

    def _bucket(self, ts: float) -> float:
        return ts - (ts % self.interval)

    def on_tick(self, ts: float, price: float, volume: float = 0.0) -> Optional[Bar]:
        """Feed one tick; returns the COMPLETED bar when a bucket rolls over,
        else None. Out-of-order ticks within the open bucket are absorbed;
        ticks older than the open bucket are dropped (never rewrite history)."""
        if price <= 0:
            raise ValueError("tick price must be positive")
        b = self._bucket(ts)
        if self._open is None:
            self._open = Bar(b, price, price, price, price, volume)
            return None
        if b < self._open.ts:
            return None                       # stale tick — history is immutable
        if b == self._open.ts:
            self._open.high = max(self._open.high, price)
            self._open.low = min(self._open.low, price)
            self._open.close = price
            self._open.volume += volume
            return None
        done = self._open
        self._open = Bar(b, price, price, price, price, volume)
        return done

    def flush(self) -> Optional[Bar]:
        done, self._open = self._open, None
        return done
