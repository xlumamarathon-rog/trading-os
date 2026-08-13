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

    # ---- market data (Aug 2026) — the terminal's own feed is the ONLY
    # correct forex/CFD price source for anything that executes on MT5:
    # it is the broker's real bid/ask including their spread. Verified
    # against vendor source (aiomql core/meta_trader.py): symbol_info_tick
    # and copy_rates_from_pos.

    def _meta(self):
        if not hasattr(self, "_mt"):
            from aiomql import MetaTrader  # type: ignore
            self._mt = MetaTrader()
        return self._mt

    async def tick(self, symbol: str) -> Optional[dict]:
        await self._ensure()
        t = await self._meta().symbol_info_tick(symbol)
        if t is None:
            return None
        return {"symbol": symbol, "bid": float(t.bid), "ask": float(t.ask),
                "last": float(getattr(t, "last", 0.0) or (t.bid + t.ask) / 2),
                "time": int(t.time)}

    async def candles(self, symbol: str, timeframe: str, count: int) -> list:
        await self._ensure()
        from aiomql import TimeFrame  # type: ignore
        tf = getattr(TimeFrame, timeframe, None)
        if tf is None:
            raise ValueError(f"unknown timeframe {timeframe!r}")
        rates = await self._meta().copy_rates_from_pos(symbol, int(tf), 0, count)
        if rates is None:
            return []
        # ndarray columns: time, open, high, low, close, tick_volume, ...
        return [{"ts": int(r[0]), "o": float(r[1]), "h": float(r[2]),
                 "l": float(r[3]), "c": float(r[4])} for r in rates]

    # ---- history depth (Aug 2026): how far back does THIS broker's server
    # actually serve M1 bars and real ticks? SeriesInfoInteger is MQL5-only,
    # so we bisect copy_rates_range / copy_ticks_range per calendar day
    # (mt5_service/history_probe.py — pure, tested off-Windows). Verified
    # against vendor source: core/meta_trader.py copy_rates_range (:446) and
    # copy_ticks_range (:497). COPY_TICKS_INFO==1 (bid/ask changes).

    async def history_depth(self, symbol: str) -> dict:
        import datetime as _dt

        from mt5_service.history_probe import (day_bounds_epoch,
                                               earliest_available_async)
        await self._ensure()
        from aiomql import TimeFrame  # type: ignore
        meta = self._meta()

        async def m1_has(day):
            p1, p2 = day_bounds_epoch(day)
            rates = await meta.copy_rates_range(
                symbol, int(TimeFrame.M1),
                _dt.datetime.fromtimestamp(p1, _dt.timezone.utc),
                _dt.datetime.fromtimestamp(p2, _dt.timezone.utc))
            return rates is not None and len(rates) > 0

        async def tick_has(day):
            p1, p2 = day_bounds_epoch(day)
            ticks = await meta.copy_ticks_range(
                symbol,
                _dt.datetime.fromtimestamp(p1, _dt.timezone.utc),
                _dt.datetime.fromtimestamp(p2, _dt.timezone.utc),
                1)                                   # COPY_TICKS_INFO
            return ticks is not None and len(ticks) > 0

        m1_first = await earliest_available_async(m1_has)
        tick_first = await earliest_available_async(tick_has)
        return {"symbol": symbol,
                "m1_first_date": m1_first.isoformat() if m1_first else None,
                "tick_first_date": tick_first.isoformat() if tick_first else None,
                "probed_at": _dt.datetime.now(_dt.timezone.utc)
                .isoformat(timespec="seconds")}
