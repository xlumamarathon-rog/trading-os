"""MODULE 8 — Pre-trade gate + hash-chained append-only audit log (spec §Phase 2).

Replaces XQRiskCore (v2). Every decision row is chained: hash_n = sha256(hash_{n-1}
+ canonical_json(row)) — any later mutation breaks verify_chain(). Persistence
backend is injectable (in-memory list now; Postgres table with no UPDATE grant
on the VPS).
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Optional

GENESIS = "0" * 64


class AuditLog:
    def __init__(self) -> None:
        self._rows: list[dict] = []

    def append(self, row: dict) -> dict:
        prev = self._rows[-1]["hash"] if self._rows else GENESIS
        body = dict(row)
        body["ts"] = body.get("ts", time.time())
        body["prev_hash"] = prev
        payload = json.dumps({k: v for k, v in body.items() if k != "hash"}, sort_keys=True, default=str)
        body["hash"] = hashlib.sha256((prev + payload).encode()).hexdigest()
        self._rows.append(body)
        return body

    def verify_chain(self) -> bool:
        prev = GENESIS
        for row in self._rows:
            payload = json.dumps({k: v for k, v in row.items() if k != "hash"}, sort_keys=True, default=str)
            if row.get("prev_hash") != prev:
                return False
            if hashlib.sha256((prev + payload).encode()).hexdigest() != row.get("hash"):
                return False
            prev = row["hash"]
        return True

    @property
    def rows(self) -> list[dict]:
        return list(self._rows)


@dataclass
class GateDecision:
    approved: bool
    reason: str


class PreTradeGate:
    def __init__(self, risk_limits, audit: AuditLog) -> None:
        self.risk = risk_limits
        self.audit = audit

    def check(self, order: dict, var_95: float,
              sector_exposure_pct: Optional[float] = None) -> GateDecision:
        reason = "approved"
        approved = True
        if var_95 >= self.risk.max_var_daily:
            approved, reason = False, "var_at_limit"
        elif sector_exposure_pct is not None and sector_exposure_pct >= self.risk.max_sector_exposure_pct:
            approved, reason = False, "sector_exposure_cap"
        self.audit.append({"type": "pre_trade_gate", "order": order,
                           "var_95": var_95, "approved": approved, "reason": reason})
        return GateDecision(approved, reason)
