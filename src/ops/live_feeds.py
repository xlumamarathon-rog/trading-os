"""MODULE 67 — Live Quote Feeds (Aug 2026).

Real prices behind the exact interface ReplayQuoteFeed (M62) established, so
run_paper and the strategy engine cannot tell the difference:

    tick_once(now) -> {symbol: price}     (async here — real HTTP)
    last_price / candles / atr_proxy / bars_window / completed_count / status

Providers:
  YahooQuoteFeed   query1.finance.yahoo.com chart API — verified live
                   2026-08-12 (ledger): NSE quote age 1–2s in session, one
                   endpoint for india/forex/crypto, no credentials. Budgeted:
                   one HTTP call per min_gap_s, round-robin across OPEN
                   symbols only (a closed market is never polled).
  Mt5QuoteFeed     the mt5_service bridge's /tick and /candles — the BROKER'S
                   own bid/ask including spread; the only correct live source
                   for anything that executes on MT5 (feed doctrine, ledger).
  FeedMux          per-leg composition: mt5 legs on the bridge, india on
                   Yahoo — "trade on the feed you execute on".

Fail-soft by contract: any provider error counts a strike; after
max_errors the feed flags itself degraded and DELEGATES to the injected
ReplayQuoteFeed fallback — the cockpit keeps working on replayed real
history and says so in status(). A recovered provider un-degrades.

Session-aware at the feed level (MODULE 58 clock): closed legs are neither
polled nor advanced — no phantom night candles, and no wasted budget.
"""
from __future__ import annotations

import datetime as dt
import time
from collections import deque
from typing import Optional

import httpx

UTC = dt.timezone.utc

# Yahoo symbol map — mirrors scripts/fetch_market_data.py ("yh" fields), so
# live feed and bundled backtest data share one pedigree.
YAHOO_SYMBOLS = {
    "RELIANCE": "RELIANCE.NS", "TCS": "TCS.NS", "HDFCBANK": "HDFCBANK.NS",
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "JPY=X",
    "AUDUSD": "AUDUSD=X", "USDCAD": "CAD=X", "NZDUSD": "NZDUSD=X",
    "EURJPY": "EURJPY=X", "GBPJPY": "GBPJPY=X", "XAUUSD": "GC=F",
    "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD",
}


