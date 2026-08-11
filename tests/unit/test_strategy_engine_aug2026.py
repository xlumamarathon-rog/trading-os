"""MODULE 65 — strategy engine. Invariants:

  - every sleeve boots DISABLED; nothing fires until an operator enables it
  - entries go through the injected router (the real door in production) —
    a router rejection increments the sleeve's rejection count, no attach
  - one open position per symbol; the owning sleeve is attributed exactly
  - a throwing sleeve is disabled and reported, never retried silently
  - /strategies read is viewer+; /strategies/toggle is operator-only with
    the typed ENABLE confirmation on the risk-increasing direction only
"""
import time
from pathlib import Path

import httpx
import pytest

from src.core.kill_switch import KillSwitch
from src.ops.cockpit_gateway import create_gateway
from src.ops.persistence import JsonlAuditLog
from src.ops.strategy_engine import StrategyEngine

# ---------------------------------------------------------------- fakes


class FakeFeed:
    """Deterministic bar-clock: 60 up-trending bars, price 100 -> ~130."""

    def __init__(self):
        self.completed = {"SYM": 0}
        self.bars = []
        px = 100.0
        for _ in range(60):
            o, c = px, px * 1.005
            self.bars.append({"open": o, "high": c * 1.002,
                              "low": o * 0.998, "close": c})
            px = c
        self.last = px

    def completed_count(self, sym):
        return self.completed.get(sym, 0)

    def bars_window(self, sym, n=200):
        return self.bars[-n:]

    def last_price(self, sym):
        return self.last


class FakeRecord:
    filled_qty = 10.0
    avg_fill_price = 130.0


class FakeResult:
    def __init__(self, accepted, reason="ok"):
        self.accepted = accepted
        self.reason = reason
        self.record = FakeRecord() if accepted else None


class FakeRouter:
    def __init__(self, accept=True, reason="ok"):
        self.accept, self.reason, self.requests = accept, reason, []

    async def route_order(self, req):
        self.requests.append(req)
        return FakeResult(self.accept, self.reason)


class FakePos:
    def __init__(self, direction="buy"):
        self.state = "RISK_ON"
        self.direction = direction


class FakeExitMgr:
    def __init__(self):
        self.positions = {}
        self.attached = []

    async def attach(self, **kw):
        self.attached.append(kw)
        self.positions[kw["symbol"]] = FakePos(kw["direction"])


UNIVERSE = {"SYM": {"leg": "india", "lot": 1}}


def always_buy(bars, i, regime):
    return "buy"


def never_signal(bars, i, regime):
    return None


def explode(bars, i, regime):
    raise RuntimeError("bad math in sleeve")


def make_engine(router=None, signals=None):
    router = router or FakeRouter()
    mgr = FakeExitMgr()
    notes = []
    eng = StrategyEngine(router=router, exit_mgr=mgr, feed=FakeFeed(),
                         universe=UNIVERSE, note_fn=notes.append,
                         signals=signals or {"auto_buy": always_buy,
                                             "quiet": never_signal})
    return eng, router, mgr, notes


# ---------------------------------------------------------------- engine

async def test_all_sleeves_boot_disabled_and_nothing_fires():
    eng, router, mgr, _ = make_engine()
    assert all(not s["enabled"] for s in eng.sleeves.values())
    eng.feed.completed["SYM"] = 1                    # a bar closes
    await eng.on_tick()
    assert router.requests == [] and mgr.attached == []


async def test_enabled_sleeve_enters_through_the_router():
    eng, router, mgr, notes = make_engine()
    eng.set_enabled("auto_buy", True, "beef")
    eng.feed.completed["SYM"] = 1
    await eng.on_tick()
    assert len(router.requests) == 1
    req = router.requests[0]
    assert req.symbol == "SYM" and req.direction == "buy"
    assert req.algo_id == "ALGO-PAPER-1"             # india leg tagged (SEBI)
    assert req.stop < req.entry                       # protective stop below
    assert mgr.attached and mgr.attached[0]["symbol"] == "SYM"
    assert eng.sleeves["auto_buy"]["entries"] == 1
    assert eng.sleeve_for("SYM") == "auto_buy"
    assert any("AUTO auto_buy" in n for n in notes)


async def test_same_bar_never_evaluated_twice():
    eng, router, _, _ = make_engine()
    eng.set_enabled("auto_buy", True)
    eng.feed.completed["SYM"] = 1
    await eng.on_tick()
    await eng.on_tick()                              # no new bar completed
    assert len(router.requests) == 1


