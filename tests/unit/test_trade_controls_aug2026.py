"""MODULES 62-64 — trade controls + research lab + quote feed + the
runnable paper assembly. Invariants:

  - manual_exit closes ONE position through the real exit path (stop
    cancelled, market-out), fail-loud on unknown symbols
  - /control/close_position and /control/order: operator-only, per-symbol
    typed confirmation, audited; a ticket goes through the FULL router
  - the replay feed advances ONLY open markets and never invents prices
    outside a real bar's range
  - research lab refuses non-allowlisted strategies/datasets and never
    runs two backtests at once
"""
import asyncio
import datetime as dt
import time
from pathlib import Path

import httpx
import pytest

from src.core.kill_switch import KillSwitch
from src.exits.exit_manager import ExitManager
from src.ops.cockpit_gateway import create_gateway
from src.ops.market_clock import MarketClock
from src.ops.persistence import JsonlAuditLog
from src.ops.quote_feed import ReplayQuoteFeed
from src.ops.research_lab import ResearchLab

UTC = dt.timezone.utc


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


class SpyAdapter:
    """Records the exit path calls manual_exit must make."""

    def __init__(self):
        self.cancelled, self.exits, self.placed = [], [], []

    async def place_stop(self, symbol, qty, stop, leg, direction="buy"):
        self.placed.append((symbol, qty, stop, leg))
        return f"stop-{symbol}"

    async def cancel_stop(self, order_id, leg):
        self.cancelled.append((order_id, leg))

    async def exit_market(self, symbol, qty, leg, direction="buy"):
        self.exits.append((symbol, qty, leg))

    async def modify_stop(self, order_id, new_stop, leg):
        pass


from src.core.config_loader import load_config

EXIT_CFG = load_config("config/master.yaml").model_extra["exit_manager"]


async def make_manager():
    adapter = SpyAdapter()
    mgr = ExitManager(EXIT_CFG, adapter)
    await mgr.attach(symbol="RELIANCE", direction="buy", entry=100.0,
                     qty=10, atr=2.0, leg="india", lot_size=1.0)
    return mgr, adapter


# ---------------------------------------------------------- manual exit

async def test_manual_exit_closes_via_real_exit_path():
    mgr, adapter = await make_manager()
    out = await mgr.manual_exit("RELIANCE", 106.0)
    assert out["reason"] == "manual_close"
    assert adapter.cancelled, "resting stop must be cancelled FIRST"
    assert adapter.exits == [("RELIANCE", 10, "india")]
    assert mgr.positions["RELIANCE"].state == "EXITED"
    assert mgr.positions["RELIANCE"].telemetry.exit_reason == "manual_close"


async def test_manual_exit_unknown_symbol_fails_loud():
    mgr, _ = await make_manager()
    with pytest.raises(KeyError, match="TCS"):
        await mgr.manual_exit("TCS", 100.0)


async def test_manual_exit_twice_fails_loud():
    mgr, _ = await make_manager()
    await mgr.manual_exit("RELIANCE", 106.0)
    with pytest.raises(KeyError):
        await mgr.manual_exit("RELIANCE", 106.0)


# ---------------------------------------------------------- gateway

def make_app(tmp_path: Path, **extra):
    ks = KillSwitch(redis=MemRedis(), brokers={},
                    sentinel_path=tmp_path / "halt.sentinel",
                    unlock_phrase="GO", auto_trigger_daily_loss_pct=0.03,
                    auto_trigger_var_breach=True, max_var_daily=0.02)

    async def snapshot():
        return {}

    audit = JsonlAuditLog(tmp_path / "a.jsonl")
    app = create_gateway(tokens={"VTOK1234": "viewer", "OTOK5678": "operator"},
                         kill_switch=ks, audit_log=audit, snapshot_fn=snapshot,
                         ui_dir=None, **extra)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://gw")
    return client, audit


V = {"Authorization": "Bearer VTOK1234"}
O = {"Authorization": "Bearer OTOK5678"}


