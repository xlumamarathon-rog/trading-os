"""M35 adapter — MT5 server-side SL via the Windows exec microservice.

Short support (Aug 2026): MT5's SL is a POSITION attribute, so the service
resolves the closing side from the open position itself. The adapter accepts
`direction` (position side) for interface parity with IndiaStopAdapter and
does not need to transmit it.
"""
from __future__ import annotations


class Mt5StopAdapter:
    def __init__(self, mt5_client) -> None:
        self.client = mt5_client

    async def place_stop(self, symbol: str, qty: float, stop_price: float, leg: str,
                         *, direction: str = "buy") -> str:
        resp = await self.client.post("/position/stop", json={
            "symbol": symbol, "lots": qty, "sl": stop_price})
        resp.raise_for_status()
        return str(resp.json().get("position_id"))

    async def modify_stop(self, stop_order_id: str, new_price: float, leg: str) -> None:
        resp = await self.client.post("/position/modify", json={
            "position_id": stop_order_id, "sl": new_price})
        resp.raise_for_status()

    async def cancel_stop(self, stop_order_id: str, leg: str) -> None:
        # MT5 SL rides the position; clearing = modify sl to 0 (broker semantics)
        resp = await self.client.post("/position/modify", json={
            "position_id": stop_order_id, "sl": 0})
        resp.raise_for_status()

    async def replace_stop(self, old_id: str, symbol: str, qty: float,
                           trigger_price: float, leg: str,
                           *, direction: str = "buy") -> str:
        # Position-level SL persists across partial closes — same id remains valid.
        resp = await self.client.post("/position/modify", json={
            "position_id": old_id, "sl": trigger_price})
        resp.raise_for_status()
        return old_id

    async def exit_market(self, symbol: str, qty: float, leg: str,
                          *, direction: str = "buy") -> None:
        resp = await self.client.post("/position/close", json={"symbol": symbol, "lots": qty})
        resp.raise_for_status()
