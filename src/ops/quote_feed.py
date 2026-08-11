"""MODULE 62 — Paper Quote Feed (Aug 2026).

A credential-free price feed for the runnable paper cockpit
(scripts/run_paper.py): replays the repo's REAL bundled OHLC bar-by-bar,
synthesizing ticks INSIDE each bar's true range (the paper_replay_real
convention: up-bars test the low first, down-bars the high first — the
conservative ordering). No random walks, no invented prices.

Session-aware by construction: a leg advances ONLY while its market is open
(MODULE 58 MarketClock) — the fix for phantom night candles, applied at the
feed level this time. The cockpit labels this feed "REPLAY (real history)".

The interface is the contract, not this implementation:
    last_price(symbol) -> float | None
    candles(symbol, n) -> [{ts, o, h, l, c}]
    tick_once(now) -> {symbol: price} for legs whose market is open
On the VPS the same interface is backed by OpenAlgo/MT5 live quotes.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import deque
from pathlib import Path
from typing import Optional

UTC = dt.timezone.utc

# fraction-of-range waypoints inside a bar; conservative path ordering
_UP_PATH = (0.0, -1.0, 0.35, 1.0, 0.75)      # open -> low -> mid -> high -> close side
_DOWN_PATH = (0.0, 1.0, 0.65, -1.0, 0.25)


class ReplayQuoteFeed:
    """Replays bundled real OHLC as a live-ish tick stream, looping."""

    def __init__(self, data_dirs: dict, market_clock=None,
                 symbol_legs: Optional[dict] = None,
                 candle_interval_s: int = 300, history: int = 96) -> None:
        """data_dirs: {symbol: (data_dir, filename)} pointing at repo datasets.
        symbol_legs: {symbol: leg} for session gating (default: crypto/24-7)."""
        self.market_clock = market_clock
        self.symbol_legs = symbol_legs or {}
        self.candle_interval_s = candle_interval_s
        self._bars: dict[str, list] = {}
        self._cursor: dict[str, int] = {}
        self._last: dict[str, float] = {}
        self._candles: dict[str, deque] = {}
        self._tick_n: dict[str, int] = {}
        self._completed: dict[str, int] = {}
        for sym, (ddir, fname) in data_dirs.items():
            path = Path(ddir) / fname
            rows = json.loads(path.read_text())
            bars = rows["bars"] if isinstance(rows, dict) and "bars" in rows else rows
            self._bars[sym] = [b for b in bars
                               if all(k in b for k in ("open", "high", "low", "close"))]
            if not self._bars[sym]:
                raise ValueError(f"quote_feed: no usable bars for {sym} in {path}")
            self._cursor[sym] = 0
            self._candles[sym] = deque(maxlen=history)
            self._tick_n[sym] = 0
            self._completed[sym] = 0
            self._last[sym] = float(self._bars[sym][0]["open"])

    # ---------------- tick synthesis ----------------

    def _leg_open(self, sym: str, now: dt.datetime) -> bool:
        leg = self.symbol_legs.get(sym)
        if leg is None or self.market_clock is None:
            return True
        return self.market_clock.is_open(leg, now)

    def _next_price(self, sym: str) -> float:
        bars, i = self._bars[sym], self._cursor[sym]
        bar = bars[i % len(bars)]
        o, h, l, c = (float(bar["open"]), float(bar["high"]),
                      float(bar["low"]), float(bar["close"]))
        up = c >= o
        path = _UP_PATH if up else _DOWN_PATH
        step = self._tick_n[sym] % len(path)
        if step == len(path) - 1:                     # bar exhausted -> next bar
            self._cursor[sym] = (i + 1) % len(bars)
            self._completed[sym] += 1                 # MODULE 65 bar-close hook
        self._tick_n[sym] += 1
        w = path[step]
        mid = (h + l) / 2.0
        half = (h - l) / 2.0
        px = c if step == len(path) - 1 else mid + w * half
        return round(max(l, min(h, px)), 6)

    def tick_once(self, now: Optional[dt.datetime] = None) -> dict:
        """Advance one tick for every OPEN market; closed legs stay frozen."""
        now = now or dt.datetime.now(UTC)
        out = {}
        for sym in self._bars:
            if not self._leg_open(sym, now):
                continue
            px = self._next_price(sym)
            self._last[sym] = px
            self._aggregate(sym, px, now)
            out[sym] = px
        return out

    def _aggregate(self, sym: str, px: float, now: dt.datetime) -> None:
        bucket = int(now.timestamp() // self.candle_interval_s) * self.candle_interval_s
        cs = self._candles[sym]
        if cs and cs[-1]["ts"] == bucket:
            k = cs[-1]
            k["h"] = max(k["h"], px); k["l"] = min(k["l"], px); k["c"] = px
        else:
            cs.append({"ts": bucket, "o": px, "h": px, "l": px, "c": px})

    # ---------------- read API (gateway providers) ----------------

    def last_price(self, symbol: str) -> Optional[float]:
        return self._last.get(symbol)

    def candles(self, symbol: str, n: int = 96) -> list:
        return list(self._candles.get(symbol, []))[-n:]

    def atr_proxy(self, symbol: str, n: int = 14) -> Optional[float]:
        """Average candle range — good enough for manual-ticket sizing when
        no daily ATR is at hand; the sizer treats it as gap insurance."""
        cs = self.candles(symbol, n)
        if not cs:
            return None
        return sum(k["h"] - k["l"] for k in cs) / len(cs) or None

    def completed_count(self, symbol: str) -> int:
        """Bar completions since boot — the strategy engine's clock edge."""
        return self._completed.get(symbol, 0)

    def bars_window(self, symbol: str, n: int = 200) -> list:
        """The last n REAL file bars ending at the current cursor (wrapping),
        in the exact shape the signal contract expects (bars[:i] history).
        This is the same daily series the strategies were certified on."""
        bars = self._bars.get(symbol)
        if not bars:
            return []
        i = self._cursor[symbol]
        n = min(n, len(bars))
        return [bars[(i - n + k) % len(bars)] for k in range(n)]

    def status(self) -> dict:
        return {"kind": "replay_real_history",
                "symbols": {s: {"bars": len(b), "cursor": self._cursor[s],
                                "completed": self._completed[s],
                                "last": self._last.get(s)}
                            for s, b in self._bars.items()}}
