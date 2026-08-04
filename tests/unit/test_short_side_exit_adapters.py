"""Short support through the exit adapters (Aug 2026).

Protective stops and exits must act AGAINST the position side:
long ("buy") → SELL orders · short ("sell") → BUY orders.
`direction` defaults to "buy" everywhere, so every pre-existing caller keeps
its exact long-side behavior — verified by the untouched long-side tests.
"""
import pytest

from src.core.config_loader import load_config
from src.exits.adapters.composite import CompositeStopAdapter
from src.exits.adapters.india_stops import IndiaStopAdapter, closing_action
from src.exits.exit_manager import ExitManager

CFG = load_config("config/master.yaml")
EXIT_CFG = CFG.model_extra["exit_manager"]
TREND = {"trend_state": "STRONG_TREND", "vol_regime": "NORMAL"}


def test_closing_action_mapping():
    assert closing_action("buy") == "SELL"
    assert closing_action("sell") == "BUY"


class FakeResp:
    status_code = 200
    def raise_for_status(self): pass
    def json(self): return {"orderid": "OID-1", "position_id": "PID-1"}


class SpyClient:
    def __init__(self):
        self.posts = []

    async def post(self, path, json=None):
        self.posts.append((path, json))
        return FakeResp()


async def test_india_short_stop_places_buy_slm():
    client = SpyClient()
    a = IndiaStopAdapter(client, apikey="K", algo_id="ALGO-1")
    await a.place_stop("RELIANCE", 10, 105.0, "india", direction="sell")
    path, payload = client.posts[-1]
    assert path == "/api/v1/placeorder"
    assert payload["action"] == "BUY"                 # protects a SHORT
    assert payload["pricetype"] == "SL-M"


async def test_india_long_stop_unchanged_sell_slm():
    client = SpyClient()
    a = IndiaStopAdapter(client, apikey="K", algo_id="ALGO-1")
    await a.place_stop("RELIANCE", 10, 95.0, "india")  # default direction="buy"
    _, payload = client.posts[-1]
    assert payload["action"] == "SELL"                 # legacy behavior intact


async def test_india_short_exit_market_buys_back():
    client = SpyClient()
    a = IndiaStopAdapter(client, apikey="K", algo_id="ALGO-1")
    await a.exit_market("RELIANCE", 10, "india", direction="sell")
    _, payload = client.posts[-1]
    assert payload["action"] == "BUY" and payload["pricetype"] == "MARKET"


async def test_india_short_replace_stop_reissues_buy():
    client = SpyClient()
    a = IndiaStopAdapter(client, apikey="K", algo_id="ALGO-1")
    await a.replace_stop("OLD-1", "RELIANCE", 5, 108.0, "india", direction="sell")
    paths = [p for p, _ in client.posts]
    assert "/api/v1/cancelorder" in paths
    _, payload = client.posts[-1]
    assert payload["action"] == "BUY"


class RecordingAdapter:
    """Composite-shaped spy recording the direction each call receives."""
    def __init__(self):
        self.calls = []

    async def place_stop(self, symbol, qty, stop_price, leg, *, direction="buy"):
        self.calls.append(("place_stop", direction))
        return "S1"

    async def modify_stop(self, stop_order_id, new_price, leg):
        self.calls.append(("modify_stop", None))

    async def cancel_stop(self, stop_order_id, leg):
        self.calls.append(("cancel_stop", None))

    async def replace_stop(self, old_id, symbol, qty, trigger, leg, *, direction="buy"):
        self.calls.append(("replace_stop", direction))
        return "S1"

    async def exit_market(self, symbol, qty, leg, *, direction="buy"):
        self.calls.append(("exit_market", direction))


async def test_exit_manager_threads_direction_for_short_position():
    adapter = RecordingAdapter()
    mgr = ExitManager(dict(EXIT_CFG), adapter)
    pos = await mgr.attach(symbol="X", direction="sell", entry=100.0, qty=100,
                           atr=1.5, leg="india")      # short: stop ABOVE entry
    assert pos.stop == pytest.approx(100.0 + 2.0 * 1.5)
    assert ("place_stop", "sell") in adapter.calls

    # profitable move DOWN for a short → breakeven + partial 1 fire
    await mgr.on_bar("X", 96.5, 95.5, 95.8, TREND)    # r ≥ 1 for the short
    assert ("exit_market", "sell") in adapter.calls   # partial buys back
    assert ("replace_stop", "sell") in adapter.calls  # stop re-placed as BUY side

    # active exit (time stop) also carries the short side
    for _ in range(int(EXIT_CFG["max_bars_no_progress"]["india"]) + 1):
        await mgr.on_bar("X", 95.9, 95.7, 95.8, TREND)
        if mgr.positions["X"].state == "EXITED":
            break
    assert mgr.positions["X"].state == "EXITED"
    exits = [d for (c, d) in adapter.calls if c == "exit_market"]
    assert all(d == "sell" for d in exits)


async def test_exit_manager_long_still_sends_buy_side_default():
    adapter = RecordingAdapter()
    mgr = ExitManager(dict(EXIT_CFG), adapter)
    await mgr.attach(symbol="Y", direction="buy", entry=100.0, qty=100,
                     atr=1.5, leg="india")
    assert ("place_stop", "buy") in adapter.calls


async def test_composite_routes_direction_to_india_leg():
    class Spy:
        def __init__(self): self.kw = None
        async def place_stop(self, symbol, qty, stop_price, leg, *, direction="buy"):
            self.kw = direction; return "S"
        async def exit_market(self, symbol, qty, leg, *, direction="buy"):
            self.kw = direction
    india, mt5 = Spy(), Spy()
    comp = CompositeStopAdapter(india_adapter=india, mt5_adapter=mt5)
    await comp.place_stop("RELIANCE", 1, 99.0, "india", direction="sell")
    assert india.kw == "sell" and mt5.kw is None
    await comp.exit_market("BTCUSD", 1, "mt5_crypto", direction="sell")
    assert mt5.kw == "sell"