async def test_close_position_confirmation_is_per_symbol(tmp_path):
    calls = []

    async def close(symbol, reason):
        calls.append(symbol)
        return {"symbol": symbol}

    c, audit = make_app(tmp_path, close_position_fn=close)
    # wrong phrase (generic yes) refused
    r = await c.post("/control/close_position", headers=O,
                     json={"symbol": "RELIANCE", "confirm": "yes"})
    assert r.status_code == 400 and "CLOSE RELIANCE" in r.json()["detail"]
    # phrase for a DIFFERENT symbol refused — cannot fat-finger the wrong row
    r = await c.post("/control/close_position", headers=O,
                     json={"symbol": "TCS", "confirm": "CLOSE RELIANCE"})
    assert r.status_code == 400
    assert calls == []
    # exact per-symbol phrase accepted + audited
    r = await c.post("/control/close_position", headers=O,
                     json={"symbol": "RELIANCE", "confirm": "CLOSE RELIANCE"})
    assert r.status_code == 200 and calls == ["RELIANCE"]
    assert any(row.get("action") == "close_position" for row in audit.rows)


async def test_close_position_viewer_forbidden_unknown_404(tmp_path):
    async def close(symbol, reason):
        raise KeyError(f"no open position for {symbol!r}")

    c, _ = make_app(tmp_path, close_position_fn=close)
    r = await c.post("/control/close_position", headers=V,
                     json={"symbol": "RELIANCE", "confirm": "CLOSE RELIANCE"})
    assert r.status_code == 403
    r = await c.post("/control/close_position", headers=O,
                     json={"symbol": "RELIANCE", "confirm": "CLOSE RELIANCE"})
    assert r.status_code == 404


async def test_order_ticket_validation_and_audit(tmp_path):
    tickets = []

    async def place(ticket, actor):
        tickets.append(ticket)
        return {"accepted": True, "qty": 10}

    c, audit = make_app(tmp_path, place_order_fn=place)
    base = {"symbol": "RELIANCE", "direction": "buy", "stop": 95.0,
            "confirm": "PLACE RELIANCE"}
    # stop mandatory
    r = await c.post("/control/order", headers=O,
                     json={**base, "stop": 0})
    assert r.status_code == 400 and "stop" in r.json()["detail"]
    # direction validated
    r = await c.post("/control/order", headers=O,
                     json={**base, "direction": "long"})
    assert r.status_code == 400
    # confirmation phrase per symbol
    r = await c.post("/control/order", headers=O,
                     json={**base, "confirm": "PLACE TCS"})
    assert r.status_code == 400
    assert tickets == []
    # viewer forbidden
    assert (await c.post("/control/order", headers=V, json=base)).status_code == 403
    # valid ticket goes through and is audited
    r = await c.post("/control/order", headers=O, json=base)
    assert r.status_code == 200 and r.json()["accepted"] is True
    assert tickets[0]["symbol"] == "RELIANCE"
    assert any(row.get("action") == "place_order" for row in audit.rows)


async def test_order_rejection_reason_passes_through_verbatim(tmp_path):
    async def place(ticket, actor):
        return {"accepted": False, "reason": "session:india_closed"}

    c, _ = make_app(tmp_path, place_order_fn=place)
    r = await c.post("/control/order", headers=O,
                     json={"symbol": "RELIANCE", "direction": "buy",
                           "stop": 95.0, "confirm": "PLACE RELIANCE"})
    assert r.status_code == 200
    assert r.json() == {"accepted": False, "reason": "session:india_closed"}


async def test_candles_endpoint(tmp_path):
    async def candles(symbol, n):
        return [{"ts": 0, "o": 1, "h": 2, "l": 0.5, "c": 1.5}][:n]

    c, _ = make_app(tmp_path, candles_fn=candles)
    assert (await c.get("/candles?symbol=RELIANCE", headers=V)).json() != []
    assert (await c.get("/candles", headers=V)).json() == []
    assert (await c.get("/candles?symbol=X")).status_code == 401


# ---------------------------------------------------------- quote feed

