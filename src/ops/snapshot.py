"""Cockpit snapshot builder — the EXACT state shape the cockpit SPA eats.

Contract-tested against cockpit/web/state_contract.json (field-name canary,
two-sided since 2026-08-11: the snapshot must emit every contract field and
app.js must consume everything not marked ui_optional), so the Python
gateway and the UI cannot drift apart silently. The contract originally
lived in the retired cockpit-next demo app's TypeScript types.
"""
from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path


class SnapshotBuilder:
    def __init__(self, *, mode: str, price_history_len: int = 120) -> None:
        self.mode = mode
        self.candles: dict[str, deque] = {}
        self.equity_curve: deque = deque(maxlen=price_history_len)
        self.events: deque = deque(maxlen=50)
        self._len = price_history_len

    # ---- feeds push into the builder ----

    def push_candle(self, symbol: str, ts: int, o: float, h: float, l: float, c: float) -> None:
        dq = self.candles.setdefault(symbol, deque(maxlen=self._len))
        dq.append({"time": ts, "open": o, "high": h, "low": l, "close": c})

    def push_equity(self, ts: int, value: float) -> None:
        self.equity_curve.append({"time": ts, "value": value})

    def push_event(self, message: str, level: str = "info") -> None:
        self.events.appendleft({"t": time.strftime("%H:%M"), "m": message, "level": level})

    # ---- the gateway snapshot_fn ----

    def build(self, *, halted: bool, role: str, equity: float, pnl: float, costs: float,
              var95: float, var_limit: float, positions_fn, workers: dict,
              approvals: list, gex: dict, gate_path: str | Path) -> dict:
        gate_raw = {}
        p = Path(gate_path)
        if p.exists():
            gate_raw = json.loads(p.read_text())
        positions = []
        for pos in positions_fn():
            r_val = pos.r_value or 1.0
            positions.append({
                "symbol": pos.symbol, "leg": pos.leg, "qty": pos.remaining_qty,
                "entry": pos.entry, "stop": pos.stop,
                "r_now": ((pos.extreme - pos.entry) / r_val) if pos.is_long
                         else ((pos.entry - pos.extreme) / r_val),
                "state": pos.state,
                "mfe_r": ((pos.extreme - pos.entry) / r_val) if pos.is_long
                         else ((pos.entry - pos.extreme) / r_val),
                "unrealized": 0.0,
            })
        return {
            "mode": self.mode, "halted": halted, "role": role,
            "equity": equity, "pnl": pnl, "costs": costs,
            "var95": var95, "varLimit": var_limit,
            "positions": positions,
            "equityCurve": list(self.equity_curve),
            "candles": {s: list(dq) for s, dq in self.candles.items()},
            "workers": workers, "approvals": approvals, "events": list(self.events),
            "gex": gex,
            "gate": {
                "paper_days_completed": int(gate_raw.get("paper_days_completed", 0)),
                "clean_reconciliation_streak": int(gate_raw.get("clean_reconciliation_streak", 0)),
                "sebi_checks_passed": bool(gate_raw.get("sebi_checks_passed", False)),
                "static_ip": False,     # overwritten by runtime from config
                "human_ack": gate_raw.get("human_ack", "") != "",
            },
        }