class _BaseLiveFeed:
    """Shared cache/session/fallback plumbing for HTTP-backed feeds."""

    kind = "live"

    def __init__(self, symbols, *, market_clock=None, symbol_legs=None,
                 fallback=None, min_gap_s: float = 8.0,
                 candle_interval_s: int = 300, history: int = 96,
                 max_errors: int = 5) -> None:
        self.symbols = list(symbols)
        # M70: optional FlowTelemetry recorder (records only, never trades).
        # Feeds that receive bid/ask/volume in their payloads hand the raw
        # snapshot over instead of discarding it. None = disabled.
        self.flow = None
        self.market_clock = market_clock
        self.symbol_legs = symbol_legs or {}
        self.fallback = fallback
        self.min_gap_s = min_gap_s
        self.candle_interval_s = candle_interval_s
        self.max_errors = max_errors
        self._last: dict[str, float] = {}
        self._candles: dict[str, deque] = {s: deque(maxlen=history)
                                           for s in self.symbols}
        self._daily: dict[str, list] = {}
        self._completed: dict[str, int] = {s: 0 for s in self.symbols}
        self._rr: deque = deque(self.symbols)
        self._last_http = 0.0
        self._errors = 0
        self.degraded = False
        self._daily_loaded = False

    # ---------------- provider hooks ----------------

    async def _fetch_last(self, symbol: str) -> Optional[float]:  # pragma: no cover
        raise NotImplementedError

    async def _fetch_daily(self, symbol: str, n: int = 250) -> list:  # pragma: no cover
        raise NotImplementedError

    # ---------------- session ----------------

    def _leg_open(self, symbol: str, now: dt.datetime) -> bool:
        leg = self.symbol_legs.get(symbol)
        if leg is None or self.market_clock is None:
            return True
        return self.market_clock.is_open(leg, now)

    # ---------------- the tick ----------------

    async def _ensure_daily(self) -> None:
        if self._daily_loaded:
            return
        for sym in self.symbols:
            try:
                bars = await self._fetch_daily(sym)
                if bars:
                    self._daily[sym] = bars
            except Exception:  # noqa: BLE001 — bars_window falls back
                self._errors += 1
        self._daily_loaded = True

    async def tick_once(self, now: Optional[dt.datetime] = None) -> dict:
        now = now or dt.datetime.now(UTC)
        if self.degraded and self.fallback is not None:
            # keep probing the provider once per gap so we can recover
            if time.monotonic() - self._last_http >= self.min_gap_s:
                self._last_http = time.monotonic()
                try:
                    probe = await self._fetch_last(self.symbols[0])
                    if probe is not None:
                        self._errors = 0
                        self.degraded = False
                except Exception:  # noqa: BLE001
                    pass
            if self.degraded:
                import inspect
                res = self.fallback.tick_once(now)
                if inspect.isawaitable(res):        # fallback may itself be live
                    res = await res
                return res
        await self._ensure_daily()
        if time.monotonic() - self._last_http < self.min_gap_s:
            return {}
        # round-robin: next OPEN symbol only
        for _ in range(len(self._rr)):
            sym = self._rr[0]
            self._rr.rotate(-1)
            if self._leg_open(sym, now):
                break
        else:
            return {}                              # every market closed
        self._last_http = time.monotonic()
        try:
            px = await self._fetch_last(sym)
        except Exception:  # noqa: BLE001 — strike, maybe degrade
            self._errors += 1
            if self._errors >= self.max_errors and self.fallback is not None:
                self.degraded = True
            return {}
        if px is None:
            return {}
        self._errors = 0
        self._last[sym] = px
        self._aggregate(sym, px, now)
        self._roll_daily(sym, now)
        return {sym: px}

    def _aggregate(self, sym: str, px: float, now: dt.datetime) -> None:
        bucket = int(now.timestamp() // self.candle_interval_s) * self.candle_interval_s
        cs = self._candles[sym]
        if cs and cs[-1]["ts"] == bucket:
            k = cs[-1]
            k["h"] = max(k["h"], px); k["l"] = min(k["l"], px); k["c"] = px
        else:
            cs.append({"ts": bucket, "o": px, "h": px, "l": px, "c": px})

    def _roll_daily(self, sym: str, now: dt.datetime) -> None:
        """A completed daily bar = strategy clock edge (M65)."""
        bars = self._daily.get(sym)
        if not bars:
            return
        last_date = str(bars[-1].get("date", ""))
        today = now.date().isoformat()
        if last_date and last_date < today and self._last.get(sym) is not None:
            # yesterday's bar is now complete; open today's from the live px
            px = self._last[sym]
            bars.append({"date": today, "open": px, "high": px,
                         "low": px, "close": px})
            self._completed[sym] += 1
        elif last_date == today:
            px = self._last.get(sym)
            if px is not None:
                b = bars[-1]
                b["high"] = max(b["high"], px)
                b["low"] = min(b["low"], px)
                b["close"] = px

    # ---------------- read API (ReplayQuoteFeed contract) ----------------

    def last_price(self, symbol: str) -> Optional[float]:
        if self.degraded and self.fallback is not None:
            return self.fallback.last_price(symbol)
        px = self._last.get(symbol)
        if px is None and self._daily.get(symbol):
            return float(self._daily[symbol][-1]["close"])
        return px

    def candles(self, symbol: str, n: int = 96) -> list:
        if self.degraded and self.fallback is not None:
            return self.fallback.candles(symbol, n)
        return list(self._candles.get(symbol, []))[-n:]

    def atr_proxy(self, symbol: str, n: int = 14) -> Optional[float]:
        bars = self._daily.get(symbol)
        if bars and len(bars) > n:
            window = bars[-n:]
            return sum(b["high"] - b["low"] for b in window) / n or None
        cs = self.candles(symbol, n)
        if not cs:
            return None
        return sum(k["h"] - k["l"] for k in cs) / len(cs) or None

    def bars_window(self, symbol: str, n: int = 200) -> list:
        bars = self._daily.get(symbol)
        if bars:
            return bars[-n:]
        if self.fallback is not None:
            return self.fallback.bars_window(symbol, n)
        return []

    def completed_count(self, symbol: str) -> int:
        return self._completed.get(symbol, 0)

    def status(self) -> dict:
        return {"kind": self.kind + ("_DEGRADED_replay" if self.degraded else ""),
                "errors": self._errors,
                "symbols": {s: {"last": self._last.get(s),
                                "daily_bars": len(self._daily.get(s, [])),
                                "completed": self._completed.get(s, 0)}
                            for s in self.symbols}}


class YahooQuoteFeed(_BaseLiveFeed):
    kind = "yahoo_live"

    def __init__(self, symbols, *, client: Optional[httpx.AsyncClient] = None,
                 **kw) -> None:
        super().__init__(symbols, **kw)
        self._client = client or httpx.AsyncClient(
            timeout=10.0, headers={"User-Agent": "Mozilla/5.0"})
        self._yh = {s: YAHOO_SYMBOLS.get(s, s) for s in self.symbols}

    async def _chart(self, symbol: str, interval: str, rng: str) -> dict:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{self._yh[symbol]}?interval={interval}&range={rng}")
        resp = await self._client.get(url)
        resp.raise_for_status()
        return resp.json()["chart"]["result"][0]

    async def _fetch_last(self, symbol: str) -> Optional[float]:
        r = await self._chart(symbol, "1m", "1d")
        px = r["meta"].get("regularMarketPrice")
        return float(px) if px is not None else None

    async def _fetch_daily(self, symbol: str, n: int = 250) -> list:
        r = await self._chart(symbol, "1d", "1y")
        ts = r.get("timestamp") or []
        q = r["indicators"]["quote"][0]
        out = []
        for i, t in enumerate(ts):
            o, h, l, c = (q["open"][i], q["high"][i], q["low"][i], q["close"][i])
            if None in (o, h, l, c):
                continue
            out.append({"date": dt.datetime.fromtimestamp(t, UTC).date().isoformat(),
                        "open": float(o), "high": float(h),
                        "low": float(l), "close": float(c)})
        return out[-n:]


class Mt5QuoteFeed(_BaseLiveFeed):
    kind = "mt5_bridge"

    def __init__(self, symbols, *, base_url: str, token: str = "",
                 client: Optional[httpx.AsyncClient] = None,
                 min_gap_s: float = 1.0, **kw) -> None:
        super().__init__(symbols, min_gap_s=min_gap_s, **kw)
        headers = {"X-MT5-Auth": token} if token else {}
        self._client = client or httpx.AsyncClient(
            timeout=6.0, headers=headers, verify=False,
            base_url=base_url.rstrip("/"))

    async def _fetch_last(self, symbol: str) -> Optional[float]:
        resp = await self._client.get(f"/tick/{symbol}")
        resp.raise_for_status()
        t = resp.json()
        mid = (float(t["bid"]) + float(t["ask"])) / 2.0
        if self.flow is not None:
            # M70 flow proxy. HONESTY NOTE: for OTC spot FX the MT5 "volume"
            # field counts quote updates, not traded contracts — recorded raw
            # and labeled PROXY; only exchange-traded MT5 symbols carry real
            # volume/aggressor data (docs/EXECUTION_AUDIT + orderflow ledger).
            self.flow.on_snapshot(symbol, ts=time.time(),
                                  ltp=float(t.get("last") or mid),
                                  bid=float(t["bid"]), ask=float(t["ask"]),
                                  cum_volume=t.get("volume"))
        # mid of the broker's real bid/ask — the exit engine marks with it;
        # the ROUTER's fills happen broker-side at the true touch anyway
        return mid

    async def _fetch_daily(self, symbol: str, n: int = 250) -> list:
        resp = await self._client.get(f"/candles/{symbol}",
                                      params={"timeframe": "D1", "count": n})
        resp.raise_for_status()
        return [{"date": dt.datetime.fromtimestamp(b["ts"], UTC).date().isoformat(),
                 "open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"]}
                for b in resp.json()]


