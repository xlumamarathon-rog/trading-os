"""MODULE 38a — Prediction Ledger: the system's honest diary (spec Addendum D).

Append-only. Every prediction is written AT DECISION TIME with its frozen
feature snapshot and model version; outcomes are settled later. Hindsight bias
is structurally impossible: settled rows cannot be edited, features cannot be
recomputed. Brier-score calibration tracking + drift auto-demote signal.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


class LedgerImmutabilityError(RuntimeError):
    pass


@dataclass
class LedgerEntry:
    entry_id: str
    ts: float
    model_version: str
    kind: str                      # news_reaction | signal | exit_decision
    prediction: dict               # e.g. {"direction": "up", "p": 0.64, "horizon": "1d"}
    features_frozen: dict
    action_taken: str              # acted | abstained | overridden_tier0 | overridden_tier1
    outcome: Optional[dict] = None
    settled_at: Optional[float] = None


class PredictionLedger:
    def __init__(self, drift_window: int = 50, brier_demote_threshold: float = 0.30) -> None:
        self._rows: dict[str, LedgerEntry] = {}
        self.drift_window = drift_window
        self.brier_demote_threshold = brier_demote_threshold

    def record(self, *, model_version: str, kind: str, prediction: dict,
               features: dict, action_taken: str) -> str:
        entry_id = uuid.uuid4().hex
        self._rows[entry_id] = LedgerEntry(
            entry_id=entry_id, ts=time.time(), model_version=model_version, kind=kind,
            prediction=dict(prediction), features_frozen=dict(features),
            action_taken=action_taken)
        return entry_id

    def settle(self, entry_id: str, outcome: dict) -> None:
        row = self._rows[entry_id]
        if row.settled_at is not None:
            raise LedgerImmutabilityError("entry already settled — append-only, no edits")
        row.outcome = dict(outcome)
        row.settled_at = time.time()

    def amend_features(self, entry_id: str, *_args, **_kw):
        raise LedgerImmutabilityError("features are frozen at decision time — no recomputation")

    # ---------- calibration ----------

    def brier_score(self, kind: str = None, last_n: int = None) -> Optional[float]:
        rows = [r for r in self._rows.values()
                if r.settled_at is not None and (kind is None or r.kind == kind)
                and "p" in r.prediction and "hit" in (r.outcome or {})]
        rows.sort(key=lambda r: r.settled_at)
        if last_n:
            rows = rows[-last_n:]
        if not rows:
            return None
        return sum((r.prediction["p"] - (1.0 if r.outcome["hit"] else 0.0)) ** 2
                   for r in rows) / len(rows)

    def should_demote(self) -> bool:
        """Calibration drift ⇒ model demotes itself to abstain-mode (fail-toward-safety)."""
        recent = self.brier_score(last_n=self.drift_window)
        return recent is not None and recent > self.brier_demote_threshold

    def rows_for_training(self) -> list:
        return [r for r in self._rows.values() if r.settled_at is not None]

    def __len__(self) -> int:
        return len(self._rows)
