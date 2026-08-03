"""MODULE 16 — EOD reconciliation: internal audit vs broker truth (v2 additions)."""
from __future__ import annotations

from dataclasses import dataclass, field

PRICE_TOL = 0.005  # 0.5% slippage tolerance before flagging price mismatch


@dataclass
class ReconciliationReport:
    date: str
    missing_at_broker: list = field(default_factory=list)
    missing_internally: list = field(default_factory=list)
    qty_mismatches: list = field(default_factory=list)
    price_mismatches: list = field(default_factory=list)
    naked_positions: list = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.missing_at_broker or self.missing_internally
                    or self.qty_mismatches or self.price_mismatches or self.naked_positions)


def reconcile(date: str, internal: list, broker: list, naked_positions: list) -> ReconciliationReport:
    """internal/broker rows: {client_order_id, symbol, qty, price}."""
    rep = ReconciliationReport(date=date, naked_positions=list(naked_positions))
    bmap = {r["client_order_id"]: r for r in broker}
    imap = {r["client_order_id"]: r for r in internal}
    for oid, row in imap.items():
        b = bmap.get(oid)
        if b is None:
            rep.missing_at_broker.append(oid)
            continue
        if abs(b["qty"] - row["qty"]) > 1e-9:
            rep.qty_mismatches.append((oid, row["qty"], b["qty"]))
        if row["price"] > 0 and abs(b["price"] - row["price"]) / row["price"] > PRICE_TOL:
            rep.price_mismatches.append((oid, row["price"], b["price"]))
    for oid in bmap:
        if oid not in imap:
            rep.missing_internally.append(oid)
    return rep
