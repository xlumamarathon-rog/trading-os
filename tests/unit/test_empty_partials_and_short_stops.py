"""Regression tests for two strategy-lab findings (Aug 2026):

1. ExitManager crashed (IndexError) when `partials` was configured as an
   empty list — the config schema allows it, the code assumed ≥1 partial.
   Empty partials is a legitimate exit style: breakeven ratchet + chandelier
   trail with the runner keeping full size.

2. The paper server hard-coded SELL for protective stops and closes, so a
   SHORT position's stop insta-triggered / double-sold instead of protecting.
   Stops and closes must act AGAINST the open position (short → BUY).
   Clearing a stop (MT5 `sl=0` semantics) must cancel the resting order —
   a BUY stop with trigger 0 would insta-fire on the next tick.
"""
import httpx
import pytest

from src.core.config_loader import load_config
from src.core.paper_broker import PaperBroker
from src.exits.exit_manager import ExitManager
from src.ops.paper_server import create_paper_server

CFG = load_config("config/master.yaml")
EXIT_CFG = CFG.model_extra["exit_manager"]
TREND = {"trend_state": "STRONG_TREND", "vol_regime": "NORMAL"}


class MockStopAdapter:
    def __init__(self):
        self.placed, self.modified, self.exits = [], [], []
        self.cancelled = []

    async def place_stop(self, symbol, qty, stop_price, leg, **kw):
        self.placed.append((symbol, qty, stop_price, leg))
        return f"STOP-{len(self.placed)}"

    async def modify_stop(self, stop_order_id, new_price, leg):
        self.modified.append((stop_order_id, new_price))

    async def cancel_stop(self, stop_order_id, leg):
        self.cancelled.append(stop_order_id)

    async def exit_market(self, symbol, qty, leg, **kw):
        self.exits.append((symbol, qty))


# ---------- 1. empty-partials regression ----------

async def test_empty_partials_reaches_breakeven_without_crash():
    cfg = dict(EXIT_CFG)
    cfg["partials"] = []                              # regression: IndexError here
    mgr = ExitManager(cfg, MockStopAdapter())
    pos = await mgr.attach(symbol="X", direction="buy", entry=100.0, qty=100,
                           atr=1.5, leg="india")      # k_sl 2.0 → R = 3.0
    actions = await mgr.on_bar("X", 103.5, 102.5, 103.2, TREND)   # r ≥ 1
    assert "stop_to_breakeven" in actions
    assert pos.state == "BREAKEVEN"
    assert not pos.partials_taken                     # no partials configured, none taken
    assert pos.remaining_qty == 100                   # runner keeps full size


async def test_empty_partials_still_trails_the_runner():
    cfg = dict(EXIT_CFG)
    cfg["partials"] = []
    adapter = MockStopAdapter()
    mgr = ExitManager(cfg, adapter)
    pos = await mgr.attach(symbol="X", direction="buy", entry=100.0, qty=100,
                           atr=1.5, leg="india")
    await mgr.on_bar("X", 103.5, 102.5, 103.2, TREND)             # breakeven
    actions = await mgr.on_bar("X", 110.0, 108.0, 109.5, TREND)   # big push → trail
    assert any(a.startswith("trail:") for a in actions)
    assert pos.stop > pos.entry                       # chandelier above breakeven


# ---------- 2. short-position stop handling in the paper server ----------

def make_client():
    broker = PaperBroker(costs=CFG.execution_costs.india,
                         impact=CFG.execution_costs.impact_model,
                         starting_cash=1_000_000.0)
    app = create_paper_server(broker)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://paper")
    return broker, client


