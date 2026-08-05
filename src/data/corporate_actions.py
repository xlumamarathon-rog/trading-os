"""MODULE 49 — Corporate-actions adjuster (Aug 2026).

NSE equities split, bonus and dividend events distort every indicator built
on raw prices (a 1:1 RELIANCE bonus halves the price overnight — SMA/ATR see
a 50% crash and fire phantom signals). This module back-adjusts historical
bars so the series is continuous through each ex-date.

Convention: bars BEFORE the ex-date are divided by the cumulative factor
(prices) and multiplied (volume); bars on/after the ex-date are untouched —
the standard back-adjustment used by every serious data vendor.

  split 1:2  (one share becomes two)      factor = 2.0
  bonus 1:1  (one free share per share)   factor = 2.0
  dividend D (cash payout per share)      factor = close_before / (close_before − D)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CorporateAction:
    symbol: str
    ex_date: str                   # "YYYY-MM-DD" — first date trading ex
    kind: str                      # "split" | "bonus" | "dividend"
    factor: float = 0.0            # price divisor for split/bonus (e.g. 2.0)
    amount: float = 0.0            # cash per share for dividends

    def price_factor(self, close_before: float) -> float:
        if self.kind in ("split", "bonus"):
            if self.factor <= 1.0:
                raise ValueError(f"{self.kind} factor must be > 1, got {self.factor}")
            return self.factor
        if self.kind == "dividend":
            if self.amount <= 0 or self.amount >= close_before:
                raise ValueError("dividend amount must be in (0, close_before)")
            return close_before / (close_before - self.amount)
        raise ValueError(f"unknown corporate action kind: {self.kind}")


def adjust_bars(bars: list, actions: list) -> tuple[list, list]:
    """Back-adjust daily bars ({date, open, high, low, close[, volume]}) for
    a single symbol. Returns (adjusted_bars, applied_log). Never mutates the
    input. Actions with ex-dates outside the data range are skipped+logged."""
    if not bars:
        return [], []
    out = [dict(b) for b in bars]
    applied = []
    for act in sorted(actions, key=lambda a: a.ex_date):
        idx = next((i for i, b in enumerate(out) if b["date"] >= act.ex_date), None)
        if idx is None or idx == 0:
            applied.append({"ex_date": act.ex_date, "kind": act.kind,
                            "applied": False, "why": "outside data range"})
            continue
        f = act.price_factor(out[idx - 1]["close"])
        for b in out[:idx]:
            for k in ("open", "high", "low", "close"):
                b[k] = round(b[k] / f, 6)
            if "volume" in b:
                b["volume"] = b["volume"] * f
        applied.append({"ex_date": act.ex_date, "kind": act.kind,
                        "applied": True, "factor": round(f, 6)})
    return out, applied
