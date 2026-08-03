"""Wave 0 — mock fixtures (build plan §Wave 0).

Realistic async fakes for Redis and both broker legs, including failure modes:
connection loss, per-order cancel failures, margin API outages.
"""
from __future__ import annotations


class FakeRedis:
    """Minimal async Redis stand-in (get/set/setex/delete)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value) -> None:
        self.store[key] = value

    async def setex(self, key: str, ttl: int, value) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


class FailingRedis:
    """Simulates Redis being unreachable — every call raises."""

    async def get(self, key: str):
        raise ConnectionError("redis down")

    async def set(self, key: str, value) -> None:
        raise ConnectionError("redis down")

    async def delete(self, key: str) -> None:
        raise ConnectionError("redis down")


class MockBroker:
    """Broker leg fake (works for both OpenAlgo-style and MT5-service-style).

    fail_cancel_ids / fail_close_ids simulate partial infrastructure failure
    mid-kill (chaos case: one cancel fails, the rest must proceed).
    """

    def __init__(
        self,
        name: str = "mock",
        orders: list[dict] | None = None,
        positions: list[dict] | None = None,
        fail_cancel_ids: set | None = None,
        fail_close_ids: set | None = None,
    ) -> None:
        self.name = name
        self.orders = list(orders or [])
        self.positions = list(positions or [])
        self.cancelled: list = []
        self.closed: list = []
        self.fail_cancel_ids = set(fail_cancel_ids or set())
        self.fail_close_ids = set(fail_close_ids or set())

    async def get_open_orders(self) -> list[dict]:
        return list(self.orders)

    async def get_open_positions(self) -> list[dict]:
        return list(self.positions)

    async def cancel_order(self, order_id) -> None:
        if order_id in self.fail_cancel_ids:
            raise RuntimeError(f"cancel failed for {order_id}")
        self.cancelled.append(order_id)
        self.orders = [o for o in self.orders if o["id"] != order_id]

    async def close_position_market(self, position_id) -> None:
        if position_id in self.fail_close_ids:
            raise RuntimeError(f"close failed for {position_id}")
        self.closed.append(position_id)
        self.positions = [p for p in self.positions if p["id"] != position_id]


class BrokerDown:
    """Broker leg completely unreachable."""

    async def get_open_orders(self):
        raise ConnectionError("broker down")

    async def get_open_positions(self):
        raise ConnectionError("broker down")


class MockMarginAPI:
    """Margin API fake for MODULE 42 tests."""

    def __init__(
        self,
        available: float = 1_000_000.0,
        required: float = 100_000.0,
        free: float = 50_000.0,
        equity_value: float = 100_000.0,
        fail: bool = False,
    ) -> None:
        self._available = available
        self._required = required
        self._free = free
        self._equity = equity_value
        self.fail = fail

    def _maybe_fail(self) -> None:
        if self.fail:
            raise ConnectionError("margin api down")

    async def available_margin(self) -> float:
        self._maybe_fail()
        return self._available

    async def required_margin(self, symbol, qty, price, product) -> float:
        self._maybe_fail()
        return self._required

    async def free_margin(self) -> float:
        self._maybe_fail()
        return self._free

    async def equity(self) -> float:
        self._maybe_fail()
        return self._equity

    async def margin_required(self, symbol, lots) -> float:
        self._maybe_fail()
        return self._required
