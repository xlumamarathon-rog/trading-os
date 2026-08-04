"""Wave 12 — live-readiness tests: runtime assembly (paper + live), SAFE-START,
live ramp cap, resume-entries release, snapshot ⇄ Next.js UI contract canary,
tick feed fanout, EOD worker gate/live-day advance."""
import json
import re
from pathlib import Path

import httpx
import pytest

from src.app import LIVE_ACK_PHRASE, LiveGateError
from src.core.config_loader import load_config
from src.core.order_router import VAR_CACHE_KEY, OrderRequest
from src.core.paper_broker import PaperBroker
from src.intel.anomaly_guard import PAUSE_ENTRIES_KEY
from src.ops.eod_worker import run_eod
from src.ops.paper_server import create_paper_server
from src.ops.snapshot import SnapshotBuilder
from src.intel.tick_feed import TickFeedWorker
from src.runtime import build_runtime, ramp_cap_for, resume_entries
from tests.fixtures.fakes import FakeRedis, MockMarginAPI

CFG = load_config("config/master.yaml")


class Conns:
    def __init__(self, app):
        self._c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://paper")

    def get_openalgo(self):
        return self._c

    def get_mt5(self):
        return self._c


def paper_stack(tmp_path):
    broker = PaperBroker(costs=CFG.execution_costs.india,
                         impact=CFG.execution_costs.impact_model,
                         starting_cash=1_000_000,
                         adv_map={"RELIANCE": 8_000_000},
                         daily_sigma_map={"RELIANCE": 0.015})
    app = create_paper_server(broker)
    redis = FakeRedis()
    redis.store[VAR_CACHE_KEY] = "0.005"
    return broker, Conns(app), redis


def full_gate(tmp_path, **over):
    gate = {"paper_days_completed": 244, "clean_reconciliation_streak": 244,
            "sebi_checks_passed": True, "human_ack": LIVE_ACK_PHRASE,
            "live_days_completed": 0, "history": []}
    gate.update(over)
    p = tmp_path / "gate_state.json"
    p.write_text(json.dumps(gate))
    return p


async def make_runtime(tmp_path, mode, gate_path, cfg=CFG):
    broker, conns, redis = paper_stack(tmp_path)
    rt = await build_runtime(
        cfg, mode=mode, redis=redis, connections=conns, kill_brokers={},
        india_margin_api=MockMarginAPI(available=10_000_000, required=100_000),
        mt5_margin_api=MockMarginAPI(free=9e6, required=1e5, equity_value=1e7),
        balance_fn=lambda: 1_000_000.0, data_dir=tmp_path / "runtime",
        gate_path=gate_path, signal_valid_fn=lambda s, d: True,
        band_check_fn=lambda s, p: True, session_open_fn=lambda leg: True,
        india_apikey="K", algo_id="ALGO-1")
    return rt, broker


# ---------------- runtime assembly ----------------

async def test_paper_runtime_boots_and_trades_end_to_end(tmp_path):
    rt, broker = await make_runtime(tmp_path, "paper", tmp_path / "no_gate.json")
    assert rt.mode == "paper" and not rt.safe_started
    broker.on_tick("RELIANCE", 2500.0)
    result = await rt.router.route_order(OrderRequest(
        symbol="RELIANCE", direction="buy", entry=2500.0, stop=2450.0, atr=30.0,
        algo_id="ALGO-1"))
    assert result.accepted and broker.positionbook()
    assert rt.audit.verify_chain() and any(r["type"] == "boot" for r in rt.audit.rows)


async def test_live_runtime_requires_the_full_gate(tmp_path):
    with pytest.raises(LiveGateError):
        await make_runtime(tmp_path, "live", tmp_path / "missing_gate.json")


