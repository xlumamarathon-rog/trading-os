"""Verified-schema payload builder tests (R1 — written against vendor/openalgo source)."""
import pytest

from src.core.broker_payloads import openalgo_modify_payload, openalgo_order_payload


def test_openalgo_payload_matches_verified_schema():
    p = openalgo_order_payload(apikey="K", algo_id="ALGO-1", exchange="NSE",
                               symbol="RELIANCE", action="buy", quantity=100,
                               product="MIS", pricetype="SL-M", trigger_price=2450.0)
    assert set(p) == {"apikey", "strategy", "exchange", "symbol", "action", "quantity",
                      "product", "pricetype", "price", "trigger_price"}
    assert p["action"] == "BUY" and p["strategy"] == "ALGO-1" and p["quantity"] == 100


def test_fractional_qty_only_on_crypto_exchanges():
    with pytest.raises(ValueError):
        openalgo_order_payload(apikey="K", algo_id="A", exchange="NSE",
                               symbol="X", action="BUY", quantity=1.5)
    ok = openalgo_order_payload(apikey="K", algo_id="A", exchange="CRYPTO",
                                symbol="BTC", action="BUY", quantity=0.5)
    assert ok["quantity"] == 0.5


def test_invalid_action_rejected():
    with pytest.raises(ValueError):
        openalgo_order_payload(apikey="K", algo_id="A", exchange="NSE",
                               symbol="X", action="short", quantity=1)


def test_modify_payload_partial_fields():
    m = openalgo_modify_payload(apikey="K", orderid="O1", trigger_price=101.0)
    assert m == {"apikey": "K", "orderid": "O1", "trigger_price": 101.0}
