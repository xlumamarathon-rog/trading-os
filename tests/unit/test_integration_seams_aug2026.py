"""Aug 6 seam hunt — regression tests for cross-module contract mismatches.

The class of bug (first seen in the ShadowRunner guard fix, 9c2eac8): two
modules that are each green in isolation but cannot actually talk to each
other when wired the way production wires them. Every test here reproduces a
seam that unit tests could never catch:

1. make_portfolio_guard called equity_fn()/positions_fn() synchronously, but
   the router's balance_fn contract — the natural production source — is
   async: float(coroutine) at the first guarded entry.
2. build_runtime ("builds the ENTIRE component graph") never composed the
   guard stack: budget fence, session guard and heat cap were silently
   ABSENT from the assembled paper/live runtime even though the modules,
   the router hook and make_portfolio_guard all existed and were green.
3. WorkerSupervisor await-ed alert_fn unconditionally while KillSwitch and
   AnomalyGuard tolerate sync callables for the SAME injected parameter —
   a sync alert_fn silently lost the WORKER DOWN alert inside the
   supervisor's own try/except.
4. ExitManager await-ed on_partial/on_exit unconditionally while the
   router _maybe_awaits its sibling on_filled callback.
"""
import asyncio

import pytest

from src.app import WorkerSupervisor
from src.core.budget_manager import BudgetManager
from src.core.config_loader import load_config
from src.core.guard_stack import make_portfolio_guard
from src.core.order_router import VAR_CACHE_KEY, OrderRequest
from src.exits.exit_manager import ExitManager
from src.risk.portfolio_heat import PortfolioHeatManager
from src.runtime import build_runtime
from tests.fixtures.fakes import FakeRedis

CFG = load_config("config/master.yaml")
TREND = {"trend_state": "STRONG_TREND", "vol_regime": "NORMAL"}


class SpyStops:
    async def place_stop(self, *a, **kw): return "S1"
    async def modify_stop(self, *a, **kw): pass
    async def cancel_stop(self, *a, **kw): pass
    async def replace_stop(self, *a, **kw): return "S1"
    async def exit_market(self, *a, **kw): pass


# ---------- 1. guard stack must accept ASYNC equity/positions sources ----------

async def test_guard_stack_accepts_async_equity_and_positions_sources():
    budget = BudgetManager(200_000)
    budget.attach(1_000_000)

    async def equity():                    # the router's balance_fn contract
        return 1_000_000.0

    async def positions():
        return []

    guard = make_portfolio_guard(
        equity_fn=equity, risk_limits=CFG.risk_limits, budget=budget,
        heat_mgr=PortfolioHeatManager(max_heat_pct=0.06), positions_fn=positions)
    ok, why = await guard({"symbol": "X", "direction": "buy"})
    assert ok, why

    budget.baseline_equity += 300_000      # fence spent — same async sources
    ok, why = await guard({"symbol": "X", "direction": "buy"})
    assert not ok and why == "budget:budget_exhausted"


async def test_guard_stack_still_accepts_sync_sources():
    budget = BudgetManager(200_000)
    budget.attach(1_000_000)
    guard = make_portfolio_guard(
        equity_fn=lambda: 1_000_000.0, risk_limits=CFG.risk_limits,
        budget=budget, heat_mgr=PortfolioHeatManager(max_heat_pct=0.06),
        positions_fn=lambda: [])
    ok, why = await guard({"symbol": "X", "direction": "buy"})
    assert ok, why


# ---------- 2. build_runtime actually wires the guard stack ----------

class _Conns:
    def get_openalgo(self): return None
    def get_mt5(self): return None


def _redis_with_var():
    r = FakeRedis()
    r.store[VAR_CACHE_KEY] = "0.005"
    return r


async def _paper_runtime(tmp_path, **kw):
    async def balance():                   # async on purpose — the real contract
        return 1_000_000.0

    # MODULE 58: build_runtime now default-wires the market clock from
    # config trading_hours, so an india order routed at test time (often
    # outside NSE hours) would be refused at the session precheck before
    # ever reaching the layer under test. These are guard-stack seam tests,
    # not session tests — pin the session open unless a test overrides it.
    async def _always_open(leg):
        return True

    kw.setdefault("session_open_fn", _always_open)
    return await build_runtime(
        CFG, mode="paper", redis=_redis_with_var(), connections=_Conns(),
        kill_brokers={}, india_margin_api=None, mt5_margin_api=None,
        balance_fn=balance, data_dir=tmp_path, **kw)


async def test_build_runtime_wires_guard_stack_into_router(tmp_path):
    budget = BudgetManager(200_000)
    budget.attach(1_000_000)
    rt = await _paper_runtime(tmp_path, budget=budget)

    assert rt.router.portfolio_guard_fn is not None
    assert rt.budget is budget

    req = OrderRequest(symbol="RELIANCE", direction="buy", entry=100.0,
                       stop=97.0, atr=1.5, algo_id="ALGO-1")
    ok, why = await rt.router.portfolio_guard_fn(req)
    assert ok                                        # funded -> admitted

    budget.baseline_equity += 300_000                # fence spent
    res = await rt.router.route_order(req)           # through the REAL router
    assert not res.accepted
    assert "budget_exhausted" in res.reason


async def test_build_runtime_without_guard_components_is_legacy(tmp_path):
    rt = await _paper_runtime(tmp_path)
    assert rt.router.portfolio_guard_fn is None      # exact pre-wiring behavior
    assert rt.budget is None and rt.session_guard is None and rt.heat_mgr is None


# ---------- 3. supervisor WORKER DOWN alert survives a SYNC alert_fn ----------

async def test_supervisor_worker_down_alert_survives_sync_alert_fn():
    alerts = []

    async def always_dead():
        raise RuntimeError("permanently broken")

    sup = WorkerSupervisor(redis=FakeRedis(),
                           alert_fn=lambda msg: alerts.append(msg),  # sync!
                           max_restarts=1)
    sup.add("dead", always_dead)
    runner = asyncio.create_task(sup.run(monitor_interval=0.01))
    await asyncio.sleep(0.3)
    await sup.shutdown()
    runner.cancel()
    assert any("WORKER DOWN" in m for m in alerts), \
        "sync alert_fn must still receive the WORKER DOWN alert"


# ---------- 4. exit-engine callbacks accept sync callables ----------

async def test_exit_callbacks_accept_sync_callables():
    events = []
    cfg = dict(CFG.model_extra["exit_manager"])
    cfg["partials"] = [{"at_r": 1.0, "pct": 33}]

    mgr = ExitManager(cfg, SpyStops(),
                      on_partial=lambda s, q, p, r: events.append(("partial", s, q)),
                      on_exit=lambda s, tel: events.append(("exit", s, tel.exit_reason)))
    pos = await mgr.attach(symbol="X", direction="buy", entry=100.0, qty=100,
                           atr=1.5, leg="india")                    # R = 3
    await mgr.on_bar("X", 103.6, 102.0, 103.2, TREND)               # +1R -> partial
    assert ("partial", "X", 33.0) in events
    await mgr.on_bar("X", 103.0, pos.stop - 0.1, pos.stop, TREND)   # stop hit
    # audit BUG-7: stop hit at the breakeven level reports its species
    assert ("exit", "X", "stop_breakeven") in events