async def test_short_position_gets_buy_stop_that_protects():
    broker, client = make_client()
    broker.on_tick("BTCUSD", 100.0)
    r = await client.post("/order", json={"symbol": "BTCUSD", "direction": "sell",
                                          "qty": 1, "client_order_id": "C1"})
    assert r.status_code == 200
    assert broker.positions["BTCUSD"]["qty"] == pytest.approx(-1)

    r = await client.post("/position/stop", json={"symbol": "BTCUSD",
                                                  "lots": 1, "sl": 110.0})
    assert r.status_code == 200
    resting = list(broker.open_orders.values())
    assert len(resting) == 1
    assert resting[0]["action"] == "BUY"              # regression: was SELL

    # price BELOW the stop must NOT trigger (short is winning)
    assert broker.on_tick("BTCUSD", 95.0) == []
    # adverse move THROUGH the stop triggers a BUY that flattens the short
    fills = broker.on_tick("BTCUSD", 111.0)
    assert [f.action for f in fills] == ["BUY"]
    assert broker.positions["BTCUSD"]["qty"] == pytest.approx(0)


async def test_long_position_still_gets_sell_stop():
    broker, client = make_client()
    broker.on_tick("BTCUSD", 100.0)
    await client.post("/order", json={"symbol": "BTCUSD", "direction": "buy",
                                      "qty": 1, "client_order_id": "C2"})
    await client.post("/position/stop", json={"symbol": "BTCUSD",
                                              "lots": 1, "sl": 90.0})
    resting = list(broker.open_orders.values())
    assert resting and resting[0]["action"] == "SELL"


async def test_short_close_buys_back():
    broker, client = make_client()
    broker.on_tick("EURUSD", 1.10)
    await client.post("/order", json={"symbol": "EURUSD", "direction": "sell",
                                      "qty": 1000, "client_order_id": "C3"})
    r = await client.post("/position/close", json={"symbol": "EURUSD", "lots": 1000})
    assert r.json()["ok"] is True
    assert broker.positions["EURUSD"]["qty"] == pytest.approx(0)
    assert broker.fills[-1].action == "BUY"


async def test_sl_zero_clears_stop_by_cancelling_order():
    broker, client = make_client()
    broker.on_tick("BTCUSD", 100.0)
    await client.post("/order", json={"symbol": "BTCUSD", "direction": "sell",
                                      "qty": 1, "client_order_id": "C4"})
    r = await client.post("/position/stop", json={"symbol": "BTCUSD",
                                                  "lots": 1, "sl": 110.0})
    pid = r.json()["position_id"]
    r = await client.post("/position/modify", json={"position_id": pid, "sl": 0})
    assert r.status_code == 200
    assert broker.open_orders == {}                   # cleared, nothing can insta-fire
    assert broker.on_tick("BTCUSD", 120.0) == []      # and nothing triggers


async def test_partial_close_syncs_stop_qty_no_phantom_short():
    """Regression (walk-forward sweep find): after a PARTIAL close on an MT5
    leg, the emulated position-riding SL must cover only what REMAINS. The
    old behavior kept the ORIGINAL quantity on the resting stop, so a later
    trigger oversold and flipped the book into a phantom short."""
    broker, client = make_client()
    broker.on_tick("BTCUSD", 100.0)
    await client.post("/order", json={"symbol": "BTCUSD", "direction": "buy",
                                      "qty": 2, "client_order_id": "P1"})
    r = await client.post("/position/stop", json={"symbol": "BTCUSD",
                                                  "lots": 2, "sl": 90.0})
    pid = r.json()["position_id"]

    # partial close half at market (what ExitManager's _take_partial does) ...
    await client.post("/position/close", json={"symbol": "BTCUSD", "lots": 1})
    assert broker.positions["BTCUSD"]["qty"] == pytest.approx(1)
    # ... followed by its replace_stop -> /position/modify on the same id
    await client.post("/position/modify", json={"position_id": pid, "sl": 92.0})
    assert list(broker.open_orders.values())[0]["quantity"] == pytest.approx(1)

    # adverse move triggers the stop: position must go EXACTLY flat
    broker.on_tick("BTCUSD", 80.0)
    assert broker.positions["BTCUSD"]["qty"] == pytest.approx(0)   # no phantom short
