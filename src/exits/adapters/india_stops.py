"""M35 adapter — India resting SL orders via OpenAlgo client (broker-resident)."""
from __future__ import annotations

import uuid


class IndiaStopAdapter:
    """Thin, rate-limit-aware wrapper. Manager owns ratchet batching."""

    def __init__(self, openalgo_client) -> None:
        self.client = openalgo_client

    async def place_stop(self, symbol: str, qty: float, stop_price: float, leg: str) -> str:
        resp = await self.client.post("/api/v1/placeorder", json={
            "client_order_id": uuid.uuid4().hex, "symbol": symbol, "qty": qty,
            "order_type": "SL-M", "trigger_price": stop_price, "direction": "sell"})
        resp.raise_for_status()
        return str(resp.json().get("broker_order_id"))

    async def modify_stop(self, stop_order_id: str, new_price: float, leg: str) -> None:
        resp = await self.client.post("/api/v1/modifyorder", json={
            "order_id": stop_order_id, "trigger_price": new_price})
        resp.raise_for_status()

    async def exit_market(self, symbol: str, qty: float, leg: str) -> None:
        resp = await self.client.post("/api/v1/placeorder", json={
            "client_order_id": uuid.uuid4().hex, "symbol": symbol, "qty": qty,
            "order_type": "MARKET", "direction": "sell"})
        resp.raise_for_status()
