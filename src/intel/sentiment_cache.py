"""MODULE 11 — Sentiment signal cache (spec §Phase 2, v2: event-driven invalidation).

The execution path calls get_cached_signal() ONLY — it can never await an LLM
(there is no LLM handle in this module's read side, by construction). The
precompute loop (background) writes signals; Tier-2 severity invalidates keys.
"""
from __future__ import annotations

import json
import time
from typing import Optional

SIGNAL_PREFIX = "signal:"


class SentimentCache:
    def __init__(self, redis, ttl_seconds: int) -> None:
        self.redis = redis
        self.ttl = ttl_seconds
        self.hits = 0
        self.misses = 0

    async def get_cached_signal(self, ticker: str) -> Optional[dict]:
        """Hot-path read. Fail-closed: Redis error => None (router rejects)."""
        try:
            raw = await self.redis.get(SIGNAL_PREFIX + ticker)
        except Exception:
            self.misses += 1
            return None
        if raw is None:
            self.misses += 1
            return None
        signal = json.loads(raw)
        if time.time() - signal.get("computed_at", 0) > self.ttl:
            self.misses += 1
            return None  # stale — treat as miss (stale_signal_cache failure class)
        self.hits += 1
        return signal

    async def store_signal(self, ticker: str, direction: str, confidence: float,
                           source: str = "trading_agents") -> dict:
        signal = {"ticker": ticker, "direction": direction, "confidence": confidence,
                  "source": source, "computed_at": time.time()}
        await self.redis.setex(SIGNAL_PREFIX + ticker, self.ttl, json.dumps(signal))
        return signal

    async def invalidate(self, tickers: list) -> None:
        """Event-driven invalidation (Tier-2 severity >= threshold)."""
        for t in tickers:
            await self.redis.delete(SIGNAL_PREFIX + t)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    async def precompute_loop_once(self, watchlist: list, compute_fn) -> int:
        """Background only. compute_fn(ticker) -> (direction, confidence)."""
        count = 0
        for ticker in watchlist:
            direction, confidence = await compute_fn(ticker)
            await self.store_signal(ticker, direction, confidence)
            count += 1
        return count
