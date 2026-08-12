"""MT5 Execution Microservice — runs on the Windows VPS next to the broker.

The Linux core talks to THIS over private HTTPS; only this process touches
aiomql/MetaTrader5 (Windows-only pip package). Endpoints mirror exactly what
order_router (M4) and the MT5 stop adapter (M35) call. The mt5 interface is
injected so the service is fully testable off-Windows; on the VPS it's the
real aiomql wrapper (vendor source read per R1 before wiring).
"""
from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
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


def create_mt5_service(mt5, *, auth_token: Optional[str] = None) -> FastAPI:
    """mt5 interface: place_order, lookup_order, set_stop, modify_stop, close.

    auth_token: shared secret required on every order/position endpoint via the
    `X-MT5-Auth` header. This service can PLACE AND CLOSE REAL BROKER ORDERS, so
    network isolation alone is not enough — a shared secret means a stray
    process that reaches the port still cannot move money. Falls back to
    MT5_SERVICE_TOKEN in the environment. When neither is set the guard is a
    no-op (dev/off-Windows tests), but production deploy MUST set it — DEPLOY.md
    §2. /health is always open for liveness probes.
    """
    token = auth_token or os.environ.get("MT5_SERVICE_TOKEN") or ""
    app = FastAPI(title="MT5 Exec Service", docs_url=None, redoc_url=None)

    def require_auth(x_mt5_auth: str = Header(default="")) -> None:
        if not token:
            return  # unconfigured: preserve legacy behavior for tests/dev
        if not secrets.compare_digest(x_mt5_auth, token):  # constant-time
            raise HTTPException(status_code=401, detail="mt5 service auth failed")

    @app.get("/health")
    async def health():
        return {"status": "ok", "terminal_connected": await mt5.is_connected()}

    @app.post("/order")
    async def order(body: OrderIn, _: None = Depends(require_auth)):
        if not await mt5.is_connected():
            raise HTTPException(status_code=503, detail="mt5 terminal disconnected")
        result = await mt5.place_order(body.model_dump())
        return result   # {broker_order_id, filled_qty, avg_price}

    @app.get("/order/{client_order_id}")
    async def order_lookup(client_order_id: str, _: None = Depends(require_auth)):
        found = await mt5.lookup_order(client_order_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown order")
        return found

    @app.post("/position/stop")
    async def position_stop(body: StopIn, _: None = Depends(require_auth)):
        pid = await mt5.set_stop(body.symbol, body.lots, body.sl)
        return {"position_id": pid}

    @app.post("/position/modify")
    async def position_modify(body: StopIn, _: None = Depends(require_auth)):
        await mt5.modify_stop(body.position_id, body.sl)
        return {"ok": True}

    @app.post("/position/close")
    async def position_close(body: StopIn, _: None = Depends(require_auth)):
        await mt5.close(body.symbol, body.lots)
        return {"ok": True}

    # ---- market data (Aug 2026): the terminal's own bid/ask + candles.
    # Read-only but still auth-gated — prices are harmless, but a private
    # exec service keeps ONE posture, and account-currency ticks can leak
    # broker/account details. 503 when the terminal is down (fail-loud).

    @app.get("/tick/{symbol}")
    async def tick(symbol: str, _: None = Depends(require_auth)):
        if not await mt5.is_connected():
            raise HTTPException(status_code=503, detail="mt5 terminal disconnected")
        t = await mt5.tick(symbol)
        if t is None:
            raise HTTPException(status_code=404, detail=f"no tick for {symbol!r}")
        return t

    @app.get("/candles/{symbol}")
    async def candles(symbol: str, timeframe: str = "M5", count: int = 100,
                      _: None = Depends(require_auth)):
        if not await mt5.is_connected():
            raise HTTPException(status_code=503, detail="mt5 terminal disconnected")
        try:
            rows = await mt5.candles(symbol, timeframe,
                                     max(2, min(int(count), 1000)))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return rows

    return app
