"""Paper Broker Engine — production paper-trading core (Wave 9).

Simulates BOTH legs behind the exact verified wire schemas (OpenAlgo + our
mt5_service), so paper mode is a base-URL swap in config — the router, exit
manager, state machine and reconciler run their REAL code paths, unchanged.

Realism requirements (this is testing infrastructure for money decisions):
  - fills at last tick price ± slippage from the SQUARE-ROOT impact model (M40)
  - full India cost schedule charged on every fill (M40)
  - SL-M stop orders REST here and trigger on ticks (like the real exchange)
  - margin tracking; rejects when insufficient (same reason codes)
  - positions/orderbook/tradebook queryable for the reconciler
  - deterministic when seeded — paper results must be reproducible
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Optional

from src.core.config_loader import ImpactModel, IndiaCosts
from src.core.transaction_cost_model import impact_fraction, india_trade_cost


@dataclass
class PaperFill:
    orderid: str
    symbol: str
    action: str
    qty: float
    price: float
    cost: float
    ts: float


class PaperBroker:
    def __init__(self, *, costs: IndiaCosts, impact: ImpactModel,
                 starting_cash: float, adv_map: Optional[dict] = None,
                 daily_sigma_map: Optional[dict] = None,
                 mt5_cost_map: Optional[dict] = None) -> None:
        """mt5_cost_map: {symbol: {"half_spread": abs_price, "commission_pct": frac}}
        — MT5-leg symbols pay spread+commission (real CFD schedule), never India STT."""
        self.costs = costs
        self.impact = impact
        self.mt5_cost_map = mt5_cost_map or {}
        self.cash = starting_cash
        self.starting_cash = starting_cash
        self.adv_map = adv_map or {}
        self.sigma_map = daily_sigma_map or {}
        self.last_price: dict[str, float] = {}
        self.positions: dict[str, dict] = {}          # symbol -> {qty, avg_price}
        self.open_orders: dict[str, dict] = {}        # resting (SL-M / SL)
        self.fills: list[PaperFill] = []
        self.total_costs = 0.0
        self._ids = itertools.count(1)

    # ---------- market data ----------

    def on_tick(self, symbol: str, price: float) -> list[PaperFill]:
        """Advance the market; trigger any resting stops that price crossed."""
        if price <= 0:
            raise ValueError("tick price must be positive")
        self.last_price[symbol] = price
        triggered: list[PaperFill] = []
        for oid, order in list(self.open_orders.items()):
            if order["symbol"] != symbol or order["pricetype"] not in ("SL-M", "SL"):
                continue
            trig = order["trigger_price"]
            hit = price <= trig if order["action"] == "SELL" else price >= trig
            if hit:
                del self.open_orders[oid]
                triggered.append(self._fill(oid, symbol, order["action"],
                                            order["quantity"], ref_price=trig,
                                            product=order.get("product", "MIS")))
        return triggered

    # ---------- order lifecycle (OpenAlgo-schema semantics) ----------

    def place_order(self, payload: dict) -> dict:
        symbol = payload["symbol"]
        action = payload["action"].upper()
        qty = float(payload["quantity"])
        pricetype = payload.get("pricetype", "MARKET")
        if qty <= 0:
            return {"status": "error", "message": "invalid quantity"}
        if symbol not in self.last_price and pricetype == "MARKET":
            return {"status": "error", "message": f"no market data for {symbol}"}

        oid = f"PB{next(self._ids):06d}"
        if pricetype in ("SL-M", "SL"):
            self.open_orders[oid] = {**payload, "orderid": oid, "action": action,
                                     "quantity": qty, "rested_at": time.time()}
            return {"status": "success", "orderid": oid, "filled_qty": 0}

        # MARKET: margin check then immediate fill with slippage + costs
        ref = self.last_price[symbol]
        notional = qty * ref
        if action == "BUY" and notional > self.cash:
            return {"status": "error", "message": "insufficient_margin"}
        fill = self._fill(oid, symbol, action, qty, ref_price=ref,
                          product=payload.get("product", "MIS"))
        return {"status": "success", "orderid": oid,
                "filled_qty": qty, "avg_price": fill.price}

    def modify_order(self, orderid: str, trigger_price: float) -> dict:
        order = self.open_orders.get(orderid)
        if order is None:
            return {"status": "error", "message": "unknown orderid"}
        order["trigger_price"] = trigger_price
        return {"status": "success", "orderid": orderid}

    def cancel_order(self, orderid: str) -> dict:
        if self.open_orders.pop(orderid, None) is None:
            return {"status": "error", "message": "unknown orderid"}
        return {"status": "success", "orderid": orderid}

    # ---------- internals ----------

    def _fill(self, oid: str, symbol: str, action: str, qty: float,
              ref_price: float, product: str) -> PaperFill:
        adv = self.adv_map.get(symbol, 1_000_000.0)
        sigma = self.sigma_map.get(symbol, 0.02)
        slip = impact_fraction(self.impact, qty, adv, sigma)
        price = ref_price * (1 + slip) if action == "BUY" else ref_price * (1 - slip)
        if symbol in self.mt5_cost_map:
            mc = self.mt5_cost_map[symbol]
            cost = mc.get("half_spread", 0.0) * qty + mc.get("commission_pct", 0.0) * qty * price
        else:
            cost = india_trade_cost(self.costs, action.lower(), qty, price,
                                    "delivery" if product == "CNC" else "intraday").total
        pos = self.positions.setdefault(symbol, {"qty": 0.0, "avg_price": 0.0})
        signed = qty if action == "BUY" else -qty
        new_qty = pos["qty"] + signed
        if pos["qty"] != 0 and (pos["qty"] > 0) != (new_qty > 0) and new_qty != 0:
            pos["avg_price"] = price                     # flipped through zero
        elif signed > 0 and new_qty > 0:
            prev_notional = pos["avg_price"] * pos["qty"] if pos["qty"] > 0 else 0.0
            pos["avg_price"] = (prev_notional + qty * price) / new_qty
        pos["qty"] = new_qty
        if new_qty == 0:
            pos["avg_price"] = 0.0
        self.cash += -qty * price - cost if action == "BUY" else qty * price - cost
        self.total_costs += cost
        fill = PaperFill(oid, symbol, action, qty, price, cost, time.time())
        self.fills.append(fill)
        return fill

    # ---------- books (reconciler / cockpit reads) ----------

    def positionbook(self) -> list[dict]:
        return [{"symbol": s, "qty": p["qty"], "avg_price": p["avg_price"]}
                for s, p in self.positions.items() if p["qty"] != 0]

    def orderbook(self) -> list[dict]:
        return [dict(o) for o in self.open_orders.values()]

    def tradebook(self) -> list[dict]:
        return [{"client_order_id": f.orderid, "symbol": f.symbol, "qty": f.qty,
                 "price": f.price, "action": f.action, "ts": f.ts} for f in self.fills]

    def equity(self) -> float:
        unrealized = sum(
            p["qty"] * (self.last_price.get(s, p["avg_price"]) - p["avg_price"])
            for s, p in self.positions.items() if p["qty"] != 0
        )
        return self.cash + unrealized + sum(
            abs(p["qty"]) * p["avg_price"] for p in self.positions.values() if p["qty"] > 0
        )

    def available_margin(self) -> float:
        return max(0.0, self.cash)