def make_feed(tmp_path, market_clock=None, legs=None):
    import json as _json
    bars = [{"date": "2026-08-01", "open": 100.0, "high": 104.0,
             "low": 99.0, "close": 103.0},
            {"date": "2026-08-02", "open": 103.0, "high": 105.0,
             "low": 101.0, "close": 102.0}]
    p = tmp_path / "SYM.json"
    p.write_text(_json.dumps(bars))
    return ReplayQuoteFeed({"SYM": (str(tmp_path), "SYM.json")},
                           market_clock=market_clock, symbol_legs=legs or {})


def test_feed_prices_stay_inside_real_bar_ranges(tmp_path):
    feed = make_feed(tmp_path)
    for _ in range(50):
        feed.tick_once()
        px = feed.last_price("SYM")
        assert 99.0 <= px <= 105.0, px


def test_feed_freezes_closed_markets(tmp_path):
    clock = MarketClock({"india": {"open": "09:15", "close": "15:30",
                                   "weekdays": [0, 1, 2, 3, 4], "holidays": []}})
    feed = make_feed(tmp_path, market_clock=clock, legs={"SYM": "india"})
    night = dt.datetime(2026, 8, 10, 15, 31, tzinfo=UTC)   # 21:01 IST
    before = feed.last_price("SYM")
    out = feed.tick_once(night)
    assert out == {} and feed.last_price("SYM") == before  # frozen
    day = dt.datetime(2026, 8, 11, 5, 0, tzinfo=UTC)       # 10:30 IST Tue
    assert "SYM" in feed.tick_once(day)                     # advances


def test_feed_aggregates_candles(tmp_path):
    feed = make_feed(tmp_path)
    t0 = dt.datetime(2026, 8, 11, 5, 0, tzinfo=UTC)
    for i in range(12):
        feed.tick_once(t0 + dt.timedelta(seconds=30 * i))
    cs = feed.candles("SYM")
    assert len(cs) >= 2
    assert all(k["l"] <= k["o"] <= k["h"] and k["l"] <= k["c"] <= k["h"]
               for k in cs)


# ---------------------------------------------------------- research lab

async def test_lab_allowlists_strategy_and_dataset(tmp_path):
    lab = ResearchLab(".", out_root=tmp_path / "runs", strategies=["tsmom"])
    with pytest.raises(ValueError, match="unknown strategy"):
        await lab.start("rm -rf /", "india_6m")
    with pytest.raises(ValueError, match="unknown dataset"):
        await lab.start("tsmom", "../../etc")


async def test_lab_single_flight_and_catalog(tmp_path, monkeypatch):
    lab = ResearchLab(".", out_root=tmp_path / "runs", strategies=["tsmom"])

    class FakeProc:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(0.01)
            self.returncode = 0
            return b"", b""

    fake = FakeProc()

    async def fake_exec(*a, **kw):
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    meta = await lab.start("tsmom", "india_6m", "beef")
    assert meta["status"] == "running"
    with pytest.raises(RuntimeError, match="already in progress"):
        await lab.start("tsmom", "india_6m", "beef")
    # let the reaper finish; no results.json -> failed (fail-loud, not fake-done)
    await asyncio.sleep(0.05)
    runs = lab.runs()
    assert runs and runs[0]["id"] == meta["id"]
    assert runs[0]["status"] == "failed"

    # write results.json for a fresh run -> done + metrics extracted
    (tmp_path / "runs" / meta["id"] / "results.json").write_text(
        '{"return_pct": 1.15, "reconciliation": "CLEAN", "audit_chain_ok": true}')
    m2 = dict(meta, status="done")
    import json as _json
    (tmp_path / "runs" / meta["id"] / "meta.json").write_text(_json.dumps(m2))
    runs = lab.runs()
    assert runs[0]["results"]["reconciliation"] == "CLEAN"


