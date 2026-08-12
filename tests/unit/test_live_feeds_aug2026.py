"""MODULE 67 — live quote feeds. All HTTP is recorded fixtures via
httpx.MockTransport (no live network in tests — repo rule). Invariants:

  - Yahoo feed parses the REAL captured chart payloads (2026-08-12 live
    session) into last_price / daily bars_window / atr_proxy
  - closed markets are never polled; the HTTP budget (min_gap_s) is enforced
  - fail-soft: strikes -> degraded -> the replay fallback serves; a healed
    provider un-degrades
  - MT5 feed sends the auth header and mids the broker's real bid/ask
  - FeedMux routes reads per symbol and merges ticks
  - run_paper's make_feed honors FEED=replay|yahoo|mt5
"""
import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

from src.ops.live_feeds import FeedMux, Mt5QuoteFeed, YahooQuoteFeed
from src.ops.market_clock import MarketClock
from src.ops.quote_feed import ReplayQuoteFeed

UTC = dt.timezone.utc
FIX = Path("tests/fixtures")
INTRA = FIX / "yahoo_chart_intraday_reliance.json"
DAILY = FIX / "yahoo_chart_daily_reliance.json"

HOURS = {"india": {"open": "09:15", "close": "15:30",
                   "weekdays": [0, 1, 2, 3, 4], "holidays": []}}
SESSION_OPEN = dt.datetime(2026, 8, 12, 5, 0, tzinfo=UTC)     # 10:30 IST Wed
SESSION_CLOSED = dt.datetime(2026, 8, 12, 16, 0, tzinfo=UTC)  # 21:30 IST


def replay_fallback(tmp_path, sym="RELIANCE"):
    bars = [{"date": "2026-08-01", "open": 1300.0, "high": 1320.0,
             "low": 1290.0, "close": 1310.0},
            {"date": "2026-08-02", "open": 1310.0, "high": 1330.0,
             "low": 1300.0, "close": 1325.0}]
    p = tmp_path / f"{sym}.json"
    p.write_text(json.dumps(bars))
    return ReplayQuoteFeed({sym: (str(tmp_path), f"{sym}.json")})


def yahoo_transport(requests_log, fail=False):
    intra = INTRA.read_text()
    daily = DAILY.read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(str(request.url))
        if fail:
            raise httpx.ConnectError("provider down", request=request)
        body = daily if "interval=1d" in str(request.url) else intra
        return httpx.Response(200, text=body)

    return httpx.MockTransport(handler)


def make_yahoo(tmp_path, requests_log, *, fail=False, min_gap_s=0.0,
               clock=None, max_errors=3):
    client = httpx.AsyncClient(transport=yahoo_transport(requests_log, fail))
    return YahooQuoteFeed(
        ["RELIANCE"], client=client, market_clock=clock,
        symbol_legs={"RELIANCE": "india"},
        fallback=replay_fallback(tmp_path), min_gap_s=min_gap_s,
        max_errors=max_errors)


# ---------------------------------------------------------------- yahoo

async def test_yahoo_parses_the_real_fixture(tmp_path):
    log = []
    feed = make_yahoo(tmp_path, log)
    out = await feed.tick_once(SESSION_OPEN)
    assert out == {"RELIANCE": 1311.8}            # regularMarketPrice, live capture
    assert feed.last_price("RELIANCE") == 1311.8
    bars = feed.bars_window("RELIANCE", 200)
    assert len(bars) == 60                         # daily fixture depth
    assert bars[-1]["close"] == pytest.approx(1311.8, rel=1e-3)
    assert feed.atr_proxy("RELIANCE") > 0
    assert feed.status()["kind"] == "yahoo_live"


async def test_yahoo_never_polls_a_closed_market(tmp_path):
    log = []
    clock = MarketClock(HOURS)
    feed = make_yahoo(tmp_path, log, clock=clock)
    out = await feed.tick_once(SESSION_CLOSED)     # 21:30 IST — NSE closed
    assert out == {}
    tick_urls = [u for u in log if "interval=1m" in u]
    assert tick_urls == []                          # zero quote polls at night


async def test_yahoo_http_budget_enforced(tmp_path):
    log = []
    feed = make_yahoo(tmp_path, log)
    feed.min_gap_s = 3600.0                        # one call per hour max
    await feed.tick_once(SESSION_OPEN)
    n_after_first = len(log)
    await feed.tick_once(SESSION_OPEN)
    await feed.tick_once(SESSION_OPEN)
    assert len(log) == n_after_first               # budget respected


