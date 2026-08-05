"""MODULE 54 — Shadow mode (Aug 2026).

The last de-risking step between paper certification and real money: run the
FULL decision path (strategy signal → session guard → portfolio heat →
router intent) against live market data with paper consequences, WHILE the
real process trades — then diff the two decision streams. Any divergence
means the live process is not doing what the certified logic would do.

Two parts:
  ShadowRunner   consumes bars, produces an append-only decision log
                 (symbol, date, signal, admitted, reject_reason)
  diff_decisions compares shadow vs live order-intent streams and reports
                 mismatches by kind (missing_live, missing_shadow,
                 direction_mismatch)

Shadow NEVER places real orders and NEVER blocks the live path — it is a
witness, not a participant."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, Optional


@dataclass
class ShadowDecision:
    date: str
    symbol: str
    signal: Optional[str]          # "buy" | "sell" | None
    admitted: bool                 # passed guards (would have been routed)
    reject_reason: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ParityReport:
    matched: int = 0
    missing_live: list = field(default_factory=list)     # shadow said trade, live didn't
    missing_shadow: list = field(default_factory=list)   # live traded, shadow wouldn't
    direction_mismatch: list = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.missing_live or self.missing_shadow or self.direction_mismatch)


class ShadowRunner:
    def __init__(self, *, signal_fn: Callable, regime_fn: Callable,
                 entry_allowed_fn: Optional[Callable] = None) -> None:
        """signal_fn(bars, i, regime) -> direction|None (MODULE 47 contract).
        entry_allowed_fn() -> (ok, reason): session guard / heat / throttle
        stack; None means always allowed."""
        self.signal_fn = signal_fn
        self.regime_fn = regime_fn
        self.entry_allowed_fn = entry_allowed_fn
        self.decisions: list[ShadowDecision] = []

    def on_bar(self, symbol: str, bars: list, i: int) -> ShadowDecision:
        regime = self.regime_fn(bars, i)
        signal = self.signal_fn(bars, i, regime)
        admitted, why = True, ""
        if signal and self.entry_allowed_fn is not None:
            admitted, why = self.entry_allowed_fn()
        d = ShadowDecision(date=bars[i]["date"], symbol=symbol, signal=signal,
                           admitted=bool(signal) and admitted,
                           reject_reason="" if admitted else why)
        self.decisions.append(d)
        return d

    def intents(self) -> list[dict]:
        """The order intents shadow WOULD have routed."""
        return [{"date": d.date, "symbol": d.symbol, "direction": d.signal}
                for d in self.decisions if d.admitted and d.signal]


def diff_decisions(shadow_intents: list, live_intents: list) -> ParityReport:
    """Both streams: [{date, symbol, direction}]. Keyed by (date, symbol)."""
    rep = ParityReport()
    s = {(d["date"], d["symbol"]): d["direction"] for d in shadow_intents}
    l = {(d["date"], d["symbol"]): d["direction"] for d in live_intents}
    for k, sdir in s.items():
        if k not in l:
            rep.missing_live.append({"date": k[0], "symbol": k[1], "shadow": sdir})
        elif l[k] != sdir:
            rep.direction_mismatch.append({"date": k[0], "symbol": k[1],
                                           "shadow": sdir, "live": l[k]})
        else:
            rep.matched += 1
    for k, ldir in l.items():
        if k not in s:
            rep.missing_shadow.append({"date": k[0], "symbol": k[1], "live": ldir})
    return rep