async def test_open_symbol_is_owned_until_exit():
    eng, router, mgr, _ = make_engine()
    eng.set_enabled("auto_buy", True)
    eng.feed.completed["SYM"] = 1
    await eng.on_tick()
    eng.feed.completed["SYM"] = 2                    # next bar closes
    await eng.on_tick()
    assert len(router.requests) == 1                 # still just one entry
    # exit releases the symbol
    mgr.positions["SYM"].state = "EXITED"
    eng.record_exit("SYM", 1.5)
    eng.feed.completed["SYM"] = 3
    await eng.on_tick()
    assert len(router.requests) == 2                 # re-entry allowed


async def test_router_rejection_counts_no_attach():
    eng, router, mgr, _ = make_engine(
        router=FakeRouter(accept=False, reason="precheck_failed:session_failed"))
    eng.set_enabled("auto_buy", True)
    eng.feed.completed["SYM"] = 1
    await eng.on_tick()
    assert mgr.attached == []
    assert eng.sleeves["auto_buy"]["rejections"] == 1
    assert eng.sleeves["auto_buy"]["entries"] == 0


async def test_throwing_sleeve_is_disabled_and_reported():
    eng, router, _, notes = make_engine(signals={"boom": explode})
    eng.set_enabled("boom", True)
    eng.feed.completed["SYM"] = 1
    await eng.on_tick()
    assert eng.sleeves["boom"]["enabled"] is False
    assert "bad math" in eng.sleeves["boom"]["error"]
    assert any("DISABLED after error" in n for n in notes)
    assert router.requests == []


async def test_exit_attribution_updates_the_ledger():
    eng, _, mgr, _ = make_engine()
    eng.set_enabled("auto_buy", True)
    eng.feed.completed["SYM"] = 1
    await eng.on_tick()
    eng.record_exit("SYM", 2.0)
    st = eng.sleeves["auto_buy"]
    assert st["closed"] == 1 and st["wins"] == 1 and st["realized_r"] == 2.0
    eng.record_exit("SYM", -1.0)                     # symbol already released
    assert st["closed"] == 1                          # no double count


def test_unknown_sleeve_fails_loud():
    eng, _, _, _ = make_engine()
    with pytest.raises(KeyError, match="unknown sleeve"):
        eng.set_enabled("nope", True)


def test_status_shape_for_the_cockpit():
    eng, _, _, _ = make_engine()
    rows = eng.status()["sleeves"]
    assert {r["name"] for r in rows} == {"auto_buy", "quiet"}
    assert all(set(r) >= {"name", "enabled", "entries", "rejections",
                          "closed", "wins", "realized_r", "open_positions"}
               for r in rows)


def test_default_registry_is_the_real_one():
    """Wired without an explicit signals dict, the engine trades the SAME
    registry the certified backtests use — no shadow strategy list."""
    from src.strategies.signals import SIGNALS
    eng = StrategyEngine(router=FakeRouter(), exit_mgr=FakeExitMgr(),
                         feed=FakeFeed(), universe=UNIVERSE)
    assert set(eng.sleeves) == set(SIGNALS)


# ---------------------------------------------------------------- gateway

class MemRedis:
    def __init__(self):
        self.store, self._exp = {}, {}

    async def get(self, k):
        exp = self._exp.get(k)
        if exp is not None and time.time() > exp:
            self.store.pop(k, None); self._exp.pop(k, None)
        return self.store.get(k)

    async def set(self, k, v): self.store[k] = v; self._exp.pop(k, None)
    async def setex(self, k, ttl, v): self.store[k] = v; self._exp[k] = time.time() + ttl
    async def delete(self, k): self.store.pop(k, None); self._exp.pop(k, None)


def make_app(tmp_path: Path, engine):
    ks = KillSwitch(redis=MemRedis(), brokers={},
                    sentinel_path=tmp_path / "halt.sentinel",
                    unlock_phrase="GO", auto_trigger_daily_loss_pct=0.03,
                    auto_trigger_var_breach=True, max_var_daily=0.02)

    async def snapshot():
        return {}

    audit = JsonlAuditLog(tmp_path / "a.jsonl")
    app = create_gateway(tokens={"VTOK1234": "viewer", "OTOK5678": "operator"},
                         kill_switch=ks, audit_log=audit, snapshot_fn=snapshot,
                         ui_dir=None, strategy_engine=engine)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://gw"), audit


V = {"Authorization": "Bearer VTOK1234"}
O = {"Authorization": "Bearer OTOK5678"}


