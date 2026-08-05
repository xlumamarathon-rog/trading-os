"""Per-leg exit personalities (Aug 2026): `k_trail_by_regime_by_leg` and
`partials_by_leg` override the flat values per leg; absent legs fall back to
the global config (fully backward compatible).

Evidence base (6-month real replays + walk-forward): India rewards tight
profit-locking; crypto trends reward a full runner with no partials.
"""
from src.core.config_loader import load_config
from src.exits.exit_manager import ExitManager

CFG = load_config("config/master.yaml")
EXIT_CFG = CFG.model_extra["exit_manager"]
TREND = {"trend_state": "STRONG_TREND", "vol_regime": "NORMAL"}


class MockStopAdapter:
    def __init__(self):
        self.placed, self.modified, self.exits, self.cancelled = [], [], [], []

    async def place_stop(self, symbol, qty, stop_price, leg, **kw):
        self.placed.append((symbol, qty, stop_price, leg))
        return f"STOP-{len(self.placed)}"

    async def modify_stop(self, stop_order_id, new_price, leg):
        self.modified.append((stop_order_id, new_price))

    async def cancel_stop(self, stop_order_id, leg):
        self.cancelled.append(stop_order_id)

    async def replace_stop(self, old_id, symbol, qty, trigger, leg, **kw):
        return old_id

    async def exit_market(self, symbol, qty, leg, **kw):
        self.exits.append((symbol, qty))


def test_master_yaml_defines_per_leg_overrides():
    assert EXIT_CFG["k_trail_by_regime_by_leg"]["india"]["STRONG_TREND"] == 2.0
    assert EXIT_CFG["partials_by_leg"]["india"] == [{"at_r": 1.0, "pct": 50}]
    assert EXIT_CFG["partials_by_leg"]["mt5_crypto"] == []
    assert "mt5_forex" not in EXIT_CFG["k_trail_by_regime_by_leg"]


def test_trail_map_resolves_per_leg_with_fallback():
    mgr = ExitManager(EXIT_CFG, MockStopAdapter())
    assert mgr._trail_map("india")["STRONG_TREND"] == 2.0          # override
    assert mgr._trail_map("mt5_crypto")["STRONG_TREND"] == 3.0     # override
    assert mgr._trail_map("mt5_forex") == EXIT_CFG["k_trail_by_regime"]  # fallback


def test_k_trail_uses_leg_override():
    mgr = ExitManager(EXIT_CFG, MockStopAdapter())
    assert mgr._k_trail(TREND, None, "india", False) == 2.0
    assert mgr._k_trail(TREND, None, "mt5_forex", False) == 3.0


async def test_india_takes_50pct_partial_at_1r():
    mgr = ExitManager(EXIT_CFG, MockStopAdapter())
    pos = await mgr.attach(symbol="X", direction="buy", entry=100.0, qty=100,
                           atr=1.5, leg="india")                    # R = 3.0
    await mgr.on_bar("X", 103.5, 102.5, 103.2, TREND)               # r >= 1
    assert pos.partials_taken == [1.0]
    assert pos.remaining_qty == 50                                  # 50% booked


async def test_crypto_runner_takes_no_partials_but_trails():
    adapter = MockStopAdapter()
    mgr = ExitManager(EXIT_CFG, adapter)
    pos = await mgr.attach(symbol="BTCUSD", direction="buy", entry=60_000,
                           qty=1.0, atr=900.0, leg="mt5_crypto")    # R = 2700
    await mgr.on_bar("BTCUSD", 63_000, 62_000, 62_800, TREND)       # r >= 1
    assert pos.partials_taken == []                                 # no partial
    assert pos.remaining_qty == 1.0                                 # full runner
    assert pos.state == "BREAKEVEN"
    actions = await mgr.on_bar("BTCUSD", 70_000, 68_000, 69_500, TREND)
    assert any(a.startswith("trail:3") for a in actions)            # 3.0×ATR trail


async def test_legacy_flat_config_still_works():
    flat = {k: v for k, v in EXIT_CFG.items()
            if k not in ("k_trail_by_regime_by_leg", "partials_by_leg")}
    mgr = ExitManager(flat, MockStopAdapter())
    assert mgr._trail_map("india") == flat["k_trail_by_regime"]
    assert mgr._partials_for("mt5_crypto") == flat["partials"]
