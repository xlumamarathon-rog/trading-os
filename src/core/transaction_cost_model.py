"""MODULE 40 — Transaction Cost Model (spec §Phase 1).

Full cost of any hypothetical trade:
- India: brokerage + STT + exchange txn + stamp duty + GST (per config schedule)
- Market impact: square-root law  ΔP/P ≈ Y · σ_daily · √(Q / ADV)
- MT5: spread + swap holding cost

Consumers: position_sizer (net-edge gate), rebalance_scheduler, every backtest
(build-plan lint L4), MODULE 38's after-cost promotion gate.
All rates come from config — nothing hardcoded here (R3).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.core.config_loader import ImpactModel, IndiaCosts

VALID_SIDES = ("buy", "sell")
VALID_PRODUCTS = ("delivery", "intraday")


@dataclass
class CostBreakdown:
    notional: float
    components: dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return float(sum(self.components.values()))

    def as_dict(self) -> dict:
        return {"notional": self.notional, "components": dict(self.components), "total": self.total}


def india_trade_cost(
    costs: IndiaCosts, side: str, qty: float, price: float, product: str = "delivery"
) -> CostBreakdown:
    if side not in VALID_SIDES:
        raise ValueError(f"side must be one of {VALID_SIDES}")
    if product not in VALID_PRODUCTS:
        raise ValueError(f"product must be one of {VALID_PRODUCTS}")
    if qty <= 0 or price <= 0:
        return CostBreakdown(notional=0.0)

    notional = qty * price
    brokerage = costs.brokerage_flat
    if product == "delivery":
        stt = notional * costs.stt_delivery_pct  # both sides
    else:
        stt = notional * costs.stt_intraday_sell_pct if side == "sell" else 0.0
    exchange = notional * costs.exchange_txn_pct
    stamp = notional * costs.stamp_duty_pct if side == "buy" else 0.0
    gst = costs.gst_pct * (brokerage + exchange)

    return CostBreakdown(
        notional=notional,
        components={
            "brokerage": brokerage,
            "stt": stt,
            "exchange_txn": exchange,
            "stamp_duty": stamp,
            "gst": gst,
        },
    )


def india_round_trip_cost(
    costs: IndiaCosts, qty: float, price_in: float, price_out: float, product: str = "delivery"
) -> float:
    buy = india_trade_cost(costs, "buy", qty, price_in, product)
    sell = india_trade_cost(costs, "sell", qty, price_out, product)
    return buy.total + sell.total


def impact_cost(
    impact: ImpactModel, qty: float, price: float, adv_shares: float, daily_sigma: float
) -> float:
    """Square-root market impact, in currency.

    Conservative convention: the full impact fraction is applied to the whole
    quantity (upper bound vs the Almgren average-price half-factor).
    """
    if qty <= 0 or price <= 0 or adv_shares <= 0 or daily_sigma < 0:
        return 0.0
    frac = impact.y_coefficient * daily_sigma * math.sqrt(qty / adv_shares)
    return frac * qty * price


def impact_fraction(impact: ImpactModel, qty: float, adv_shares: float, daily_sigma: float) -> float:
    if qty <= 0 or adv_shares <= 0 or daily_sigma < 0:
        return 0.0
    return impact.y_coefficient * daily_sigma * math.sqrt(qty / adv_shares)


def mt5_trade_cost(
    lots: float,
    spread_points: float,
    point_value_per_lot: float,
    swap_cost_per_lot_per_day: float = 0.0,
    hold_days: float = 0.0,
) -> CostBreakdown:
    if lots <= 0:
        return CostBreakdown(notional=0.0)
    spread = spread_points * point_value_per_lot * lots
    swap = max(0.0, swap_cost_per_lot_per_day) * lots * max(0.0, hold_days)
    return CostBreakdown(notional=lots, components={"spread": spread, "swap": swap})


def net_edge(expected_profit: float, total_cost: float) -> float:
    """Positive ⇒ trade is worth doing after costs."""
    return expected_profit - total_cost
