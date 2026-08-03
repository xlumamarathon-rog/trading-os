"""MODULE 35 tests — spec acceptance: ratchet monotonicity (property), broker-resident
stop on attach, breakeven/partials, regime-adaptive chandelier, tighten triggers,
time stop, weekend policy, telemetry, restart recovery, never-widen invariant."""
import random

import pytest

from src.core.config_loader import load_config
from src.exits.exit_manager import ExitManager, StopWidenAttempt

CFG = load_config("config/master.yaml")
EXIT_CFG = CFG.model_extra["exit_manager"]

TREND = {"trend_state": "STRONG_TREND", "vol_regime": "NORMAL"}
RANGE_ = {"trend_state": "RANGE", "vol_regime": "NORMAL"}
SHOCK = {"trend_state": "RANGE", "vol_regime": "SHOCK"}


class MockStopAdapter:
    def __init__(self):
        self.placed, self.modified, self.exits = [], [], []
        self.cancelled, self.replaced = [], []

    async def place_stop(self, symbol, qty, stop_price, leg):
        self.placed.append((symbol, qty, stop_price, leg))
        return f"STOP-{len(self.placed)}"

    async def modify_stop(self, stop_order_id, new_price, leg):
        self.modified.append((stop_order_id, new_price))

    async def cancel_stop(self, stop_order_id, leg):
        self.cancelled.append(stop_order_id)

    async def replace_stop(self, old_id, symbol, qty, trigger, leg):
        self.replaced.append((old_id, qty, trigger))
        return f"STOP-R{len(self.replaced)}"

    async def exit_market(self, symbol, qty, leg):
        self.exits.append((symbol, qty))


def make_mgr():
    adapter = MockStopAdapter()
    partials, exits = [], []

    async def on_partial(sym, qty, price, at_r):
        partials.append((sym, qty, at_r))

    async def on_exit(sym, telemetry):
        exits.append((sym, telemetry))

    return ExitManager(EXIT_CFG, adapter, on_partial, on_exit), adapter, partials, exits


async def attach_long(mgr, entry=100.0, atr=1.5, leg="india", qty=100):
    return await mgr.attach(symbol="X", direction="buy", entry=entry, qty=qty, atr=atr, leg=leg)


# ---------- attach & initial stop ----------

async def test_attach_places_broker_stop_immediately_with_leg_k():
    mgr, adapter, _, _ = make_mgr()
    pos = await attach_long(mgr)                      # india k_sl = 2.0
    assert len(adapter.placed) == 1                   # broker-resident from second zero
    assert pos.stop == pytest.approx(100.0 - 2.0 * 1.5)
    mgr2, a2, _, _ = make_mgr()
    p2 = await mgr2.attach(symbol="BTCUSD", direction="buy", entry=60_000, qty=0.5,
                           atr=900.0, leg="mt5_crypto")   # crypto k_sl = 3.0 (wider)
    assert p2.stop == pytest.approx(60_000 - 3.0 * 900)


async def test_structure_stop_widens_when_further():
    mgr, _, _, _ = make_mgr()
    pos = await mgr.attach(symbol="X", direction="buy", entry=100.0, qty=10, atr=1.0,
                           leg="india", structure_stop=95.0)   # 5 > 2×ATR=2
    assert pos.stop == pytest.approx(95.0)


# ---------- the invariant ----------

async def test_never_widen_forced_attempt_raises():
    mgr, _, _, _ = make_mgr()
    await attach_long(mgr)
    with pytest.raises(StopWidenAttempt):
        await mgr.force_stop_change("X", 90.0)         # widening — refused