async def test_research_endpoints_rbac(tmp_path):
    lab = ResearchLab(".", out_root=tmp_path / "runs", strategies=["tsmom"])
    c, _ = make_app(tmp_path, research_lab=lab)
    r = await c.get("/research/runs", headers=V)
    assert r.status_code == 200 and r.json()["options"]["strategies"] == ["tsmom"]
    r = await c.post("/research/run", headers=V,
                     json={"strategy": "tsmom", "dataset": "india_6m"})
    assert r.status_code == 403                       # viewer cannot launch
    r = await c.post("/research/run", headers=O,
                     json={"strategy": "nope", "dataset": "india_6m"})
    assert r.status_code == 400                       # allowlist enforced


# ------------------------------------------------ the runnable assembly

async def test_run_paper_assembly_full_loop(tmp_path, monkeypatch):
    """Boot scripts/run_paper.py's assemble() — the REAL product — and walk
    the whole loop through the live gateway: prices present, crypto ticket
    fills and attaches exits, india ticket obeys the session clock, manual
    close lands in history, kill switch halts the router."""
    monkeypatch.setenv("COCKPIT_OPERATOR_TOKEN", "OP-TEST-123")
    monkeypatch.setenv("COCKPIT_VIEWER_TOKEN", "VW-TEST-123")
    import importlib
    run_paper = importlib.import_module("scripts.run_paper")
    app, _feed_loop, (op, vw) = await run_paper.assemble(tmp_path)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                          base_url="http://gw")
    OPH = {"Authorization": f"Bearer {op}"}

    # state serves real equity + feed status
    s = (await c.get("/state", headers=OPH)).json()
    assert s["mode"] == "paper" and s["equity"] > 0
    assert s["feed"]["kind"] == "replay_real_history"

    # clock endpoint live
    legs = (await c.get("/clock", headers=OPH)).json()["legs"]
    assert set(legs) == {"india", "mt5_forex", "mt5_crypto"}

    # crypto ticket: 24/7 -> must reach the broker and fill
    px = s["feed"]["symbols"]["BTCUSD"]["last"]
    r = await c.post("/control/order", headers=OPH,
                     json={"symbol": "BTCUSD", "direction": "buy",
                           "stop": px * 0.98, "confirm": "PLACE BTCUSD"})
    body = r.json()
    assert r.status_code == 200 and body["accepted"] is True, body
    s = (await c.get("/state", headers=OPH)).json()
    assert any(p["symbol"] == "BTCUSD" for p in s["positions"])

    # india ticket: honored or refused EXACTLY per the market clock
    from src.ops.market_clock import MarketClock
    from src.core.config_loader import load_config
    clock = MarketClock(load_config("config/master.yaml").model_extra["trading_hours"])
    px_r = (await c.get("/state", headers=OPH)).json()["feed"]["symbols"]["RELIANCE"]["last"]
    r = await c.post("/control/order", headers=OPH,
                     json={"symbol": "RELIANCE", "direction": "buy",
                           "stop": px_r * 0.98, "confirm": "PLACE RELIANCE"})
    body = r.json()
    if clock.is_open("india"):
        assert body["accepted"] in (True, False)      # may hit guards, never session
        if not body["accepted"]:
            assert "session" not in body["reason"]
    else:
        # router formats precheck refusals as "precheck_failed:session_failed"
        assert body["accepted"] is False and "session" in body["reason"], body

    # manual close of the crypto position -> lands in /history
    r = await c.post("/control/close_position", headers=OPH,
                     json={"symbol": "BTCUSD", "confirm": "CLOSE BTCUSD"})
    assert r.status_code == 200 and r.json()["reason"] == "manual_close"
    hist = (await c.get("/history", headers=OPH)).json()
    assert any(h["symbol"] == "BTCUSD" and h["exit_reason"] == "manual_close"
               for h in hist)

    # kill switch halts, then further tickets are refused by the ROUTER
    r = await c.post("/control/kill", headers=OPH,
                     json={"confirm": "KILL ALL POSITIONS", "reason": "test"})
    assert r.status_code == 200
    r = await c.post("/control/order", headers=OPH,
                     json={"symbol": "BTCUSD", "direction": "buy",
                           "stop": px * 0.98, "confirm": "PLACE BTCUSD"})
    body = r.json()
    assert body["accepted"] is False and body["reason"] == "trading_halted", body