class OpenAlgoQuoteFeed(_BaseLiveFeed):
    """India real-time via the operator's OWN OpenAlgo hub (MODULE 68).

    The 'MT5 for India': one connection to the self-hosted hub; the broker
    underneath (dhan | angel | fyers | zerodha | 20+ streaming adapters in
    OpenAlgo) is swappable without touching this code. Shapes verified
    against vendored OpenAlgo source + docs (R1):
      POST /api/v1/multiquotes {apikey, symbols:[{symbol,exchange}...]}
        -> {status, results:[{symbol, data:{ltp,bid,ask,open,high,low,...}}]}
      POST /api/v1/history {apikey, symbol, exchange, interval:"D", ...}
        -> {status, data:[{timestamp,open,high,low,close,volume}...]}

    BATCHED: one multiquotes call covers the whole universe per poll —
    against localhost this sustains ~1s cadence at trivial cost, which for
    a daily-bar engine with broker-resident stops is operationally
    equivalent to a websocket. The ws-proxy upgrade can replace _fetch_all
    later without touching any consumer."""

    kind = "openalgo_hub"

    def __init__(self, symbols, *, base_url: str, apikey: str = "",
                 exchange: str = "NSE",
                 client: Optional[httpx.AsyncClient] = None,
                 min_gap_s: float = 1.5, **kw) -> None:
        super().__init__(symbols, min_gap_s=min_gap_s, **kw)
        self._apikey = apikey
        self._exchange = exchange
        self._client = client or httpx.AsyncClient(
            timeout=6.0, base_url=base_url.rstrip("/"))

    async def _fetch_all(self, symbols) -> dict:
        resp = await self._client.post("/api/v1/multiquotes", json={
            "apikey": self._apikey,
            "symbols": [{"symbol": s, "exchange": self._exchange}
                        for s in symbols]})
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            raise RuntimeError(f"openalgo multiquotes: {body.get('message')}")
        out = {}
        for row in body.get("results", []):
            data = row.get("data") or {}
            ltp = data.get("ltp")
            if ltp:
                out[row["symbol"]] = float(ltp)
                if self.flow is not None:
                    # M70 flow proxy: NSE snapshots carry cumulative day
                    # volume (TTQ) + best bid/ask — the exact inputs of the
                    # 1-second order-flow PROXY (never a footprint: NSE
                    # retail feeds have no trade tape by spec).
                    self.flow.on_snapshot(
                        row["symbol"], ts=time.time(), ltp=float(ltp),
                        bid=data.get("bid"), ask=data.get("ask"),
                        cum_volume=data.get("volume"))
        return out

    async def _fetch_last(self, symbol: str) -> Optional[float]:
        return (await self._fetch_all([symbol])).get(symbol)

    async def _fetch_daily(self, symbol: str, n: int = 250) -> list:
        end = dt.date.today()
        start = end - dt.timedelta(days=int(n * 1.6) + 10)
        resp = await self._client.post("/api/v1/history", json={
            "apikey": self._apikey, "symbol": symbol,
            "exchange": self._exchange, "interval": "D",
            "start_date": start.isoformat(), "end_date": end.isoformat()})
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            raise RuntimeError(f"openalgo history: {body.get('message')}")
        out = []
        for b in body.get("data", []):
            out.append({"date": str(b["timestamp"])[:10],
                        "open": float(b["open"]), "high": float(b["high"]),
                        "low": float(b["low"]), "close": float(b["close"])})
        return out[-n:]

    async def tick_once(self, now: Optional[dt.datetime] = None) -> dict:
        """Batched override: ONE multiquotes call for every OPEN symbol."""
        now = now or dt.datetime.now(UTC)
        if self.degraded and self.fallback is not None:
            if time.monotonic() - self._last_http >= self.min_gap_s:
                self._last_http = time.monotonic()
                try:
                    if await self._fetch_all(self.symbols[:1]):
                        self._errors = 0
                        self.degraded = False
                except Exception:  # noqa: BLE001
                    pass
            if self.degraded:
                import inspect
                res = self.fallback.tick_once(now)
                if inspect.isawaitable(res):
                    res = await res
                return res
        await self._ensure_daily()
        if time.monotonic() - self._last_http < self.min_gap_s:
            return {}
        open_syms = [s for s in self.symbols if self._leg_open(s, now)]
        if not open_syms:
            return {}
        self._last_http = time.monotonic()
        try:
            ticks = await self._fetch_all(open_syms)
        except Exception:  # noqa: BLE001 — strike, maybe degrade
            self._errors += 1
            if self._errors >= self.max_errors and self.fallback is not None:
                self.degraded = True
            return {}
        self._errors = 0
        for sym, px in ticks.items():
            self._last[sym] = px
            self._aggregate(sym, px, now)
            self._roll_daily(sym, now)
        return ticks


