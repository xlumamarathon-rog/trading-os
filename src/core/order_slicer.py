"""MODULE 51 — Execution slicer (Aug 2026).

Large orders move markets. The impact model (transaction_cost_model) only
ESTIMATES that damage — this module MANAGES it by splitting a parent order
into TWAP child slices capped at a participation rate of expected volume.

Pure planning module: the router/executor owns the clock and the sends.
Lot-flooring is respected so every child slice is executable; the remainder
rides on the last slice."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class SlicePlan:
    slices: list                    # [qty, ...] executable child quantities
    n_slices: int
    participation_pct: float        # expected share of interval volume
    reason: str


def plan_slices(*, qty: float, adv: float, lot_size: float = 1.0,
                max_participation_pct: float = 0.05,
                bar_fraction_of_day: float = 1.0 / 75,
                max_slices: int = 20) -> SlicePlan:
    """TWAP plan: cap each child at max_participation_pct of the volume
    expected during one execution interval (bar_fraction_of_day of ADV;
    default = one 5-minute bar of a 375-minute NSE session)."""
    if qty <= 0 or lot_size <= 0:
        return SlicePlan([], 0, 0.0, "invalid_qty_or_lot")
    if adv <= 0 or not math.isfinite(adv):
        return SlicePlan([], 0, 0.0, "invalid_adv")

    interval_volume = adv * bar_fraction_of_day
    max_child = max(lot_size,
                    math.floor((interval_volume * max_participation_pct) / lot_size) * lot_size)
    n = min(max_slices, max(1, math.ceil(qty / max_child)))
    base = math.floor((qty / n) / lot_size) * lot_size
    if base <= 0:
        # order too small to split at this lot size — one executable slice
        whole = math.floor(qty / lot_size) * lot_size
        if whole <= 0:
            return SlicePlan([], 0, 0.0, "below_one_lot")
        return SlicePlan([whole], 1, whole / interval_volume, "single_slice")

    slices = [base] * n
    remainder = math.floor((qty - base * n) / lot_size) * lot_size
    slices[-1] += remainder
    return SlicePlan(slices, n, slices[0] / interval_volume,
                     "twap" if n > 1 else "single_slice")
