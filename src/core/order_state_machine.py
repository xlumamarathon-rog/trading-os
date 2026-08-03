"""MODULE 41 — Order State Machine (spec §Phase 1, NEW in v2).

Every order lives in an explicit state machine:
    CREATED → SENT → ACKED → PARTIAL → FILLED
                   ↘ REJECTED / CANCELLED
        (timeout) → UNKNOWN → (reconcile) → adopt broker truth | FAILED_NOT_PLACED

Hard cases owned here:
- timeout-after-send: state UNKNOWN — NO retry until reconciled against the broker.
- partial fills: filled_qty tracked monotonically; consumers see actual quantity.
- idempotency: client_order_id is unique per attempt; a retry is a NEW attempt and
  is only legal from FAILED_NOT_PLACED (proven not at broker), never from UNKNOWN.
- duplicate acks/fills are tolerated (no-op / validated), never double-counted.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OrderState(str, Enum):
    CREATED = "CREATED"
    SENT = "SENT"
    ACKED = "ACKED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"
    FAILED_NOT_PLACED = "FAILED_NOT_PLACED"


TERMINAL_STATES = {
    OrderState.FILLED,
    OrderState.REJECTED,
    OrderState.CANCELLED,
    OrderState.FAILED_NOT_PLACED,
}

_LEGAL: dict[OrderState, set[OrderState]] = {
    OrderState.CREATED: {OrderState.SENT, OrderState.FAILED_NOT_PLACED},
    OrderState.SENT: {
        OrderState.ACKED,
        OrderState.REJECTED,
        OrderState.UNKNOWN,
        OrderState.FILLED,   # some brokers ack+fill atomically
        OrderState.PARTIAL,
    },
    OrderState.ACKED: {
        OrderState.PARTIAL,
        OrderState.FILLED,
        OrderState.REJECTED,
        OrderState.CANCELLED,
        OrderState.UNKNOWN,
    },
    OrderState.PARTIAL: {
        OrderState.PARTIAL,
        OrderState.FILLED,
        OrderState.CANCELLED,  # remainder cancelled — fills stay booked
        OrderState.REJECTED,   # remainder rejected — fills stay booked
        OrderState.UNKNOWN,
    },
    OrderState.UNKNOWN: {
        OrderState.ACKED,
        OrderState.PARTIAL,
        OrderState.FILLED,
        OrderState.REJECTED,
        OrderState.CANCELLED,
        OrderState.FAILED_NOT_PLACED,
    },
    OrderState.FILLED: set(),
    OrderState.REJECTED: set(),
    OrderState.CANCELLED: set(),
    OrderState.FAILED_NOT_PLACED: set(),
}


class IllegalTransition(RuntimeError):
    pass


class RetryNotAllowed(RuntimeError):
    pass


@dataclass
class OrderRecord:
    client_order_id: str
    symbol: str
    direction: str            # "buy" | "sell"
    requested_qty: float
    leg: str                  # "india" | "mt5_forex" | "mt5_crypto"
    state: OrderState = OrderState.CREATED
    broker_order_id: Optional[str] = None
    filled_qty: float = 0.0
    avg_fill_price: Optional[float] = None
    reject_reason: Optional[str] = None
    history: list = field(default_factory=list)

    def _transition(self, new: OrderState, note: str = "") -> None:
        if new is not self.state and new not in _LEGAL[self.state]:
            raise IllegalTransition(f"{self.state.value} → {new.value} ({note})")
        self.history.append((time.time(), self.state.value, new.value, note))
        self.state = new

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


class OrderStateMachine:
    """Owns every OrderRecord; the router never mutates records directly."""

    def __init__(self) -> None:
        self.records: dict[str, OrderRecord] = {}

    # ---------- lifecycle ----------

    def create(self, symbol: str, direction: str, qty: float, leg: str) -> OrderRecord:
        rec = OrderRecord(
            client_order_id=uuid.uuid4().hex,
            symbol=symbol,
            direction=direction,
            requested_qty=qty,
            leg=leg,
        )
        self.records[rec.client_order_id] = rec
        return rec

    def mark_sent(self, rec: OrderRecord) -> None:
        rec._transition(OrderState.SENT, "dispatched to broker")

    def on_ack(self, rec: OrderRecord, broker_order_id: str) -> None:
        if rec.state is OrderState.ACKED and rec.broker_order_id == broker_order_id:
            return  # duplicate ack — tolerated no-op
        rec.broker_order_id = broker_order_id
        rec._transition(OrderState.ACKED, f"broker ack {broker_order_id}")

    def on_fill(self, rec: OrderRecord, qty: float, price: float) -> None:
        if qty <= 0:
            raise ValueError("fill qty must be positive")
        new_filled = rec.filled_qty + qty
        if new_filled > rec.requested_qty + 1e-9:
            raise ValueError(
                f"overfill: {new_filled} > requested {rec.requested_qty}"
            )
        prev_notional = (rec.avg_fill_price or 0.0) * rec.filled_qty
        rec.filled_qty = new_filled
        rec.avg_fill_price = (prev_notional + qty * price) / rec.filled_qty
        if abs(rec.filled_qty - rec.requested_qty) <= 1e-9:
            rec._transition(OrderState.FILLED, f"fill {qty}@{price}")
        else:
            rec._transition(OrderState.PARTIAL, f"partial {qty}@{price}")

    def on_reject(self, rec: OrderRecord, reason: str) -> None:
        rec.reject_reason = reason
        rec._transition(OrderState.REJECTED, reason)

    def on_cancel(self, rec: OrderRecord, note: str = "") -> None:
        rec._transition(OrderState.CANCELLED, note or "cancelled")

    def on_timeout(self, rec: OrderRecord) -> None:
        """Network died after send — the truth lives at the broker now."""
        rec._transition(OrderState.UNKNOWN, "timeout after send — reconcile required")

    # ---------- reconciliation & retry ----------

    async def reconcile_unknown(self, rec: OrderRecord, broker_lookup) -> OrderState:
        """broker_lookup(client_order_id) -> dict|None with broker truth.

        Adopts the broker's state. Only a confirmed absence at the broker
        (lookup returns None) yields FAILED_NOT_PLACED — the only retryable state.
        A lookup FAILURE keeps the order UNKNOWN (fail-closed, R4).
        """
        if rec.state is not OrderState.UNKNOWN:
            raise IllegalTransition(f"reconcile only from UNKNOWN, not {rec.state.value}")
        try:
            truth = await broker_lookup(rec.client_order_id)
        except Exception as exc:  # noqa: BLE001 — stay UNKNOWN, surface the reason
            rec.history.append((time.time(), "UNKNOWN", "UNKNOWN", f"reconcile_failed:{exc}"))
            return rec.state

        if truth is None:
            rec._transition(OrderState.FAILED_NOT_PLACED, "confirmed absent at broker")
            return rec.state

        rec.broker_order_id = truth.get("broker_order_id", rec.broker_order_id)
        status = truth.get("status")
        filled = float(truth.get("filled_qty", 0.0))
        price = truth.get("avg_price")
        if filled > rec.filled_qty and price is not None:
            self_fill = filled - rec.filled_qty
            # route through on_fill for monotonic accounting — via ACKED if needed
            if rec.state is OrderState.UNKNOWN:
                rec._transition(OrderState.ACKED, "adopted from broker during reconcile")
            self.on_fill(rec, self_fill, float(price))
        elif status == "acked":
            rec._transition(OrderState.ACKED, "adopted: acked at broker")
        elif status == "rejected":
            self.on_reject(rec, truth.get("reason", "rejected at broker"))
        elif status == "cancelled":
            rec._transition(OrderState.CANCELLED, "adopted: cancelled at broker")
        return rec.state

    def retry_as_new(self, rec: OrderRecord) -> OrderRecord:
        """Idempotency rule: retry ONLY from FAILED_NOT_PLACED, as a NEW attempt."""
        if rec.state is not OrderState.FAILED_NOT_PLACED:
            raise RetryNotAllowed(
                f"retry only from FAILED_NOT_PLACED, not {rec.state.value}"
            )
        fresh = self.create(rec.symbol, rec.direction, rec.requested_qty, rec.leg)
        fresh.history.append((time.time(), "CREATED", "CREATED", f"retry_of:{rec.client_order_id}"))
        return fresh

    # ---------- accounting invariant ----------

    def open_exposure(self) -> dict[str, float]:
        """Net filled quantity per symbol — the position truth used by reconciler."""
        out: dict[str, float] = {}
        for rec in self.records.values():
            if rec.filled_qty > 0:
                sign = 1.0 if rec.direction == "buy" else -1.0
                out[rec.symbol] = out.get(rec.symbol, 0.0) + sign * rec.filled_qty
        return out
