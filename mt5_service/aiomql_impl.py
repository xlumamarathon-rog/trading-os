"""Real MT5 interface for mt5_service — wired to the VERIFIED aiomql API (R1).

Verified against vendor/aiomql/src/aiomql (2026-08):
  - lib/bot.py:   Bot,  async initialize()
  - lib/order.py: Order(**kwargs), async send();  Order.send_order(request=...)
Runs ONLY on the Windows VPS (aiomql -> MetaTrader5 pip pkg is Windows-only).
Lazy import: constructing this class off-Windows raises a clear error (R4).
"""
from __future__ import annotations

from typing import Optional


class AiomqlUnavailable(RuntimeError):
    pass


class Mt5Aiomql:
    """Implements the interface mt5_service/app.py expects."""

    def __init__(self) -> None:
        try:
            from aiomql import Bot, Order, TradeAction, OrderType  # type: ignore
        except ImportError as exc:
            raise AiomqlUnavailable(
                "aiomql/MetaTrader5 not importable — this service runs on the "
                "Windows VPS only (DEPLOY.md Phase A step 2)") from exc
        self._Bot, self._Order = Bot, Order
        self._TradeAction, self._OrderType = TradeAction, OrderType
        self._bot = None
        self._orders: dict = {}

    async def _ensure(self):
        if self._bot is None:
            self._bot = self._Bot()
            await self._bot.initialize()               # verified: Bot.initialize()
        return self._bot

    async def is_connected(self) -> bool:
        try:
            await self._ensure()
            return True
        except Exception:  # noqa: BLE001 — surfaced as 503 by app.py (fail-closed)
            return False

    async def place_order(self, body: dict) -> dict:
        await self._ensure()
        order = self._Order(                            # verified: Order(**kwargs).send()
            symbol=body["symbol"],
            volume=float(body["qty"]),
            type=self._OrderType.BUY if body["direction"] == "buy" else self._OrderType.SELL,
        )
        result = await order.send()
        out = {"broker_order_id": str(getattr(result, "order", "")),
               "filled_qty": float(getattr(result, "volume", body["qty"])),
               "avg_price": float(getattr(result, "price", 0.0))}
        self._orders[body["client_order_id"]] = out
        return out

    async def lookup_order(self, client_order_id: str) -> Optional[dict]:
        found = self._orders.get(client_order_id)
        if found is None:
            return None
        return {"broker_order_id": found["broker_order_id"], "status": "filled",
                "filled_qty": found["filled_qty"], "avg_price": found["avg_price"]}

    async def set_stop(self, symbol: str, lots: float, sl: float) -> str:
        await self._ensure()
        order = self._Order(symbol=symbol, volume=float(lots),
                            type=self._OrderType.SELL, sl=float(sl))
        result = await order.send()
        return str(getattr(result, "order", ""))

    async def modify_stop(self, position_id: str, sl: float) -> None:
        await self._ensure()
        await self._Order.send_order(request={          # verified: classmethod send_order
            "action": self._TradeAction.SLTP, "position": int(position_id),
            "sl": float(sl)})

    async def close(self, symbol: str, lots: float) -> None:
        await self._ensure()
        order = self._Order(symbol=symbol, volume=float(lots), type=self._OrderType.SELL)
        await order.send()