async def test_yahoo_fail_soft_degrades_to_replay_then_recovers(tmp_path):
    log = []
    feed = make_yahoo(tmp_path, log, fail=True, max_errors=2)
    for _ in range(4):                             # strike out
        await feed.tick_once(SESSION_OPEN)
    assert feed.degraded is True
    assert "DEGRADED" in feed.status()["kind"]
    # reads + ticks now come from the replay fallback (real bundled bars)
    out = await feed.tick_once(SESSION_OPEN)
    assert "RELIANCE" in out
    assert 1290.0 <= feed.last_price("RELIANCE") <= 1330.0
    assert feed.candles("RELIANCE", 5)             # fallback candles flow
    # provider heals -> probe un-degrades
    feed._client = httpx.AsyncClient(transport=yahoo_transport(log, fail=False))
    feed._last_http = 0.0
    await feed.tick_once(SESSION_OPEN)
    assert feed.degraded is False


# ---------------------------------------------------------------- mt5

def mt5_transport(requests_log):
    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append((str(request.url),
                             request.headers.get("X-MT5-Auth", "")))
        if request.url.path.startswith("/tick/"):
            return httpx.Response(200, json={
                "symbol": "EURUSD", "bid": 1.1500, "ask": 1.1504,
                "last": 1.1502, "time": 1786600000})
        if request.url.path.startswith("/candles/"):
            return httpx.Response(200, json=[
                {"ts": 1786500000 + i * 86400, "o": 1.14, "h": 1.16,
                 "l": 1.13, "c": 1.15} for i in range(30)])
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_mt5_feed_mids_broker_bid_ask_and_sends_auth(tmp_path):
    log = []
    client = httpx.AsyncClient(transport=mt5_transport(log),
                               base_url="https://mt5-vps.internal:8443",
                               headers={"X-MT5-Auth": "SECRET-7"})
    feed = Mt5QuoteFeed(["EURUSD"], base_url="https://mt5-vps.internal:8443",
                        client=client, min_gap_s=0.0,
                        fallback=replay_fallback(tmp_path, "EURUSD"))
    out = await feed.tick_once(SESSION_OPEN)
    assert out == {"EURUSD": pytest.approx((1.1500 + 1.1504) / 2)}
    assert len(feed.bars_window("EURUSD")) == 30   # D1 candles from the bridge
    assert all(tok == "SECRET-7" for _, tok in log)  # auth on EVERY call


# ---------------------------------------------------------------- mux

async def test_mux_routes_per_symbol_and_merges_ticks(tmp_path):
    ylog, mlog = [], []
    yh = make_yahoo(tmp_path, ylog)
    mt = Mt5QuoteFeed(["EURUSD"], base_url="http://bridge",
                      client=httpx.AsyncClient(transport=mt5_transport(mlog),
                                               base_url="http://bridge"),
                      min_gap_s=0.0, fallback=replay_fallback(tmp_path, "EURUSD"))
    mux = FeedMux({mt: ["EURUSD"], yh: ["RELIANCE"]})
    out = await mux.tick_once(SESSION_OPEN)
    assert set(out) == {"EURUSD", "RELIANCE"}
    assert mux.last_price("RELIANCE") == 1311.8    # routed to yahoo
    assert mux.last_price("EURUSD") == pytest.approx(1.1502)
    assert mux.completed_count("RELIANCE") == 0
    assert "mux" == mux.status()["kind"]


# ---------------------------------------------------------------- factory

def test_make_feed_selects_by_env(tmp_path):
    import importlib
    run_paper = importlib.import_module("scripts.run_paper")
    from src.core.config_loader import load_config
    cfg = load_config("config/master.yaml")
    uni = run_paper.load_universe()
    clock = MarketClock(cfg.model_extra["trading_hours"])
    assert isinstance(run_paper.make_feed("replay", uni, clock, cfg),
                      ReplayQuoteFeed)
    yh = run_paper.make_feed("yahoo", uni, clock, cfg)
    assert isinstance(yh, YahooQuoteFeed) and yh.fallback is not None
    mux = run_paper.make_feed("mt5", uni, clock, cfg)
    assert isinstance(mux, FeedMux)
    # doctrine: india routed to yahoo, mt5 legs to the bridge
    assert isinstance(mux._feed_for("RELIANCE"), YahooQuoteFeed)
    assert isinstance(mux._feed_for("EURUSD"), Mt5QuoteFeed)


# ---------------------------------------------------------------- openalgo

from src.ops.live_feeds import OpenAlgoQuoteFeed