async def test_property_stop_monotonic_on_random_walks_long_and_short():
    for direction, seed in (("buy", 1), ("buy", 2), ("sell", 3), ("sell", 4)):
        mgr, _, _, _ = make_mgr()
        entry = 100.0
        await mgr.attach(symbol="X", direction=direction, entry=entry, qty=10,
                         atr=1.5, leg="india")
        pos = mgr.positions["X"]
        rng = random.Random(seed)
        price, prev_stop = entry, pos.stop
        for _ in range(400):
            price *= 1 + rng.gauss(0.001 if direction == "buy" else -0.001, 0.01)
            hi, lo = price * 1.005, price * 0.995
            await mgr.on_bar("X", hi, lo, price, TREND)
            if pos.state == "EXITED":
                break
            if direction == "buy":
                assert pos.stop >= prev_stop - 1e-9   # NEVER widens
            else:
                assert pos.stop <= prev_stop + 1e-9
            prev_stop = pos.stop


# ---------- lifecycle ----------

async def test_breakeven_and_partial1_at_1r_exactly_once():
    mgr, adapter, partials, _ = make_mgr()
    pos = await attach_long(mgr)                       # R = 3.0
    await mgr.on_bar("X", 103.2, 102.5, 103.1, TREND)  # ≥ +1R
    assert pos.state in ("BREAKEVEN", "TRAILING")
    assert pos.stop == pytest.approx(100.0)            # breakeven
    assert len(partials) == 1 and partials[0][2] == 1.0
    await mgr.on_bar("X", 103.3, 102.8, 103.2, TREND)  # again ≥1R — no double partial
    assert len(partials) == 1


async def test_partial2_and_trailing_at_2r():
    mgr, _, partials, _ = make_mgr()
    pos = await attach_long(mgr)                       # R=3 ⇒ +2R = 106
    await mgr.on_bar("X", 103.2, 102.5, 103.1, TREND)
    await mgr.on_bar("X", 106.5, 105.0, 106.2, TREND)
    assert pos.state == "TRAILING"
    assert [p[2] for p in partials] == [1.0, 2.0]
    assert pos.remaining_qty == pytest.approx(34)      # lot-floored: 33 + 33 sold


async def test_chandelier_follows_extreme_with_regime_k():
    mgr, _, _, _ = make_mgr()
    pos = await attach_long(mgr)
    await mgr.on_bar("X", 103.2, 102.5, 103.1, TREND)
    await mgr.on_bar("X", 106.5, 105.0, 106.2, TREND)
    await mgr.on_bar("X", 110.0, 108.0, 109.5, TREND)   # extreme=110, k=3 ⇒ stop 110-4.5
    assert pos.stop == pytest.approx(110.0 - 3.0 * 1.5)


async def test_range_regime_trails_tighter_than_trend():
    stops = {}
    for name, regime in (("trend", TREND), ("range", RANGE_)):
        mgr, _, _, _ = make_mgr()
        pos = await attach_long(mgr)
        await mgr.on_bar("X", 103.2, 102.5, 103.1, regime)
        await mgr.on_bar("X", 106.5, 105.0, 106.2, regime)
        await mgr.on_bar("X", 110.0, 108.0, 109.5, regime)
        stops[name] = pos.stop
    assert stops["range"] > stops["trend"]              # tighter = closer to price


async def test_shock_regime_tightens_hard():
    mgr, _, _, _ = make_mgr()
    pos = await attach_long(mgr)
    await mgr.on_bar("X", 103.2, 102.5, 103.1, TREND)
    await mgr.on_bar("X", 110.0, 108.0, 109.5, SHOCK)   # k = 0.75
    assert pos.stop == pytest.approx(110.0 - 0.75 * 1.5)


async def test_event_window_halves_k():
    mgr, _, _, _ = make_mgr()
    pos = await attach_long(mgr)
    await mgr.on_bar("X", 103.2, 102.5, 103.1, TREND)
    await mgr.on_bar("X", 110.0, 108.0, 109.5, TREND, event_minutes=20.0)  # ≤30 ⇒ k=1.5
    assert pos.stop == pytest.approx(110.0 - 1.5 * 1.5)


