"""Wave 3 intel tests — M10 (news two-speed+cluster), M43 (calendar), M11 (cache), M12 (bridge), M34 (regime)."""
import math
import random
import time

import pytest

from src.core.config_loader import load_config
from src.intel.event_calendar import EventCalendar
from src.intel.india_news_adapter import IndiaNewsAdapter, NewsItem
from src.intel.regime_detector import (
    RegimeDetector,
    adx,
    classify_trend,
    classify_vol,
    hurst_exponent,
    rolling_vol_series,
)
from src.intel.sentiment_cache import SentimentCache
from src.intel.verdict_bridge import parse_ai_berkshire_report, process_report
from tests.fixtures.fakes import FailingRedis, FakeRedis

CFG = load_config("config/master.yaml")


# ---------------- M10 news adapter ----------------

def fetcher(name, items):
    async def f():
        return items

    f.source_name = name
    return f


async def test_normalize_cluster_and_dissemination_count():
    raw = {"headline": "RBI hikes repo rate by 50 bps in surprise move",
           "published_at": 1000.0, "tickers": ["hdfcbank"]}
    raw2 = {"headline": "Surprise move: RBI hikes repo rate 50 bps",
            "published_at": 1010.0, "tickers": ["HDFCBANK"]}
    raw3 = {"headline": "Infosys wins mega deal from EU client",
            "published_at": 1020.0, "tickers": ["INFY"]}
    adapter = IndiaNewsAdapter(
        [fetcher("et", [raw]), fetcher("nse", [raw2]), fetcher("bse", [raw3])],
        hot_poll_seconds=CFG.model_extra["news"]["hot_poll_seconds_held_symbols"],
        cold_poll_minutes=CFG.model_extra["news"]["cold_poll_minutes_watchlist"],
    )
    items = await adapter.fetch_all()
    assert len(items) == 2  # RBI story clustered across 2 sources
    rbi = next(i for i in items if "RBI" in i.headline or "rbi" in i.headline.lower())
    assert rbi.cluster_size == 2                       # dissemination feature
    assert rbi.tickers == ["HDFCBANK"]                 # normalized upper
    assert rbi.first_seen_at > 0                       # M37 timestamp integrity


async def test_malformed_items_counted_not_crashing():
    adapter = IndiaNewsAdapter([fetcher("bad", [{"nope": 1}, {"headline": "ok", "published_at": 5.0}])],
                               hot_poll_seconds=45, cold_poll_minutes=15)
    items = await adapter.fetch_all()
    assert len(items) == 1 and adapter.malformed_count == 1


async def test_ticker_filter_and_source_down_resilience():
    async def broken():
        raise ConnectionError("feed down")

    broken.source_name = "down"
    adapter = IndiaNewsAdapter(
        [fetcher("ok", [{"headline": "TCS results beat", "published_at": 1.0, "tickers": ["TCS"]}]),
         broken],
        hot_poll_seconds=45, cold_poll_minutes=15)
    items = await adapter.fetch_all(ticker="tcs")
    assert len(items) == 1 and items[0].tickers == ["TCS"]


def test_two_speed_poll_delays():
    adapter = IndiaNewsAdapter([], hot_poll_seconds=45, cold_poll_minutes=15)
    assert adapter.poll_delay(symbol_is_held=True) == 45
    assert adapter.poll_delay(symbol_is_held=False) == 15 * 60


# ---------------- M43 event calendar ----------------

def test_lockout_window_pre_and_post():
    cal = EventCalendar(pre_lockout_min=30, post_resume_min=15)
    t_event = 10_000.0
    cal.add_event("RBI policy", t_event, affected=["HDFCBANK"])
    assert cal.lockout_active("HDFCBANK", t_event - 29 * 60) is True
    assert cal.lockout_active("HDFCBANK", t_event - 31 * 60) is False
    assert cal.lockout_active("HDFCBANK", t_event + 14 * 60) is True
    assert cal.lockout_active("HDFCBANK", t_event + 16 * 60) is False
    assert cal.lockout_active("INFY", t_event) is False   # unaffected symbol


def test_market_wide_affects_all_and_session_check():
    cal = EventCalendar(30, 15)
    cal.add_event("Union Budget", 5_000.0)  # market_wide default
    assert cal.lockout_active("ANYTHING", 5_000.0) is True
    assert cal.session_check("ANYTHING", 5_000.0) is False
    assert cal.minutes_to_next("ANYTHING", 4_000.0) == pytest.approx(1000 / 60)
    assert cal.minutes_to_next("ANYTHING", 6_000.0) is None


# ---------------- M11 sentiment cache ----------------

async def test_cache_hit_miss_ttl_and_stale():
    cache = SentimentCache(FakeRedis(), ttl_seconds=100)
    assert await cache.get_cached_signal("TCS") is None            # miss
    await cache.store_signal("TCS", "BUY", 0.72)
    sig = await cache.get_cached_signal("TCS")                     # hit
    assert sig["direction"] == "BUY" and cache.hits == 1
    # stale: rewrite computed_at into the past
    import json
    stored = json.loads(cache.redis.store["signal:TCS"])
    stored["computed_at"] = time.time() - 101
    cache.redis.store["signal:TCS"] = json.dumps(stored)
    assert await cache.get_cached_signal("TCS") is None            # stale => miss


