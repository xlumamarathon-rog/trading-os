"""Composite stop adapter — routes exit-engine broker calls by leg (M35).

india      -> IndiaStopAdapter (OpenAlgo SL-M, integer quantities)
mt5_forex  -> Mt5StopAdapter   (server-side SL on position, fractional lots)
mt5_crypto -> Mt5StopAdapter

Discovered by the paper simulation: a single-leg adapter sent 0.62 BTC lots to
the NSE integer-only path. The composite makes leg routing structural.
"""
from __future__ import annotations


class CompositeStopAdapter:
    def __init__(self, india_adapter, mt5_adapter) -> None:
        self._india = india_adapter
        self._mt5 = mt5_adapter

    def _pick(self, leg: str):
        return self._india if leg == "india" else self._mt5

    async def place_stop(self, symbol, qty, stop_price, leg, *, direction="buy"):
        return await self._pick(leg).place_stop(symbol, qty, stop_price, leg,
                                                direction=direction)

    async def modify_stop(self, stop_order_id, new_price, leg):
        return await self._pick(leg).modify_stop(stop_order_id, new_price, leg)

    async def cancel_stop(self, stop_order_id, leg):
        adapter = self._pick(leg)
        if hasattr(adapter, "cancel_stop"):
            return await adapter.cancel_stop(stop_order_id, leg)

    async def replace_stop(self, old_id, symbol, qty, trigger, leg, *, direction="buy"):
        adapter = self._pick(leg)
        if hasattr(adapter, "replace_stop"):
            return await adapter.replace_stop(old_id, symbol, qty, trigger, leg,
                                              direction=direction)
        return old_id

    async def exit_market(self, symbol, qty, leg, *, direction="buy"):
        return await self._pick(leg).exit_market(symbol, qty, leg,
                                                 direction=direction)
