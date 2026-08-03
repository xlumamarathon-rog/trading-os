"""MODULE 36 — Anomaly Guard (spec §Phase 1 v2 — Tier 0, millisecond shock reflex).

Watches tick streams per symbol. Fires on:
  - velocity: |return over 1s/5s/30s| > k_h × baseline sigma for that horizon
  - spread blowout: (ask-bid) > mult × baseline median spread
  - volume spike: rolling 30s volume > mult × baseline 30s volume

Actions on shock (all local, no external calls in the hot path):
  - set PAUSE_ENTRIES flag in Redis (with cooloff TTL); order_router checks it
  - invoke injected callbacks: cancel_entry_orders / tighten_exits
    (NEVER cancels protective stops — callback contract, asserted in tests)

Invariants (spec §12.3): model-free; cannot be vetoed by any model output.
Fail-closed: if Redis is unreachable, entries are considered PAUSED, and the
shock event is still recorded locally.

NOTE Wave-2 scope: baseline sigmas/medians are primed externally (prime()); the
self-estimating EWMA baseline updater lands with MODULE 34 in Wave 3.
"""
from __future__ import annotations

import inspect
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

PAUSE_ENTRIES_KEY = "PAUSE_ENTRIES"


async def _maybe_await(result):
    if inspect.isawaitable(result):
        return await result
    return result


@dataclass
class Tick:
    ts: float
    price: float
    bid: float
    ask: float
    volume: float = 0.0


@dataclass
class ShockEvent:
    symbol: str
    trigger: str
    ts: float
    details: dict = field(default_factory=dict)


@dataclass
class _Baseline:
    sigma: dict  # horizon_seconds -> baseline sigma of returns over that horizon
    median_spread: float
    volume_30s: float


class _TickWindow:
    def __init__(self, max_age_seconds: float) -> None:
        self.ticks: deque[Tick] = deque()
        self.max_age = max_age_seconds

    def add(self, tick: Tick) -> None:
        self.ticks.append(tick)
        cutoff = tick.ts - self.max_age
        while self.ticks and self.ticks[0].ts < cutoff:
            self.ticks.popleft()

    def return_over(self, seconds: float) -> Optional[float]:
        if not self.ticks:
            return None
        last = self.ticks[-1]
        target = last.ts - seconds
        base: Optional[Tick] = None
        for t in self.ticks:
            if t.ts <= target:
                base = t
            else:
                break
        if base is None or base.price <= 0:
            return None
        return (last.price - base.price) / base.price

    def volume_over(self, seconds: float) -> float:
        if not self.ticks:
            return 0.0
        cutoff = self.ticks[-1].ts - seconds
        return sum(t.volume for t in self.ticks if t.ts >= cutoff)


