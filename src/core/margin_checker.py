"""MODULE 42 — Margin / Funds Pre-Check (spec §Phase 1, NEW in v2).

Fail-closed: if the margin API is missing or unreachable, the order is REJECTED.
India: broker-required margin + configured headroom buffer; F&O lot validation.
MT5: post-trade free margin must remain >= configured % of equity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.core.config_loader import RiskLimits


@dataclass
class MarginDecision:
    ok: bool
    reason: str
    required: Optional[float] = None
    available: Optional[float] = None


class MarginChecker:
    def __init__(self, risk: RiskLimits, india_api=None, mt5_api=None) -> None:
        self.risk = risk
        self.india_api = india_api
        self.mt5_api = mt5_api

    async def check_india(
        self, symbol: str, qty: float, price: float, product: str = "delivery", lot_size: int = 1
    ) -> MarginDecision:
        if qty <= 0 or price <= 0:
            return MarginDecision(False, "invalid_qty_or_price")
        if lot_size > 1 and int(qty) % int(lot_size) != 0:
            return MarginDecision(False, "lot_size_mismatch")
        if self.india_api is None:
            return MarginDecision(False, "no_margin_api_fail_closed")
        try:
            available = await self.india_api.available_margin()
            required = await self.india_api.required_margin(symbol, qty, price, product)
        except Exception as exc:  # noqa: BLE001 — fail-closed by design (R4)
            return MarginDecision(False, f"margin_api_unreachable_fail_closed:{exc}")
        need = required * (1.0 + self.risk.margin_buffer_india)
        if available >= need:
            return MarginDecision(True, "ok", required=need, available=available)
        return MarginDecision(False, "insufficient_margin", required=need, available=available)

    async def check_mt5(self, symbol: str, lots: float) -> MarginDecision:
        if lots <= 0:
            return MarginDecision(False, "invalid_lots")
        if self.mt5_api is None:
            return MarginDecision(False, "no_margin_api_fail_closed")
        try:
            free = await self.mt5_api.free_margin()
            equity = await self.mt5_api.equity()
            required = await self.mt5_api.margin_required(symbol, lots)
        except Exception as exc:  # noqa: BLE001 — fail-closed by design (R4)
            return MarginDecision(False, f"margin_api_unreachable_fail_closed:{exc}")
        if equity <= 0:
            return MarginDecision(False, "non_positive_equity")
        post_free_ratio = (free - required) / equity
        if post_free_ratio >= self.risk.mt5_min_free_margin_pct:
            return MarginDecision(True, "ok", required=required, available=free)
        return MarginDecision(
            False, "free_margin_below_floor", required=required, available=free
        )