async def test_live_runtime_with_gate_boots_SAFE_STARTED(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.app.assert_live_allowed", lambda cfg, gp: {"ok": True}, raising=True)
    # static_ip is false in repo config — bypass only the config clause via monkeypatch
    import src.runtime as rtmod
    monkeypatch.setattr(rtmod, "assert_live_allowed", lambda cfg, gp: {"ok": True})
    gate = full_gate(tmp_path)
    rt, broker = await make_runtime(tmp_path, "live", gate)
    assert rt.safe_started is True
    # entries are PAUSED: router must reject even a perfect order
    broker.on_tick("RELIANCE", 2500.0)
    result = await rt.router.route_order(OrderRequest(
        symbol="RELIANCE", direction="buy", entry=2500.0, stop=2450.0, atr=30.0,
        algo_id="ALGO-1"))
    assert not result.accepted and result.reason == "entries_paused_shock"
    # operator resume via the documented path → trading allowed
    await resume_entries(rt.redis, rt.audit, actor="op-1234")
    result2 = await rt.router.route_order(OrderRequest(
        symbol="RELIANCE", direction="buy", entry=2500.0, stop=2450.0, atr=30.0,
        algo_id="ALGO-1"))
    assert result2.accepted
    assert any(r.get("action") == "resume_entries" for r in rt.audit.rows)


# ---------------- live ramp ----------------

def test_ramp_cap_applies_during_first_live_days(tmp_path):
    gate = full_gate(tmp_path, live_days_completed=0)
    assert ramp_cap_for(CFG, gate, "live") == 0.01           # defaults: 5 days @1%
    gate2 = full_gate(tmp_path, live_days_completed=7)
    assert ramp_cap_for(CFG, gate2, "live") is None          # ramp over
    assert ramp_cap_for(CFG, gate, "paper") is None          # paper never ramped


async def test_ramped_sizing_is_smaller_in_early_live(tmp_path, monkeypatch):
    import src.runtime as rtmod
    monkeypatch.setattr(rtmod, "assert_live_allowed", lambda cfg, gp: {"ok": True})
    gate = full_gate(tmp_path, live_days_completed=0)
    rt, broker = await make_runtime(tmp_path, "live", gate)
    await resume_entries(rt.redis, rt.audit, actor="op")
    broker.on_tick("RELIANCE", 2500.0)
    result = await rt.router.route_order(OrderRequest(
        symbol="RELIANCE", direction="buy", entry=2500.0, stop=2450.0, atr=30.0,
        algo_id="ALGO-1"))
    assert result.accepted
    # ramp: notional ≤ 1% of balance (vs normal 5%)
    assert result.record.filled_qty * 2500.0 <= 1_000_000 * 0.01 * 1.001


# ---------------- snapshot ⇄ UI contract canary ----------------

def test_snapshot_matches_nextjs_cockpitstate_contract(tmp_path):
    ts_src = Path("cockpit-next/lib/types.ts").read_text()
    block = ts_src.split("interface CockpitState")[1].split("}")[0]
    ts_fields = set(re.findall(r"^\s*(\w+)\s*:", block, re.M))

    sb = SnapshotBuilder(mode="paper")
    sb.push_candle("RELIANCE", 1, 1, 2, 0.5, 1.5)
    sb.push_equity(1, 1_000_000)
    sb.push_event("boot")
    snap = sb.build(halted=False, role="operator", equity=1e6, pnl=0.0, costs=0.0,
                    var95=0.005, var_limit=0.02, positions_fn=lambda: [],
                    workers={"tick_feed": True}, approvals=[],
                    gex={"net": 0, "regime": "dampen", "strikes": []},
                    gate_path=tmp_path / "g.json")
    missing = ts_fields - set(snap.keys())
    assert not missing, f"snapshot missing UI fields: {missing}"


# ---------------- tick feed ----------------

async def test_tick_feed_fans_out_and_heartbeats(tmp_path):
    from src.exits.exit_manager import ExitManager
    from src.intel.anomaly_guard import AnomalyGuard

    redis = FakeRedis()
    guard = AnomalyGuard(redis=redis, velocity_sigma={"s1": 6, "s5": 5, "s30": 4},
                         spread_blowout_mult=3, volume_spike_mult=5, cooloff_minutes=15)

    class NullAdapter:
        async def place_stop(self, *a, **kw): return "S1"
        async def modify_stop(self, *a): pass
        async def exit_market(self, *a, **kw): pass

    exit_mgr = ExitManager(CFG.model_extra["exit_manager"], NullAdapter())
    sb = SnapshotBuilder(mode="paper")

    async def stream():
        for i in range(12):
            yield {"symbol": "RELIANCE", "price": 2500 + i, "ts": 1000 + i}

    worker = TickFeedWorker(stream_factory=stream, guard=guard, exit_mgr=exit_mgr,
                            snapshot=sb, redis=redis, sub_bar_ticks=6)
    await worker.run()
    assert worker.processed == 12
    assert len(sb.candles["RELIANCE"]) == 2          # two 6-tick sub-bars
    assert "heartbeat:tick_feed" in redis.store       # R9


# ---------------- EOD worker ----------------

async def test_eod_worker_advances_gate_and_live_days(tmp_path):
    gate = tmp_path / "gate.json"
    rows = [{"client_order_id": "1", "symbol": "A", "qty": 1, "price": 10.0}]
    state = {"cash": 1e6, "equity": 1e6, "total_costs": 0.0, "positions": [], "resting": []}
    alerts = []

    async def alert(msg):
        alerts.append(msg)

    r1 = await run_eod(date="2026-08-04", mode="paper", internal_trades=rows,
                       broker_trades=rows, naked_positions=[], broker_state=state,
                       fills_today=[], audit_rows=1, gate_path=gate,
                       reports_dir=tmp_path / "reports", alert_fn=alert)
    assert r1["clean"] and r1["gate"]["paper_days_completed"] == 1
    r2 = await run_eod(date="2026-08-05", mode="live", internal_trades=rows,
                       broker_trades=rows, naked_positions=[], broker_state=state,
                       fills_today=[], audit_rows=2, gate_path=gate,
                       reports_dir=tmp_path / "reports", alert_fn=alert)
    assert r2["gate"]["live_days_completed"] == 1     # ramp counter advanced
    assert (tmp_path / "reports" / "report_2026-08-05.md").exists()
    assert alerts and "CLEAN" in alerts[0]

    # dirty live day: gate day doesn't count AND live ramp day doesn't advance
    bad = [{"client_order_id": "PHANTOM", "symbol": "X", "qty": 9, "price": 1.0}] + rows
    r3 = await run_eod(date="2026-08-06", mode="live", internal_trades=bad,
                       broker_trades=rows, naked_positions=[], broker_state=state,
                       fills_today=[], audit_rows=3, gate_path=gate,
                       reports_dir=tmp_path / "reports", alert_fn=alert)
    assert not r3["clean"] and r3["gate"]["live_days_completed"] == 1