async def test_event_driven_invalidation_and_hit_rate():
    cache = SentimentCache(FakeRedis(), ttl_seconds=1000)
    await cache.store_signal("A", "BUY", 0.6)
    await cache.store_signal("B", "SELL", 0.7)
    await cache.invalidate(["A"])
    assert await cache.get_cached_signal("A") is None
    assert (await cache.get_cached_signal("B"))["direction"] == "SELL"
    assert 0 < cache.hit_rate < 1


async def test_hot_path_fail_closed_on_redis_loss():
    cache = SentimentCache(FailingRedis(), ttl_seconds=100)
    assert await cache.get_cached_signal("TCS") is None


async def test_precompute_loop_populates():
    cache = SentimentCache(FakeRedis(), ttl_seconds=1000)

    async def compute(ticker):
        return "BUY", 0.65

    n = await cache.precompute_loop_once(["A", "B", "C"], compute)
    assert n == 3 and (await cache.get_cached_signal("B"))["confidence"] == 0.65


# ---------------- M12 verdict bridge ----------------

REPORT = """
Ticker: HDFCBANK
... deep analysis ...
Recommendation: Pass
Conviction Score: 0.82
"""


def test_parse_verdicts():
    v = parse_ai_berkshire_report(REPORT)
    assert v.ticker == "HDFCBANK" and v.recommendation == "Pass" and v.conviction == 0.82
    with pytest.raises(ValueError):
        parse_ai_berkshire_report("no structure here")


async def test_only_pass_hits_watchlist():
    rows = []

    async def insert(row):
        rows.append(row)

    assert await process_report(REPORT, insert) is not None
    assert await process_report(REPORT.replace("Pass", "Fail"), insert) is None
    assert len(rows) == 1 and rows[0]["ticker"] == "HDFCBANK"


# ---------------- M34 regime detector ----------------

def trending_series(n=400, drift=0.004, noise=0.002, seed=7):
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n):
        closes.append(closes[-1] * (1 + drift + rng.gauss(0, noise)))
    highs = [c * 1.003 for c in closes]
    lows = [c * 0.997 for c in closes]
    return highs, lows, closes


def choppy_series(n=400, noise=0.004, seed=9):
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n):
        # strong mean reversion around 100
        pull = (100.0 - closes[-1]) * 0.5 / 100.0
        closes.append(closes[-1] * (1 + pull + rng.gauss(0, noise)))
    highs = [c * 1.004 for c in closes]
    lows = [c * 0.996 for c in closes]
    return highs, lows, closes


def test_hurst_separates_trend_from_meanreversion():
    _, _, trend = trending_series()
    _, _, chop = choppy_series()
    h_trend = hurst_exponent(trend)
    h_chop = hurst_exponent(chop)
    assert h_trend > 0.55, f"trending H={h_trend}"
    assert h_chop < 0.45, f"mean-reverting H={h_chop}"
    assert h_trend > h_chop


def test_adx_higher_in_trend_than_chop():
    th, tl, tc = trending_series()
    ch, cl, cc = choppy_series()
    assert adx(th, tl, tc) > adx(ch, cl, cc)


def test_classify_vol_shock_on_jump():
    calm_history = [0.01] * 99
    assert classify_vol(0.05, calm_history)[0] == "SHOCK"
    assert classify_vol(0.001, calm_history)[0] == "LOW"
    assert classify_vol(0.01, calm_history)[0] in ("NORMAL", "HIGH")


async def test_labeled_windows_covid_shock_and_2017_grind():
    detector = RegimeDetector(FakeRedis(), vol_window=CFG.model_extra["regime_detector"]["vol_lookback_days"],
                              hurst_window=CFG.model_extra["regime_detector"]["hurst_window"],
                              adx_period=CFG.model_extra["regime_detector"]["adx_period"])
    # COVID-like: calm year then violent crash
    rng = random.Random(3)
    closes = [100.0]
    for _ in range(300):
        closes.append(closes[-1] * (1 + rng.gauss(0.0003, 0.006)))
    for _ in range(15):
        closes.append(closes[-1] * (1 + rng.gauss(-0.06, 0.05)))
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    covid = await detector.classify("COVID", highs, lows, closes)
    assert covid.vol_regime in ("HIGH", "SHOCK")

    th, tl, tc = trending_series(drift=0.002, noise=0.0015, seed=11)
    grind = await detector.classify("GRIND2017", th, tl, tc)
    assert grind.trend_state in ("STRONG_TREND", "WEAK_TREND")
    assert grind.vol_regime != "SHOCK"
    assert grind.hurst > 0.5
    # published to redis
    assert "regime:GRIND2017" in detector.redis.store