async def test_crypto_weekend_tighten_and_flatten_policies():
    mgr, _, _, _ = make_mgr()
    pos = await mgr.attach(symbol="X", direction="buy", entry=60_000, qty=0.5,
                           atr=900.0, leg="mt5_crypto")
    await mgr.on_bar("X", 63_000, 61_000, 62_500, TREND)
    await mgr.on_bar("X", 66_000, 64_000, 65_500, TREND, crypto_weekend=True)  # k=1.5
    assert pos.stop == pytest.approx(66_000 - 1.5 * 900)

    flatten_cfg = dict(EXIT_CFG)
    flatten_cfg["crypto_weekend_policy"] = "flatten"
    mgr2 = ExitManager(flatten_cfg, MockStopAdapter())
    pos2 = await mgr2.attach(symbol="Y", direction="buy", entry=60_000, qty=0.5,
                             atr=900.0, leg="mt5_crypto")
    actions = await mgr2.on_bar("Y", 60_500, 59_800, 60_200, TREND, crypto_weekend=True)
    assert actions == ["exit:crypto_weekend_flatten"] and pos2.state == "EXITED"


async def test_time_stop_after_no_progress():
    mgr, _, _, exits = make_mgr()
    pos = await attach_long(mgr)                        # india max 20 bars
    for _ in range(21):
        await mgr.on_bar("X", 100.5, 99.8, 100.1, RANGE_)   # never a new extreme
        if pos.state == "EXITED":
            break
    assert pos.state == "EXITED" and pos.telemetry.exit_reason == "time_stop_no_progress"


async def test_stop_hit_exit_with_mfe_telemetry():
    mgr, adapter, _, exits = make_mgr()
    pos = await attach_long(mgr)                        # stop 97, R=3
    await mgr.on_bar("X", 103.2, 102.5, 103.1, TREND)   # +1R, breakeven, extreme 103.2
    actions = await mgr.on_bar("X", 100.5, 99.5, 99.8, TREND)   # low 99.5 ≤ stop 100
    assert actions == ["exit:stop_hit"]
    t = pos.telemetry
    assert t.exit_reason == "stop_hit" and t.exit_price == pytest.approx(100.0)
    assert t.mfe_r > 1.0 and 0 <= t.mfe_captured_pct <= 100
    # partials produced exits; stop_hit itself must NOT add a market sell (no double-exit)
    partial_sells = len([p for p in pos.partials_taken])
    assert len(adapter.exits) == partial_sells
    assert exits


async def test_min_ratchet_step_batches_modifies():
    mgr, adapter, _, _ = make_mgr()
    pos = await attach_long(mgr)
    await mgr.on_bar("X", 103.2, 102.5, 103.1, TREND)
    await mgr.on_bar("X", 106.5, 105.0, 106.2, TREND)
    base_modifies = len(adapter.modified)
    # micro new highs: +0.01 each — improvement < 0.25×ATR(=0.375) ⇒ no modify spam
    for i in range(10):
        await mgr.on_bar("X", 106.5 + 0.01 * (i + 1), 105.5, 106.3, TREND)
    assert len(adapter.modified) <= base_modifies + 1


async def test_restart_recovery_snapshot_roundtrip():
    mgr, adapter, _, _ = make_mgr()
    pos = await attach_long(mgr)
    await mgr.on_bar("X", 103.2, 102.5, 103.1, TREND)
    snap = mgr.to_snapshot()
    revived = ExitManager.from_snapshot(snap, EXIT_CFG, adapter)
    rpos = revived.positions["X"]
    assert rpos.stop == pos.stop and rpos.state == pos.state
    assert rpos.stop_order_id == pos.stop_order_id
    assert revived.naked_positions() == []


async def test_short_side_symmetry():
    mgr, _, _, _ = make_mgr()
    pos = await mgr.attach(symbol="X", direction="sell", entry=100.0, qty=10,
                           atr=1.5, leg="india")
    assert pos.stop == pytest.approx(103.0)
    await mgr.on_bar("X", 97.5, 96.7, 96.9, TREND)      # ≥ +1R for short
    assert pos.stop == pytest.approx(100.0)             # breakeven downward
    await mgr.on_bar("X", 93.5, 92.0, 92.5, TREND)      # extreme low 92 ⇒ trail above
    assert pos.stop <= 100.0 and pos.stop == pytest.approx(92.0 + 3.0 * 1.5)