async def test_strategies_read_and_toggle_rbac(tmp_path):
    eng, _, _, _ = make_engine()
    c, audit = make_app(tmp_path, eng)
    body = (await c.get("/strategies", headers=V)).json()
    assert {s["name"] for s in body["sleeves"]} == {"auto_buy", "quiet"}
    # viewer cannot toggle
    r = await c.post("/strategies/toggle", headers=V,
                     json={"sleeve": "auto_buy", "enabled": True,
                           "confirm": "ENABLE auto_buy"})
    assert r.status_code == 403
    # enabling needs the typed per-sleeve phrase
    r = await c.post("/strategies/toggle", headers=O,
                     json={"sleeve": "auto_buy", "enabled": True,
                           "confirm": "yes"})
    assert r.status_code == 400 and "ENABLE auto_buy" in r.json()["detail"]
    r = await c.post("/strategies/toggle", headers=O,
                     json={"sleeve": "auto_buy", "enabled": True,
                           "confirm": "ENABLE auto_buy"})
    assert r.status_code == 200 and eng.sleeves["auto_buy"]["enabled"] is True
    # disabling is the airbag: no phrase needed
    r = await c.post("/strategies/toggle", headers=O,
                     json={"sleeve": "auto_buy", "enabled": False})
    assert r.status_code == 200 and eng.sleeves["auto_buy"]["enabled"] is False
    # unknown sleeve 404s; both toggles audited
    r = await c.post("/strategies/toggle", headers=O,
                     json={"sleeve": "ghost", "enabled": False})
    assert r.status_code == 404
    toggles = [row for row in audit.rows if row.get("action") == "sleeve_toggle"]
    assert len(toggles) == 2


async def test_strategies_unwired_returns_empty(tmp_path):
    c, _ = make_app(tmp_path, None)
    assert (await c.get("/strategies", headers=V)).json() == {"sleeves": []}
    r = await c.post("/strategies/toggle", headers=O,
                     json={"sleeve": "x", "enabled": False})
    assert r.status_code == 501


# ------------------------------------------ real assembly, real data

async def test_assembly_auto_trades_exactly_when_the_signal_says(tmp_path,
                                                                 monkeypatch):
    """Boot the REAL run_paper assembly, enable tsmom via the endpoint, close
    one BTCUSD bar on the real feed, and assert the engine did EXACTLY what
    the certified signal function says it should on that data — a mirror
    assertion, not a hope."""
    monkeypatch.setenv("COCKPIT_OPERATOR_TOKEN", "OP-T")
    monkeypatch.setenv("COCKPIT_VIEWER_TOKEN", "VW-T")
    import importlib
    run_paper = importlib.import_module("scripts.run_paper")
    app, _feed_loop, (op, _vw) = await run_paper.assemble(tmp_path)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                          base_url="http://gw")
    OPH = {"Authorization": f"Bearer {op}"}

    body = (await c.get("/strategies", headers=OPH)).json()
    from src.strategies.signals import SIGNALS
    assert {s["name"] for s in body["sleeves"]} == set(SIGNALS)
    assert all(not s["enabled"] for s in body["sleeves"])   # safe boot

    r = await c.post("/strategies/toggle", headers=OPH,
                     json={"sleeve": "tsmom", "enabled": True,
                           "confirm": "ENABLE tsmom"})
    assert r.status_code == 200

    engine, feed = app.state.engine, app.state.feed
    broker = app.state.broker
    for _ in range(7):                               # complete >= one bar,
        for sym, px in feed.tick_once().items():     # mirroring the feed loop
            broker.on_tick(sym, px)                  # (marks prime the broker)
    # compute the EXPECTED verdict from the same signal contract the
    # engine must apply on BTCUSD (crypto: session never blocks)
    from src.ops.strategy_engine import atr14, real_regime
    px = feed.last_price("BTCUSD")
    bars = feed.bars_window("BTCUSD", 200) + [
        {"open": px, "high": px, "low": px, "close": px}]
    i = len(bars) - 1
    expected = SIGNALS["tsmom"](bars, i, real_regime(bars, i - 1))

    await engine.on_tick()
    st = (await c.get("/strategies", headers=OPH)).json()
    tsmom = next(s for s in st["sleeves"] if s["name"] == "tsmom")
    positions = (await c.get("/state", headers=OPH)).json()["positions"]
    got_btc = any(p["symbol"] == "BTCUSD" for p in positions)
    if expected in ("buy", "sell"):
        assert tsmom["entries"] >= 1, tsmom
        assert got_btc
        assert engine.sleeve_for("BTCUSD") == "tsmom"
    else:
        assert not got_btc and tsmom["entries"] == 0
