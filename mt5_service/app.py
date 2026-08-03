"""MT5 Execution Microservice — runs on the Windows VPS next to the broker.

The Linux core talks to THIS over private HTTPS; only this process touches
aiomql/MetaTrader5 (Windows-only pip package). Endpoints mirror exactly what
order_router (M4) and the MT5 stop adapter (M35) call. The mt5 interface is
injected so the service is fully testable off-Windows; on the VPS it's the
real aiomql wrapper (vendor source read per R1 before wiring).
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class OrderIn(BaseModel):
    client_order_id: str
    symbol: str
    direction: str
    qty: float
    algo_id: Optional[str] = None
    product: Optional[str] = None


class StopIn(BaseModel):
    symbol: Optional[str] = None
    lots: Optional[float] = None
    sl: Optional[float] = None
    position_id: Optional[str] = None


def create_mt5_service(mt5) -> FastAPI:
    """mt5 interface: place_order, lookup_order, set_stop, modify_stop, close."""
    app = FastAPI(title="MT5 Exec Service", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health():
        return {"status": "ok", "terminal_connected": await mt5.is_connected()}

    @app.post("/order")
    async def order(body: OrderIn):
        if not await mt5.is_connected():
            raise HTTPException(status_code=503, detail="mt5 terminal disconnected")
        result = await mt5.place_order(body.model_dump())
        return result   # {broker_order_id, filled_qty, avg_price}

    @app.get("/order/{client_order_id}")
    async def order_lookup(client_order_id: str):
        found = await mt5.lookup_order(client_order_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown order")
        return found

    @app.post("/position/stop")
    async def position_stop(body: StopIn):
        pid = await mt5.set_stop(body.symbol, body.lots, body.sl)
        return {"position_id": pid}

    @app.post("/position/modify")
    async def position_modify(body: StopIn):
        await mt5.modify_stop(body.position_id, body.sl)
        return {"ok": True}

    @app.post("/position/close")
    async def position_close(body: StopIn):
        await mt5.close(body.symbol, body.lots)
        return {"ok": True}

    return app
