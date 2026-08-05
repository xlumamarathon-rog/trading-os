"""MODULE 50 — Universe Manager (Aug 2026).

Breadth of uncorrelated instruments was the single biggest measured
performance lever (+12.95%/yr diversified vs ~−2.5% as separate books, same
year). This module makes the tradable universe a managed, screened object
instead of hardcoded dicts.

Each instrument spec carries leg routing + microstructure params; screens
enforce liquidity and lifecycle (listed/delisted windows)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Instrument:
    symbol: str
    leg: str                        # india | mt5_forex | mt5_crypto
    lot: float
    adv: float                      # average daily volume (units)
    half_spread: float = 0.0        # MT5 CFD legs only
    commission_pct: float = 0.0
    listed: str = ""                # optional "YYYY-MM-DD" lifecycle bounds
    delisted: str = ""
    tags: list = field(default_factory=list)

    def as_meta(self) -> dict:
        """Replay-harness META entry."""
        m = {"leg": self.leg, "lot": self.lot, "adv": self.adv}
        if self.leg.startswith("mt5"):
            m["half_spread"] = self.half_spread
            m["commission_pct"] = self.commission_pct
        return m


class UniverseManager:
    def __init__(self, instruments: list) -> None:
        self.instruments = {i.symbol: i for i in instruments}

    @classmethod
    def from_file(cls, path: str | Path) -> "UniverseManager":
        spec = json.loads(Path(path).read_text())
        return cls([Instrument(symbol=s, **cfg) for s, cfg in spec["symbols"].items()])

    def eligible(self, *, date: str = "", min_adv_notional: float = 0.0,
                 price_of=None, legs: Optional[list] = None) -> list:
        """Screened symbol list. min_adv_notional needs price_of(symbol) to
        turn ADV units into notional; lifecycle bounds respect `date`."""
        out = []
        for sym, ins in self.instruments.items():
            if legs and ins.leg not in legs:
                continue
            if date and ins.listed and date < ins.listed:
                continue
            if date and ins.delisted and date >= ins.delisted:
                continue
            if min_adv_notional > 0:
                if price_of is None:
                    continue           # fail-closed: can't prove liquidity
                px = price_of(sym)
                if not px or ins.adv * px < min_adv_notional:
                    continue
            out.append(sym)
        return sorted(out)

    def meta_for(self, symbols: list) -> dict:
        return {s: self.instruments[s].as_meta() for s in symbols}
