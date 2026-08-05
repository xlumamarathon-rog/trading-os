"""MODULE 52 — Transaction-Cost-Analysis monitor (Aug 2026).

Costs decided real verdicts in replay (+1.5R gross → 0.0% net on forex).
Live, a drift between MODELED cost and ACTUAL fill cost would be invisible —
this module records both per fill and raises a drift flag when the realized
cost persistently exceeds the model.

Fail-safe by design: a monitor, never a gate — it can page a human, it can
never block or place an order."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class TcaRecord:
    symbol: str
    expected_cost: float            # model estimate (currency)
    actual_cost: float              # realized (currency)
    notional: float

    @property
    def drift_bps(self) -> float:
        if self.notional <= 0:
            return 0.0
        return (self.actual_cost - self.expected_cost) / self.notional * 10_000


class TcaMonitor:
    def __init__(self, *, window: int = 50, drift_alert_bps: float = 2.0,
                 min_samples: int = 10, alert_fn=None) -> None:
        self.records: deque = deque(maxlen=window)
        self.drift_alert_bps = drift_alert_bps
        self.min_samples = min_samples
        self.alert_fn = alert_fn
        self.alerts: list = []

    def record(self, *, symbol: str, expected_cost: float, actual_cost: float,
               notional: float) -> TcaRecord:
        rec = TcaRecord(symbol, expected_cost, actual_cost, notional)
        self.records.append(rec)
        drift = self.rolling_drift_bps()
        if drift is not None and drift > self.drift_alert_bps:
            alert = {"type": "tca_drift", "rolling_drift_bps": round(drift, 3),
                     "samples": len(self.records)}
            self.alerts.append(alert)
            if self.alert_fn:
                try:
                    self.alert_fn(alert)
                except Exception:   # noqa: BLE001 — monitor must never break trading
                    pass
        return rec

    def rolling_drift_bps(self):
        if len(self.records) < self.min_samples:
            return None
        return sum(r.drift_bps for r in self.records) / len(self.records)
