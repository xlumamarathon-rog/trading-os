"""Tick Feed Worker (Wave 12) — the live data spine.

Consumes an injected async tick stream (broker WebSocket on the VPS; replay
iterator in tests/paper), fans every tick out to:
  anomaly guard -> exit engine sub-bars -> snapshot candles -> price cache.
Heartbeats every loop (R9). Stream loss => worker exits => supervisor restarts
=> repeated loss alerts. NO silent stalls.
"""
from __future__ import annotations

import time

from src.intel.anomaly_guard import Tick


class TickFeedWorker:
    def __init__(self, *, stream_factory, guard, exit_mgr, snapshot, redis,
                 regime_fn=None, sub_bar_ticks: int = 6) -> None:
        self.stream_factory = stream_factory        # async iterator of dicts
        self.guard = guard
        self.exit_mgr = exit_mgr
        self.snapshot = snapshot
        self.redis = redis
        self.regime_fn = regime_fn or (lambda s: {"trend_state": "RANGE", "vol_regime": "NORMAL"})
        self.sub_bar = sub_bar_ticks
        self._windows: dict[str, list] = {}
        self.processed = 0

    async def run(self) -> None:
        async for tick in self.stream_factory():
            sym, px = tick["symbol"], float(tick["price"])
            ts = float(tick.get("ts", time.time()))
            await self.guard.process_tick(sym, Tick(
                ts=ts, price=px, bid=float(tick.get("bid", px)),
                ask=float(tick.get("ask", px)), volume=float(tick.get("volume", 0))))
            w = self._windows.setdefault(sym, [])
            w.append(px)
            if len(w) >= self.sub_bar:
                await self.exit_mgr.on_bar(sym, max(w), min(w), w[-1], self.regime_fn(sym))
                self.snapshot.push_candle(sym, int(ts), w[0], max(w), min(w), w[-1])
                self._windows[sym] = []
            self.processed += 1
            try:
                await self.redis.setex("heartbeat:tick_feed", 120, str(ts))
            except Exception:  # noqa: BLE001 — heartbeat loss surfaces via supervisor
                pass  # pragma: no cover
