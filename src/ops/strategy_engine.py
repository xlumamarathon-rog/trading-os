"""MODULE 65 — Strategy Engine (Aug 2026).

Automatic sleeve trading for the paper cockpit: on every completed feed bar,
each ENABLED sleeve evaluates its registered signal on the same real daily
series the backtests were certified on, and any entry goes through the SAME
router door as a manual ticket — kill switch, anomaly pause, session clock,
portfolio guards, sizing, margin. The engine adds zero order logic of its
own; it only decides WHEN to knock on the router's door.

Safety posture (spec §12):
  - every sleeve boots DISABLED. Enabling one is a deliberate, audited
    operator act in the cockpit — the auto-trader equivalent of SAFE-START.
  - one open position per symbol, ever (the ExitManager owns the symbol
    until it exits). Sleeves race in registry order; first router-accepted
    entry claims the symbol.
  - the engine NEVER exits positions — the ExitManager state machine and
    the operator's CLOSE button own that side.
  - a sleeve that throws is disabled and reported, not retried silently.

Attribution: the engine remembers which sleeve opened which symbol, so the
cockpit's per-sleeve realized-R ledger is exact, not inferred.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from src.core.order_router import OrderRequest
from src.strategies.signals import SIGNALS, sma

logger = logging.getLogger("trading_os.strategy_engine")


def atr14(bars, i) -> Optional[float]:
    """Wilder-style simple ATR14 — same helper the certified replay uses."""
    if i < 15:
        return None
    trs = []
    for k in range(i - 13, i + 1):
        h, l, pc = bars[k]["high"], bars[k]["low"], bars[k - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / 14


def real_regime(bars, i) -> dict:
    """SYMMETRIC regime from real bars — the certified replay's computation
    (scripts/research_replay.py::real_regime), reproduced verbatim so live
    paper sleeves see the same regime the backtests saw."""
    s = sma(bars, i)
    tr = max(bars[i]["high"] - bars[i]["low"],
             abs(bars[i]["high"] - bars[i - 1]["close"]),
             abs(bars[i]["low"] - bars[i - 1]["close"]))
    a = atr14(bars, i)
    vol = "SHOCK" if (a and tr > 2.5 * a) else "NORMAL"
    if s is None:
        return {"trend_state": "RANGE", "vol_regime": vol, "trend_direction": "FLAT"}
    dev = (bars[i - 1]["close"] - s) / s
    strength = abs(dev)
    trend = ("STRONG_TREND" if strength > 0.02
             else ("WEAK_TREND" if strength > 0.005 else "RANGE"))
    direction = "UP" if dev > 0.002 else ("DOWN" if dev < -0.002 else "FLAT")
    return {"trend_state": trend, "vol_regime": vol, "trend_direction": direction}


class StrategyEngine:
    def __init__(self, *, router, exit_mgr, feed, universe: dict,
                 note_fn: Optional[Callable] = None,
                 signals: Optional[dict] = None,
                 stop_atr_mult: float = 2.0) -> None:
        """universe: {symbol: {leg, lot, ...}} — the paper feed's symbols."""
        self.router = router
        self.exit_mgr = exit_mgr
        self.feed = feed
        self.universe = universe
        self.note = note_fn or (lambda msg: None)
        self.signals = dict(signals if signals is not None else SIGNALS)
        self.stop_atr_mult = stop_atr_mult
        self._seen: dict[str, int] = {s: feed.completed_count(s) for s in universe}
        self._sleeve_of: dict[str, str] = {}          # open symbol -> sleeve
        self.sleeves: dict[str, dict] = {
            name: {"enabled": False, "entries": 0, "rejections": 0,
                   "closed": 0, "wins": 0, "realized_r": 0.0,
                   "last_signal": None, "error": None}
            for name in sorted(self.signals)}

    # ---------------- operator controls (gateway-wired) ----------------

    def set_enabled(self, sleeve: str, enabled: bool, actor: str = "") -> dict:
        if sleeve not in self.sleeves:
            raise KeyError(f"unknown sleeve {sleeve!r}")
        self.sleeves[sleeve]["enabled"] = bool(enabled)
        if enabled:
            self.sleeves[sleeve]["error"] = None      # re-enabling clears fault
        self.note(f"sleeve {sleeve} {'ENABLED' if enabled else 'disabled'}"
                  f"{' by ' + actor if actor else ''}")
        return {"sleeve": sleeve, "enabled": bool(enabled)}

    def status(self) -> dict:
        open_by_sleeve: dict[str, int] = {}
        for sym, sleeve in self._sleeve_of.items():
            pos = self.exit_mgr.positions.get(sym)
            if pos is not None and pos.state != "EXITED":
                open_by_sleeve[sleeve] = open_by_sleeve.get(sleeve, 0) + 1
        return {"sleeves": [
            {"name": name, **stats, "open_positions": open_by_sleeve.get(name, 0)}
            for name, stats in self.sleeves.items()]}

    # ---------------- attribution (run_paper on_exit hook) ----------------

    def sleeve_for(self, symbol: str) -> Optional[str]:
        return self._sleeve_of.get(symbol)

    def record_exit(self, symbol: str, realized_r: float) -> None:
        sleeve = self._sleeve_of.pop(symbol, None)
        if sleeve is None or sleeve not in self.sleeves:
            return
        st = self.sleeves[sleeve]
        st["closed"] += 1
        st["realized_r"] = round(st["realized_r"] + realized_r, 4)
        if realized_r > 0:
            st["wins"] += 1

    # ---------------- the clock edge ----------------

    async def on_tick(self) -> None:
        """Call once per feed-loop iteration. Fires each sleeve's signal on
        every symbol whose bar JUST completed."""
        for sym in self.universe:
            completed = self.feed.completed_count(sym)
            if completed <= self._seen.get(sym, 0):
                continue
            self._seen[sym] = completed
            await self._evaluate_symbol(sym)

    async def _evaluate_symbol(self, sym: str) -> None:
        pos = self.exit_mgr.positions.get(sym)
        if pos is not None and pos.state != "EXITED":
            return                                    # symbol is owned until exit
        px = self.feed.last_price(sym)
        if px is None:
            return
        hist = self.feed.bars_window(sym, 200)
        if len(hist) < 20:
            return
        # contract shape: decisions on bars[:i], entry at bars[i]["open"] —
        # the just-started bar carries the live mark as its open
        bars = hist + [{"open": px, "high": px, "low": px, "close": px}]
        i = len(bars) - 1
        regime = real_regime(bars, i - 1)
        a = atr14(bars, i - 1)
        if not a:
            return
        for name, stats in self.sleeves.items():
            if not stats["enabled"]:
                continue
            try:
                direction = self.signals[name](bars, i, regime)
            except Exception as exc:  # noqa: BLE001 — fail-loud, stop the sleeve
                stats["enabled"] = False
                stats["error"] = f"{type(exc).__name__}: {exc}"
                self.note(f"sleeve {name} DISABLED after error on {sym}: {exc}")
                logger.exception("sleeve %s failed on %s", name, sym)
                continue
            if direction not in ("buy", "sell"):
                continue
            stats["last_signal"] = f"{direction} {sym}"
            if await self._enter(sym, direction, px, a, name, stats):
                break                                 # symbol claimed

    async def _enter(self, sym: str, direction: str, px: float, a: float,
                     sleeve: str, stats: dict) -> bool:
        meta = self.universe[sym]
        stop = px - self.stop_atr_mult * a if direction == "buy" \
            else px + self.stop_atr_mult * a
        req = OrderRequest(
            symbol=sym, direction=direction, entry=px, stop=stop, atr=a,
            algo_id="ALGO-PAPER-1" if meta.get("leg") == "india" else None,
            lot_size=meta.get("lot", 1.0), product="intraday")
        result = await self.router.route_order(req)
        if result.accepted and result.record.filled_qty > 0:
            self.exit_mgr.positions.pop(sym, None)
            await self.exit_mgr.attach(
                symbol=sym, direction=direction,
                entry=result.record.avg_fill_price,
                qty=result.record.filled_qty, atr=a,
                leg=meta.get("leg", "india"), lot_size=meta.get("lot", 1.0))
            self._sleeve_of[sym] = sleeve
            stats["entries"] += 1
            self.note(f"AUTO {sleeve}: {direction} {sym} "
                      f"x{result.record.filled_qty} @ {result.record.avg_fill_price}")
            return True
        stats["rejections"] += 1
        return False
