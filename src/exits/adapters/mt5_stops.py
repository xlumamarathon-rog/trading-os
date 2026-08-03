"""M35 adapter — MT5 server-side SL via the Windows exec microservice."""
from __future__ import annotations


class Mt5StopAdapter:
    def __init__(self, mt5_client) -> None:
        self.client = mt5_client

    async def place_stop(self, symbol: str, qty: float, stop_price: float, leg: str) -> str:
        resp = await self.client.post("/position/stop", json={
            "symbol": symbol, "lots": qty, "sl": stop_price})
        resp.raise_for_status()
        return str(resp.json().get("position_id"))

    async def modify_stop(self, stop_order_id: str, new_price: float, leg: str) -> None:
        resp = await self.client.post("/position/modify", json={
            "position_id": stop_order_id, "sl": new_price})
        resp.raise_for_status()

    async def exit_market(self, symbol: str, qty: float, leg: str) -> None:
        resp = await self.client.post("/position/close", json={"symbol": symbol, "lots": qty})
        resp.raise_for_status()