class FeedMux:
    """Per-leg composition — trade on the feed you execute on. Routes every
    read to the feed that owns the symbol; tick_once fans out to all."""

    def __init__(self, routes: dict) -> None:
        """routes: {feed: [symbols...]} — first feed owning a symbol wins."""
        self._feeds = list(routes)
        self._by_symbol = {}
        for feed, syms in routes.items():
            for s in syms:
                self._by_symbol.setdefault(s, feed)

    def _feed_for(self, symbol: str):
        return self._by_symbol.get(symbol)

    async def tick_once(self, now: Optional[dt.datetime] = None) -> dict:
        import inspect
        out = {}
        for feed in self._feeds:
            res = feed.tick_once(now)
            if inspect.isawaitable(res):
                res = await res
            out.update(res or {})
        return out

    def last_price(self, symbol: str):
        f = self._feed_for(symbol)
        return f.last_price(symbol) if f else None

    def candles(self, symbol: str, n: int = 96) -> list:
        f = self._feed_for(symbol)
        return f.candles(symbol, n) if f else []

    def atr_proxy(self, symbol: str, n: int = 14):
        f = self._feed_for(symbol)
        return f.atr_proxy(symbol, n) if f else None

    def bars_window(self, symbol: str, n: int = 200) -> list:
        f = self._feed_for(symbol)
        return f.bars_window(symbol, n) if f else []

    def completed_count(self, symbol: str) -> int:
        f = self._feed_for(symbol)
        return f.completed_count(symbol) if f else 0

    def status(self) -> dict:
        merged = {"kind": "mux", "symbols": {}}
        for feed in self._feeds:
            st = feed.status()
            merged["symbols"].update(st.get("symbols", {}))
            merged.setdefault("feeds", []).append(st.get("kind"))
        return merged