class AnomalyGuard:
    def __init__(
        self,
        redis,
        velocity_sigma: dict,          # {"s1": 6, "s5": 5, "s30": 4} from config
        spread_blowout_mult: float,
        volume_spike_mult: float,
        cooloff_minutes: float,
        cancel_entry_orders: Optional[Callable] = None,   # NEVER cancels stops
        tighten_exits: Optional[Callable] = None,
        alert_fn: Optional[Callable] = None,
    ) -> None:
        self.redis = redis
        self.k = {1.0: velocity_sigma["s1"], 5.0: velocity_sigma["s5"], 30.0: velocity_sigma["s30"]}
        self.spread_mult = spread_blowout_mult
        self.volume_mult = volume_spike_mult
        self.cooloff_seconds = cooloff_minutes * 60.0
        self._cancel_entries = cancel_entry_orders
        self._tighten_exits = tighten_exits
        self._alert = alert_fn
        self._windows: dict[str, _TickWindow] = {}
        self._baselines: dict[str, _Baseline] = {}
        self._last_shock_ts: dict[str, float] = {}
        self.events: list[ShockEvent] = []  # local record — survives Redis loss

    # ---------- baselines (primed now; EWMA self-update in Wave 3 w/ M34) ----------

    def prime(
        self,
        symbol: str,
        sigma_1s: float,
        sigma_5s: float,
        sigma_30s: float,
        median_spread: float,
        volume_30s_baseline: float,
    ) -> None:
        self._baselines[symbol] = _Baseline(
            sigma={1.0: sigma_1s, 5.0: sigma_5s, 30.0: sigma_30s},
            median_spread=median_spread,
            volume_30s=volume_30s_baseline,
        )

    # ---------- hot path ----------

    async def process_tick(self, symbol: str, tick: Tick) -> list[ShockEvent]:
        window = self._windows.setdefault(symbol, _TickWindow(max_age_seconds=60.0))
        window.add(tick)
        baseline = self._baselines.get(symbol)
        if baseline is None:
            return []  # unprimed symbol — no detection yet (Wave 3 self-priming)

        triggers: list[ShockEvent] = []

        for horizon, k in self.k.items():
            sigma = baseline.sigma.get(horizon, 0.0)
            if sigma <= 0:
                continue
            ret = window.return_over(horizon)
            if ret is not None and abs(ret) > k * sigma:
                triggers.append(
                    ShockEvent(symbol, f"velocity_{int(horizon)}s", tick.ts,
                               {"return": ret, "threshold": k * sigma})
                )
                break  # one velocity trigger is enough

        spread = max(0.0, tick.ask - tick.bid)
        if baseline.median_spread > 0 and spread > self.spread_mult * baseline.median_spread:
            triggers.append(
                ShockEvent(symbol, "spread_blowout", tick.ts,
                           {"spread": spread, "baseline": baseline.median_spread})
            )

        vol_30s = window.volume_over(30.0)
        if baseline.volume_30s > 0 and vol_30s > self.volume_mult * baseline.volume_30s:
            triggers.append(
                ShockEvent(symbol, "volume_spike", tick.ts,
                           {"vol_30s": vol_30s, "baseline": baseline.volume_30s})
            )

        if not triggers:
            return []

        # cooloff: don't re-fire actions for the same symbol within the window
        last = self._last_shock_ts.get(symbol)
        if last is not None and (tick.ts - last) < self.cooloff_seconds:
            return []
        self._last_shock_ts[symbol] = tick.ts

        await self._act_on_shock(symbol, triggers)
        return triggers

    # ---------- actions ----------

    async def _act_on_shock(self, symbol: str, triggers: list[ShockEvent]) -> None:
        self.events.extend(triggers)  # local record FIRST — survives Redis loss
        try:
            await self.redis.setex(PAUSE_ENTRIES_KEY, int(self.cooloff_seconds), "1")
        except Exception as exc:  # noqa: BLE001 — recorded; pause reads are fail-closed anyway
            self.events.append(ShockEvent(symbol, "redis_pause_set_failed", time.time(), {"error": str(exc)}))
        if self._cancel_entries is not None:
            try:
                await _maybe_await(self._cancel_entries(symbol))
            except Exception as exc:  # noqa: BLE001
                self.events.append(ShockEvent(symbol, "cancel_entries_failed", time.time(), {"error": str(exc)}))
        if self._tighten_exits is not None:
            try:
                await _maybe_await(self._tighten_exits(symbol))
            except Exception as exc:  # noqa: BLE001
                self.events.append(ShockEvent(symbol, "tighten_exits_failed", time.time(), {"error": str(exc)}))
        if self._alert is not None:
            try:
                await _maybe_await(self._alert(symbol, [t.trigger for t in triggers]))
            except Exception as exc:  # noqa: BLE001
                self.events.append(ShockEvent(symbol, "alert_failed", time.time(), {"error": str(exc)}))

    # ---------- read side (used by order_router) ----------

    async def entries_paused(self) -> bool:
        """FAIL-CLOSED: Redis unreachable ⇒ treated as paused."""
        try:
            flag = await self.redis.get(PAUSE_ENTRIES_KEY)
        except Exception:
            return True
        return flag in ("1", b"1", 1, True)
