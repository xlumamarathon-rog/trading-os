"""Verified broker payload builders — written against ACTUAL vendor source (R1).

OpenAlgo (vendor/openalgo @ 2026-08, restx_api/schemas.py):
  required: apikey, strategy, exchange (VALID_EXCHANGES), symbol,
            action (BUY/SELL), quantity (fractional ONLY on crypto exchanges)
  optional: pricetype (MARKET/LIMIT/SL/SL-M), product (CNC/MIS/NRML),
            price, trigger_price
  response: {"orderid": "...", "status": "success"}
  prefix:   /api/v1  (restx_api/__init__.py Blueprint url_prefix)

The `strategy` field carries our SEBI Algo ID (exchange-registered tag).
"""
from __future__ import annotations

NON_CRYPTO_EXCHANGES = {"NSE", "BSE", "NFO", "BFO", "MCX", "CDS"}


def openalgo_order_payload(*, apikey: str, algo_id: str, exchange: str, symbol: str,
                           action: str, quantity: float, product: str = "MIS",
                           pricetype: str = "MARKET", price: float = 0.0,
                           trigger_price: float = 0.0) -> dict:
    action = action.upper()
    if action not in ("BUY", "SELL"):
        raise ValueError("action must be BUY or SELL")
    if exchange in NON_CRYPTO_EXCHANGES and float(quantity) != int(quantity):
        raise ValueError(f"fractional quantity {quantity} not allowed on {exchange}")
    return {
        "apikey": apikey,
        "strategy": algo_id,          # SEBI Feb-2025 Algo ID tag
        "exchange": exchange,
        "symbol": symbol,
        "action": action,
        "quantity": int(quantity) if exchange in NON_CRYPTO_EXCHANGES else quantity,
        "product": product,
        "pricetype": pricetype,
        "price": price,
        "trigger_price": trigger_price,
    }


def openalgo_modify_payload(*, apikey: str, orderid: str, trigger_price: float = None,
                            price: float = None, quantity: float = None) -> dict:
    body = {"apikey": apikey, "orderid": orderid}
    if trigger_price is not None:
        body["trigger_price"] = trigger_price
    if price is not None:
        body["price"] = price
    if quantity is not None:
        body["quantity"] = quantity
    return body
