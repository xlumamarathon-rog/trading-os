"""Paper Trading Server — serves the PaperBroker behind BOTH verified wire
schemas (Wave 9). In paper mode, connection_manager points here and every real
code path (router, exit adapters, reconciler) runs unchanged.

Endpoints:
  OpenAlgo-compatible:  /api/v1/placeorder /modifyorder /cancelorder
                        /positionbook /orderbook /tradebook   (verified schema)
  mt5_service-compatible: /order /order/{coid} /position/stop|modify|close
  Paper control:        /paper/tick /paper/state /health
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.core.paper_broker import PaperBroker


class Tick(BaseModel):
    symbol: str
    price: float


def create_paper_server(broker: PaperBroker) -> FastAPI:
    app = FastAPI(title="Trading OS Paper Broker", docs_url=None, redoc_url=None)
    mt5_orders: dict[str, dict] = {}

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": "paper"}

    # ---------------- OpenAlgo-compatible leg ----------------

    @app.post("/api/v1/placeorder")
    async def placeorder(body: dict):
        result = broker.place_order(body)
        if result.get("status") != "success":
            raise HTTPException(status_code=400, detail=result.get("message"))
        return result

    @app.post("/api/v1/modifyorder")
    async def modifyorder(body: dict):
        result = broker.modify_order(body["orderid"], float(body["trigger_price"]))
        if result.get("status") != "success":
            raise HTTPException(status_code=400, detail=result.get("message"))
        return result

    @app.post("/api/v1/cancelorder")
    async def cancelorder(body: dict):
        result = broker.cancel_order(body["orderid"])
        if result.get("status") != "success":
            raise HTTPException(status_code=400, detail=result.get("message"))
        return result

    @app.get("/api/v1/positionbook")
    async def positionbook():
        return broker.positionbook()

    @app.get("/api/v1/orderbook")
    async def orderbook():
        return broker.orderbook()

    @app.get("/api/v1/tradebook")
    async def tradebook():
        return broker.tradebook()

    # ---------------- mt5_service-compatible leg ----------------

    @app.post("/order")
    async def mt5_order(body: dict):
        payload = {"symbol": body["symbol"], "action": body["direction"].upper(),
                   "quantity": body["qty"], "pricetype": "MARKET", "product": "MIS"}
        result = broker.place_order(payload)
        if result.get("status") != "success":
            raise HTTPException(status_code=503, detail=result.get("message"))
        mt5_orders[body["client_order_id"]] = result
        return {"broker_order_id": result["orderid"],
                "filled_qty": result["filled_qty"], "avg_price": result.get("avg_price")}

    @app.get("/order/{client_order_id}")
    async def mt5_lookup(client_order_id: str):
        found = mt5_orders.get(client_order_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown order")
        return {"broker_order_id": found["orderid"], "status": "filled",
                "filled_qty": found["filled_qty"], "avg_price": found.get("avg_price")}

    def _closing_action(symbol: str) -> str:
        """Protective stops and closes act AGAINST the open position:
        long → SELL, short → BUY. Falls back to SELL when flat (legacy)."""
        pos = broker.positions.get(symbol)
        if pos and pos["qty"] < 0:
            return "BUY"
        return "SELL"

    @app.post("/position/stop")
    async def mt5_stop(body: dict):
        result = broker.place_order({"symbol": body["symbol"],
                                     "action": _closing_action(body["symbol"]),
                                     "quantity": body["lots"], "pricetype": "SL-M",
                                     "trigger_price": body["sl"], "product": "MIS"})
        return {"position_id": result["orderid"]}

    @app.post("/position/modify")
    async def mt5_modify(body: dict):
        sl = float(body["sl"])
        if sl <= 0:
            # MT5 semantics: sl=0 clears the stop. A resting BUY stop with
            # trigger 0 would insta-fire, so clearing must cancel the order.
            result = broker.cancel_order(body["position_id"])
        else:
            result = broker.modify_order(body["position_id"], sl)
            # MT5 semantics: the SL rides the POSITION. After a partial close
            # the protective stop covers exactly what REMAINS — never the
            # original quantity. Without this sync the emulated stop oversold
            # on trigger, flipping the book into a phantom short.
            order = broker.open_orders.get(body["position_id"])
            if order is not None:
                pos_qty = abs(broker.positions.get(order["symbol"], {}).get("qty", 0.0))
                if pos_qty > 0:
                    order["quantity"] = min(order["quantity"], pos_qty)
        if result.get("status") != "success":
            raise HTTPException(status_code=400, detail=result.get("message"))
        return {"ok": True}

    @app.post("/position/close")
    async def mt5_close(body: dict):
        result = broker.place_order({"symbol": body["symbol"],
                                     "action": _closing_action(body["symbol"]),
                                     "quantity": body["lots"], "pricetype": "MARKET",
                                     "product": "MIS"})
        return {"ok": result.get("status") == "success"}

    # ---------------- paper control ----------------

    @app.post("/paper/tick")
    async def tick(body: Tick):
        triggered = broker.on_tick(body.symbol, body.price)
        return {"triggered": [t.orderid for t in triggered]}

    @app.get("/paper/state")
    async def state():
        return {"cash": broker.cash, "equity": broker.equity(),
                "total_costs": broker.total_costs,
                "positions": broker.positionbook(), "resting": broker.orderbook()}

    return app