def openalgo_transport(requests_log, fail=False):
    """Fixtures shaped EXACTLY per vendored OpenAlgo docs (R1):
    docs/api/market-data/multiquotes.md + history.md."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        requests_log.append((request.url.path, body))
        if fail:
            raise httpx.ConnectError("hub down", request=request)
        if request.url.path == "/api/v1/multiquotes":
            return httpx.Response(200, json={
                "status": "success",
                "results": [{"symbol": s["symbol"], "exchange": "NSE",
                             "data": {"open": 1542.3, "high": 1571.6,
                                      "low": 1540.5, "ltp": 1569.9,
                                      "prev_close": 1539.7, "ask": 1569.9,
                                      "bid": 1569.8, "oi": 0,
                                      "volume": 14054299}}
                            for s in body["symbols"]]})
        if request.url.path == "/api/v1/history":
            return httpx.Response(200, json={
                "status": "success",
                "data": [{"timestamp": f"2026-07-{d:02d} 00:00:00+05:30",
                          "open": 1500.0, "high": 1520.0, "low": 1490.0,
                          "close": 1510.0, "volume": 1000000}
                         for d in range(1, 31)]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def make_openalgo(tmp_path, log, *, fail=False, fallback=None, max_errors=3):
    client = httpx.AsyncClient(transport=openalgo_transport(log, fail),
                               base_url="http://127.0.0.1:5000")
    return OpenAlgoQuoteFeed(
        ["RELIANCE", "TCS"], base_url="http://127.0.0.1:5000",
        apikey="OA-KEY", client=client, min_gap_s=0.0, max_errors=max_errors,
        fallback=fallback if fallback is not None else replay_fallback(tmp_path))


async def test_openalgo_batches_the_whole_universe_in_one_call(tmp_path):
    log = []
    feed = make_openalgo(tmp_path, log)
    out = await feed.tick_once(SESSION_OPEN)
    assert out == {"RELIANCE": 1569.9, "TCS": 1569.9}
    quote_calls = [b for p, b in log if p == "/api/v1/multiquotes"]
    assert len(quote_calls) == 1                    # ONE call, both symbols
    assert quote_calls[0]["apikey"] == "OA-KEY"     # documented auth field
    assert {s["symbol"] for s in quote_calls[0]["symbols"]} == {"RELIANCE", "TCS"}
    assert all(s["exchange"] == "NSE" for s in quote_calls[0]["symbols"])


async def test_openalgo_daily_history_from_the_hub(tmp_path):
    log = []
    feed = make_openalgo(tmp_path, log)
    await feed.tick_once(SESSION_OPEN)
    bars = feed.bars_window("RELIANCE", 200)
    # 30 fixture bars + today's live bar rolled on top (correct M65 behavior:
    # the live mark becomes the forming daily bar)
    assert len(bars) == 31
    assert bars[-2]["close"] == 1510.0             # last completed = fixture
    assert bars[-1]["close"] == 1569.9             # forming bar = live ltp
    hist_calls = [b for p, b in log if p == "/api/v1/history"]
    assert hist_calls and hist_calls[0]["interval"] == "D"   # documented token


async def test_openalgo_session_aware_no_polls_at_night(tmp_path):
    log = []
    clock = MarketClock(HOURS)
    client = httpx.AsyncClient(transport=openalgo_transport(log),
                               base_url="http://127.0.0.1:5000")
    feed = OpenAlgoQuoteFeed(["RELIANCE"], base_url="http://127.0.0.1:5000",
                             apikey="K", client=client, min_gap_s=0.0,
                             market_clock=clock,
                             symbol_legs={"RELIANCE": "india"},
                             fallback=replay_fallback(tmp_path))
    out = await feed.tick_once(SESSION_CLOSED)      # 21:30 IST
    assert out == {}
    assert [p for p, _ in log if p == "/api/v1/multiquotes"] == []


async def test_openalgo_degrades_through_the_full_chain_to_yahoo(tmp_path):
    """The doctrine chain: openalgo (dead) -> yahoo (alive) -> replay."""
    ylog = []
    yahoo = make_yahoo(tmp_path, ylog)              # healthy yahoo w/ replay fb
    olog = []
    feed = make_openalgo(tmp_path, olog, fail=True, fallback=yahoo,
                         max_errors=2)
    for _ in range(3):                              # strike out the hub
        await feed.tick_once(SESSION_OPEN)
    assert feed.degraded is True
    out = await feed.tick_once(SESSION_OPEN)        # served by YAHOO now
    assert out == {"RELIANCE": 1311.8}              # yahoo fixture price
    assert "DEGRADED" in feed.status()["kind"]
