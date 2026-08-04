"""MODULE 35 — Adaptive Exit Manager (spec §Phase 2, NEW in v2).

One engine, two jobs: trailing take-profit AND loss-cutting, per-market profiles.
State machine per position:

  RISK_ON    stop = entry ∓ k_sl[leg]·ATR (or structure stop if wider). Time-stop counts.
  BREAKEVEN  at +breakeven_at_r·R: stop → entry, partial 1 booked. Trade risk-free.
  TRAILING   at partials[1].at_r·R: partial 2 booked; chandelier trail:
             stop = extreme ∓ k_trail[regime]·ATR, k from regime_detector
             (STRONG_TREND loose … SHOCK tight). Tighten triggers: event window,
             crypto weekend, regime SHOCK.
  EXITED     stop hit / time stop / flatten policy. Telemetry: MFE captured %.

INVARIANTS (code-enforced, property-tested):
  - the stop only EVER moves toward profit (never_widen_stop) — a widening
    attempt raises StopWidenAttempt;
  - a broker-resident stop exists from attach() (adapter called synchronously);
  - ratchets are batched: modify only when improvement ≥ min_ratchet_step_atr·ATR;
  - engine state survives restart via to_snapshot()/from_snapshot().
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


class StopWidenAttempt(RuntimeError):
    """Raised when anything tries to move a stop AWAY from profit."""


@dataclass
class ExitTelemetry:
    exit_reason: str
    exit_price: float
    realized_r: float
    mfe_r: float
    mfe_captured_pct: float


@dataclass
class ManagedPosition:
    symbol: str
    direction: str                 # "buy" | "sell"
    entry: float
    qty: float
    atr: float
    leg: str                       # india | mt5_forex | mt5_crypto
    stop: float
    r_value: float                 # initial risk per unit
    stop_order_id: str
    state: str = "RISK_ON"         # RISK_ON | BREAKEVEN | TRAILING | EXITED
    extreme: float = 0.0           # highest high (long) / lowest low (short)
    bars_no_progress: int = 0
    partials_taken: list = field(default_factory=list)
    remaining_qty: float = 0.0
    lot_size: float = 1.0
    opened_at: float = field(default_factory=time.time)
    telemetry: Optional[ExitTelemetry] = None

    @property
    def is_long(self) -> bool:
        return self.direction == "buy"


class ExitManager:
    def __init__(self, exit_cfg: dict, stop_adapter, on_partial=None, on_exit=None) -> None:
        self.cfg = exit_cfg
        self.adapter = stop_adapter          # place_stop/modify_stop/exit_market (per leg)
        self.on_partial = on_partial
        self.on_exit = on_exit
        self.positions: dict[str, ManagedPosition] = {}
        self.modify_calls = 0

    # ---------- attach (stop resident at broker immediately) ----------

    async def attach(self, *, symbol: str, direction: str, entry: float, qty: float,
                     atr: float, leg: str, structure_stop: Optional[float] = None,
                     lot_size: float = 1.0) -> ManagedPosition:
        if entry <= 0 or qty <= 0 or atr <= 0 or lot_size <= 0:
            raise ValueError("invalid attach inputs")
        k_sl = float(self.cfg["k_sl_initial"][leg])
        dist = k_sl * atr
        if structure_stop is not None:
            struct_dist = abs(entry - structure_stop)
            dist = max(dist, struct_dist)          # widest protective distance wins
        stop = entry - dist if direction == "buy" else entry + dist
        stop_order_id = await self.adapter.place_stop(symbol, qty, stop, leg)  # ≤2s rule
        pos = ManagedPosition(
            symbol=symbol, direction=direction, entry=entry, qty=qty, atr=atr, leg=leg,
            stop=stop, r_value=abs(entry - stop), stop_order_id=stop_order_id,
            extreme=entry, remaining_qty=qty, lot_size=lot_size,
        )
        self.positions[symbol] = pos
        return pos

    # ---------- the only way a stop moves ----------

    async def _ratchet_stop(self, pos: ManagedPosition, candidate: float, force: bool = False) -> bool:
        """Move stop toward profit only. Returns True if broker modify happened."""
        improving = candidate > pos.stop if pos.is_long else candidate < pos.stop
        if not improving:
            if force:
                raise StopWidenAttempt(
                    f"{pos.symbol}: refusing stop {pos.stop} → {candidate} (never_widen_stop)"
                )
            return False
        step = abs(candidate - pos.stop)
        if step < float(self.cfg["min_ratchet_step_atr"]) * pos.atr and pos.state != "RISK_ON":
            return False  # batch tiny ratchets — don't burn broker rate limits
        pos.stop = candidate
        await self.adapter.modify_stop(pos.stop_order_id, candidate, pos.leg)
        self.modify_calls += 1
        return True

    async def force_stop_change(self, symbol: str, new_stop: float) -> None:
        """External/manual stop change — still subject to the invariant."""
        pos = self.positions[symbol]
        await self._ratchet_stop(pos, new_stop, force=True)

    # ---------- helpers ----------

    def _r_multiple(self, pos: ManagedPosition, price: float) -> float:
        move = (price - pos.entry) if pos.is_long else (pos.entry - price)
        return move / pos.r_value if pos.r_value else 0.0

    def _k_trail(self, regime: dict, event_minutes: Optional[float], leg: str,
                 crypto_weekend: bool) -> float:
        k = float(self.cfg["k_trail_by_regime"].get(regime.get("trend_state", "RANGE"),
                                                    self.cfg["k_trail_by_regime"]["RANGE"]))
        if regime.get("vol_regime") == "SHOCK":
            k = float(self.cfg["k_trail_by_regime"]["SHOCK"])
        if event_minutes is not None and event_minutes <= float(self.cfg["event_tighten_minutes"]):
            k /= 2.0
        if crypto_weekend and leg == "mt5_crypto" and self.cfg["crypto_weekend_policy"] == "tighten":
            k /= 2.0
        return k

    async def _take_partial(self, pos: ManagedPosition, at_r: float, pct: float, price: float) -> None:
        """Partials are REAL broker orders: lot-floored, executed via the adapter,
        and the resting stop is re-placed for the remaining quantity."""
        lot = getattr(pos, "lot_size", 1.0)
        import math as _math
        qty = _math.floor((pos.qty * pct / 100.0) / lot) * lot
        pos.partials_taken.append(at_r)
        if qty <= 0:
            return  # position too small to carve a lot — ladder skipped, stop still protects
        await self.adapter.exit_market(pos.symbol, qty, pos.leg)
        pos.remaining_qty = max(0.0, pos.remaining_qty - qty)
        if pos.remaining_qty > 0 and hasattr(self.adapter, "replace_stop"):
            pos.stop_order_id = await self.adapter.replace_stop(
                pos.stop_order_id, pos.symbol, pos.remaining_qty, pos.stop, pos.leg)
        if self.on_partial:
            await self.on_partial(pos.symbol, qty, price, at_r)

    async def _exit(self, pos: ManagedPosition, price: float, reason: str) -> None:
        mfe_r = self._r_multiple(pos, pos.extreme)
        realized_r = self._r_multiple(pos, price)
        pos.telemetry = ExitTelemetry(
            exit_reason=reason, exit_price=price, realized_r=realized_r, mfe_r=mfe_r,
            mfe_captured_pct=(realized_r / mfe_r * 100.0) if mfe_r > 0 else 0.0,
        )
        pos.state = "EXITED"
        if reason == "stop_hit":
            # The BROKER-resident stop already closed the remainder server-side.
            # Selling again here would double-exit (integration-test-caught bug).
            pass_through = True
        else:
            # Active exit: cancel the resting stop FIRST, then market-out.
            if hasattr(self.adapter, "cancel_stop"):
                try:
                    await self.adapter.cancel_stop(pos.stop_order_id, pos.leg)
                except Exception as exc:  # noqa: BLE001 — R5: log, still exit the position
                    logger.error("cancel_stop failed for %s: %s", pos.symbol, exc)
            import math as _math
            lot = getattr(pos, "lot_size", 1.0)
            qty = _math.floor(pos.remaining_qty / lot) * lot
            if qty > 0:
                await self.adapter.exit_market(pos.symbol, qty, pos.leg)
        if self.on_exit:
            await self.on_exit(pos.symbol, pos.telemetry)

    # ---------- per-bar lifecycle ----------

    async def on_bar(self, symbol: str, high: float, low: float, close: float,
                     regime: dict, event_minutes: Optional[float] = None,
                     crypto_weekend: bool = False) -> list[str]:
        pos = self.positions.get(symbol)
        if pos is None or pos.state == "EXITED":
            return []
        import math as _m
        if not all(isinstance(v, (int, float)) and _m.isfinite(v)
                   for v in (high, low, close)) or high < low:
            logger.error("corrupt bar for %s skipped: %s", symbol, (high, low, close))
            return ["skipped:corrupt_bar"]      # a bad feed row must never move a stop
        actions: list[str] = []

        # 0. crypto weekend flatten policy
        if (crypto_weekend and pos.leg == "mt5_crypto"
                and self.cfg["crypto_weekend_policy"] == "flatten"):
            await self._exit(pos, close, "crypto_weekend_flatten")
            return ["exit:crypto_weekend_flatten"]

        # 1. stop hit? (broker fills it server-side; we account for it)
        hit = low <= pos.stop if pos.is_long else high >= pos.stop
        if hit:
            await self._exit(pos, pos.stop, "stop_hit")
            return ["exit:stop_hit"]

        # 2. extremes + progress
        new_extreme = max(pos.extreme, high) if pos.is_long else min(pos.extreme, low)
        if new_extreme != pos.extreme:
            pos.extreme = new_extreme
            pos.bars_no_progress = 0
        else:
            pos.bars_no_progress += 1

        # 3. time stop (frees capital; caps swap bleed on crypto CFDs)
        if pos.bars_no_progress >= int(self.cfg["max_bars_no_progress"][pos.leg]):
            await self._exit(pos, close, "time_stop_no_progress")
            return ["exit:time_stop"]

        r_now = self._r_multiple(pos, close)
        partial_cfgs = self.cfg["partials"]

        # 4. breakeven + partial 1 (partials may legitimately be empty:
        #    breakeven ratchet + trailing still apply, runner keeps full size)
        if pos.state == "RISK_ON" and r_now >= float(self.cfg["breakeven_at_r"]):
            if await self._ratchet_stop(pos, pos.entry):
                actions.append("stop_to_breakeven")
            if partial_cfgs:
                p1 = partial_cfgs[0]
                if float(p1["at_r"]) not in pos.partials_taken:
                    await self._take_partial(pos, float(p1["at_r"]), float(p1["pct"]), close)
                    actions.append("partial_1")
            pos.state = "BREAKEVEN"

        # 5. partial 2 → TRAILING
        if pos.state == "BREAKEVEN" and len(partial_cfgs) > 1 and \
                r_now >= float(partial_cfgs[1]["at_r"]):
            p2 = partial_cfgs[1]
            if float(p2["at_r"]) not in pos.partials_taken:
                await self._take_partial(pos, float(p2["at_r"]), float(p2["pct"]), close)
                actions.append("partial_2")
            pos.state = "TRAILING"

        # 6. chandelier trail (runner)
        if pos.state in ("BREAKEVEN", "TRAILING"):
            k = self._k_trail(regime, event_minutes, pos.leg, crypto_weekend)
            chandelier = (pos.extreme - k * pos.atr) if pos.is_long else (pos.extreme + k * pos.atr)
            if await self._ratchet_stop(pos, chandelier):
                actions.append(f"trail:{k:g}xATR")

        return actions

    # ---------- restart recovery ----------

    def to_snapshot(self) -> dict:
        out = {}
        for sym, p in self.positions.items():
            out[sym] = {k: v for k, v in p.__dict__.items() if k != "telemetry"}
        return out

    @classmethod
    def from_snapshot(cls, snapshot: dict, exit_cfg: dict, stop_adapter,
                      on_partial=None, on_exit=None) -> "ExitManager":
        mgr = cls(exit_cfg, stop_adapter, on_partial, on_exit)
        for sym, fields_ in snapshot.items():
            mgr.positions[sym] = ManagedPosition(**fields_)
        return mgr

    def naked_positions(self) -> list[str]:
        """Reconciler check: every live position must carry a stop order id."""
        return [s for s, p in self.positions.items()
                if p.state != "EXITED" and not p.stop_order_id]
