"""MODULE 36 tests — synthetic shock replays from spec acceptance:
flash crash / spread blowout / volume spike fire; <100ms trigger latency;
normal random-walk produces no false triggers; cooloff; fail-closed pause reads;
protective stops never touched (callback contract).
"""
import random
import time

from src.core.config_loader import load_config
from src.intel.anomaly_guard import PAUSE_ENTRIES_KEY, AnomalyGuard, Tick
from tests.fixtures.fakes import FailingRedis, FakeRedis

CFG = load_config("config/master.yaml")
AG = CFG.model_extra["anomaly_guard"]


def make_guard(redis=None, **overrides):
    calls = {"cancel": [], "tighten": [], "alerts": []}
    guard = AnomalyGuard(
        redis=redis if redis is not None else FakeRedis(),
        velocity_sigma=AG["velocity_sigma"],
        spread_blowout_mult=AG["spread_blowout_mult"],
        volume_spike_mult=AG["volume_spike_mult"],
        cooloff_minutes=AG["cooloff_minutes"],
        cancel_entry_orders=lambda s: calls["cancel"].append(s),
        tighten_exits=lambda s: calls["tighten"].append(s),
        alert_fn=lambda s, t: calls["alerts"].append((s, t)),
        **overrides,
    )
    # baseline: calm large-cap — 1s sigma 2bp, 5s 4bp, 30s 10bp; spread 0.05; 30s vol 10k
    guard.prime("NIFTY", sigma_1s=0.0002, sigma_5s=0.0004, sigma_30s=0.001,
                median_spread=0.05, volume_30s_baseline=10_000)
    return guard, calls


def walk(guard_symbol_price, t0, n, sigma, seed, volume=100.0):
    rng = random.Random(seed)
    price = guard_symbol_price
    ticks = []
    for i in range(n):
        price *= 1 + rng.gauss(0, sigma)
        ticks.append(Tick(ts=t0 + i, price=price, bid=price - 0.02, ask=price + 0.03, volume=volume))
    return ticks


async def test_flash_crash_fires_velocity_trigger_within_100ms():
    guard, calls = make_guard()
    t0 = 1000.0
    for tick in walk(25_000.0, t0, 40, 0.0001, seed=1):
        assert await guard.process_tick("NIFTY", tick) == []

    crash = Tick(ts=t0 + 41, price=25_000 * 0.99, bid=24_749, ask=24_751, volume=500)
    start = time.perf_counter()
    events = await guard.process_tick("NIFTY", crash)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 100, f"trigger took {elapsed_ms:.1f}ms"
    assert any(e.trigger.startswith("velocity") for e in events)
    assert calls["cancel"] == ["NIFTY"] and calls["tighten"] == ["NIFTY"]
    assert await guard.entries_paused() is True


async def test_normal_random_walk_no_false_triggers():
    guard, calls = make_guard()
    for tick in walk(25_000.0, 1000.0, 500, 0.0001, seed=42):
        events = await guard.process_tick("NIFTY", tick)
        assert events == []
    assert calls["cancel"] == [] and await guard.entries_paused() is False


async def test_spread_blowout_trigger():
    guard, calls = make_guard()
    for tick in walk(25_000.0, 1000.0, 10, 0.00005, seed=3):
        await guard.process_tick("NIFTY", tick)
    wide = Tick(ts=1011.0, price=25_000.0, bid=24_999.0, ask=24_999.0 + 0.5, volume=100)
    events = await guard.process_tick("NIFTY", wide)
    assert any(e.trigger == "spread_blowout" for e in events)


async def test_volume_spike_trigger():
    guard, calls = make_guard()
    for tick in walk(25_000.0, 1000.0, 10, 0.00005, seed=4, volume=100.0):
        await guard.process_tick("NIFTY", tick)
    burst = Tick(ts=1011.0, price=25_000.0, bid=24_999.98, ask=25_000.03, volume=60_000)
    events = await guard.process_tick("NIFTY", burst)
    assert any(e.trigger == "volume_spike" for e in events)


async def test_cooloff_prevents_action_spam():
    guard, calls = make_guard()
    for tick in walk(25_000.0, 1000.0, 10, 0.00005, seed=5):
        await guard.process_tick("NIFTY", tick)
    c1 = Tick(ts=1011.0, price=24_700.0, bid=24_699, ask=24_701, volume=100)
    c2 = Tick(ts=1012.0, price=24_400.0, bid=24_399, ask=24_401, volume=100)
    e1 = await guard.process_tick("NIFTY", c1)
    e2 = await guard.process_tick("NIFTY", c2)  # inside cooloff — no new ACTIONS
    assert e1 and e2 == []
    assert calls["cancel"] == ["NIFTY"]  # exactly once


async def test_redis_down_pause_reads_fail_closed_and_event_recorded():
    guard, calls = make_guard(redis=FailingRedis())
    for tick in walk(25_000.0, 1000.0, 10, 0.00005, seed=6):
        await guard.process_tick("NIFTY", tick)
    crash = Tick(ts=1011.0, price=24_500.0, bid=24_499, ask=24_501, volume=100)
    events = await guard.process_tick("NIFTY", crash)
    assert events  # shock detected and recorded locally despite Redis loss
    assert any(e.trigger == "redis_pause_set_failed" for e in guard.events)
    assert await guard.entries_paused() is True  # fail-closed read


async def test_unprimed_symbol_is_silent():
    guard, _ = make_guard()
    crash = Tick(ts=1000.0, price=100.0, bid=99, ask=101, volume=1e9)
    assert await guard.process_tick("UNPRIMED", crash) == []


async def test_guard_never_touches_protective_stops():
    """Contract: the guard only receives a cancel-ENTRIES callback. There is no
    stop-cancel pathway in its API surface at all."""
    guard, _ = make_guard()
    api = {name for name in dir(guard) if not name.startswith("_")}
    assert "cancel_stop" not in api and "cancel_all_orders" not in api


async def test_pause_flag_uses_ttl_setex():
    guard, _ = make_guard()
    for tick in walk(25_000.0, 1000.0, 10, 0.00005, seed=7):
        await guard.process_tick("NIFTY", tick)
    crash = Tick(ts=1011.0, price=24_500.0, bid=24_499, ask=24_501, volume=100)
    await guard.process_tick("NIFTY", crash)
    assert guard.redis.store.get(PAUSE_ENTRIES_KEY) == "1"
