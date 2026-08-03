"""M35 adapter — India resting SL-M orders via OpenAlgo (schema verified per R1).

Payloads built by src/core/broker_payloads.py against vendor/openalgo source:
strategy carries the SEBI Algo ID; response field is "orderid".
"""
from __future__ import annotations

from src.core.broker_payloads import openalgo_modify_payload, openalgo_order_payload


class IndiaStopAdapter:
    def __init__(self, openalgo_client, apikey: str, algo_id: str,
                 exchange: str = "NSE") -> None:
        self.client = openalgo_client
        self.apikey = apikey
        self.algo_id = algo_id
        self.exchange = exchange

    async def place_stop(self, symbol: str, qty: float, stop_price: float, leg: str) -> str:
        resp = await self.client.post("/api/v1/placeorder", json=openalgo_order_payload(
            apikey=self.apikey, algo_id=self.algo_id, exchange=self.exchange,
            symbol=symbol, action="SELL", quantity=qty,
            pricetype="SL-M", trigger_price=stop_price))
        resp.raise_for_status()
        return str(resp.json().get("orderid"))

    async def modify_stop(self, stop_order_id: str, new_price: float, leg: str) -> None:
        resp = await self.client.post("/api/v1/modifyorder", json=openalgo_modify_payload(
            apikey=self.apikey, orderid=stop_order_id, trigger_price=new_price))
        resp.raise_for_status()

    async def exit_market(self, symbol: str, qty: float, leg: str) -> None:
        resp = await self.client.post("/api/v1/placeorder", json=openalgo_order_payload(
            apikey=self.apikey, algo_id=self.algo_id, exchange=self.exchange,
            symbol=symbol, action="SELL", quantity=qty, pricetype="MARKET"))
        resp.raise_for_status()
