"""MODULE 41 tests — chaos cases from spec acceptance:
network-drop-after-send, duplicate ack, partial-then-reject, idempotent retry,
overfill impossible, no order lost or double-counted.
"""
import pytest

from src.core.order_state_machine import (
    IllegalTransition,
    OrderState,
    OrderStateMachine,
    RetryNotAllowed,
)


def make(osm=None):
    osm = osm or OrderStateMachine()
    rec = osm.create("RELIANCE", "buy", 100, "india")
    return osm, rec


def test_happy_path_full_fill():
    osm, rec = make()
    osm.mark_sent(rec)
    osm.on_ack(rec, "B123")
    osm.on_fill(rec, 100, 2500.0)
    assert rec.state is OrderState.FILLED
    assert rec.filled_qty == 100 and rec.avg_fill_price == 2500.0
    assert rec.is_terminal


def test_partial_fills_accumulate_with_avg_price():
    osm, rec = make()
    osm.mark_sent(rec)
    osm.on_ack(rec, "B1")
    osm.on_fill(rec, 40, 2500.0)
    assert rec.state is OrderState.PARTIAL
    osm.on_fill(rec, 60, 2510.0)
    assert rec.state is OrderState.FILLED
    assert rec.avg_fill_price == pytest.approx((40 * 2500 + 60 * 2510) / 100)


def test_partial_then_reject_keeps_booked_fills():
    """Chaos: 40 filled, remainder rejected — the 40 must stay booked."""
    osm, rec = make()
    osm.mark_sent(rec)
    osm.on_ack(rec, "B1")
    osm.on_fill(rec, 40, 2500.0)
    osm.on_reject(rec, "rms: remainder rejected")
    assert rec.state is OrderState.REJECTED
    assert rec.filled_qty == 40
    assert osm.open_exposure() == {"RELIANCE": 40.0}


def test_overfill_impossible():
    osm, rec = make()
    osm.mark_sent(rec)
    osm.on_ack(rec, "B1")
    with pytest.raises(ValueError):
        osm.on_fill(rec, 101, 2500.0)
    osm.on_fill(rec, 100, 2500.0)
    with pytest.raises(ValueError):
        osm.on_fill(rec, 1, 2500.0)  # already FILLED — would overfill
    assert rec.filled_qty == 100  # never double-counted


def test_duplicate_ack_is_noop():
    osm, rec = make()
    osm.mark_sent(rec)
    osm.on_ack(rec, "B1")
    osm.on_ack(rec, "B1")  # duplicate — must not raise or duplicate history state
    assert rec.state is OrderState.ACKED


def test_illegal_transitions_raise():
    osm, rec = make()
    with pytest.raises(IllegalTransition):
        osm.on_fill(rec, 10, 100.0)  # CREATED → PARTIAL is illegal (must be SENT first)
    osm.mark_sent(rec)
    osm.on_reject(rec, "bad price band")
    with pytest.raises(IllegalTransition):
        osm.on_cancel(rec)  # REJECTED is terminal


async def test_timeout_then_reconcile_adopts_broker_fill():
    """Chaos: network drop after send; broker actually filled it."""
    osm, rec = make()
    osm.mark_sent(rec)
    osm.on_timeout(rec)
    assert rec.state is OrderState.UNKNOWN

    async def broker_lookup(coid):
        return {"broker_order_id": "B9", "status": "filled", "filled_qty": 100, "avg_price": 2501.0}

    state = await osm.reconcile_unknown(rec, broker_lookup)
    assert state is OrderState.FILLED
    assert rec.filled_qty == 100 and rec.avg_fill_price == 2501.0
    assert rec.broker_order_id == "B9"


async def test_timeout_reconcile_confirmed_absent_allows_retry_as_new_id():
    osm, rec = make()
    osm.mark_sent(rec)
    osm.on_timeout(rec)

    async def broker_lookup(coid):
        return None  # confirmed: never reached the broker

    state = await osm.reconcile_unknown(rec, broker_lookup)
    assert state is OrderState.FAILED_NOT_PLACED

    fresh = osm.retry_as_new(rec)
    assert fresh.client_order_id != rec.client_order_id  # idempotency: NEW attempt
    assert fresh.state is OrderState.CREATED


async def test_retry_from_unknown_is_forbidden():
    """The classic double-order bug — must be structurally impossible."""
    osm, rec = make()
    osm.mark_sent(rec)
    osm.on_timeout(rec)
    with pytest.raises(RetryNotAllowed):
        osm.retry_as_new(rec)


async def test_reconcile_lookup_failure_stays_unknown_fail_closed():
    osm, rec = make()
    osm.mark_sent(rec)
    osm.on_timeout(rec)

    async def broker_lookup(coid):
        raise ConnectionError("broker api down")

    state = await osm.reconcile_unknown(rec, broker_lookup)
    assert state is OrderState.UNKNOWN  # R4: stay unknown, no retry possible
    with pytest.raises(RetryNotAllowed):
        osm.retry_as_new(rec)


def test_open_exposure_nets_buys_and_sells():
    osm = OrderStateMachine()
    b = osm.create("TCS", "buy", 50, "india")
    s = osm.create("TCS", "sell", 20, "india")
    for r in (b, s):
        osm.mark_sent(r)
        osm.on_ack(r, f"B{r.client_order_id[:4]}")
    osm.on_fill(b, 50, 4000.0)
    osm.on_fill(s, 20, 4010.0)
    assert osm.open_exposure() == {"TCS": 30.0}
