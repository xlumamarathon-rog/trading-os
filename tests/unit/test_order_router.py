"""MODULE 4 tests — spec acceptance: routes by classification, kill-switch
un-bypassable (incl. L2 AST proof), every outcome audited, mocked executors for
all three legs, timeout→UNKNOWN→reconcile, fail-closed pre-checks.
"""
import ast
import inspect

import httpx
import pytest

import src.core.order_router as order_router_module
from src.core.config_loader import load_config
from src.core.kill_switch import KillSwitch
from src.core.margin_checker import MarginChecker
from src.core.order_router import VAR_CACHE_KEY, OrderRequest, OrderRouter
from src.core.order_state_machine import OrderState
from src.intel.anomaly_guard import PAUSE_ENTRIES_KEY, AnomalyGuard
from tests.fixtures.fakes import FakeRedis, FailingRedis, MockBroker, MockMarginAPI

CFG = load_config("config/master.yaml")


class BrokerEndpoint:
    """httpx.MockTransport broker fake — records hits, scriptable behavior."""

    def __init__(self, behavior="fill"):
        self.behavior = behavior
        self.hits = []

    def transport(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.hits.append((request.method, request.url.path))
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            if request.method == "POST":
                if self.behavior == "fill":
                    return httpx.Response(
                        200, json={"broker_order_id": "B1", "filled_qty": 0, "avg_price": None}
                    )
                if self.behavior == "instant_fill":
                    import json as _json

                    body = _json.loads(request.content.decode())
                    return httpx.Response(
                        200,
                        json={"broker_order_id": "B1", "filled_qty": body["qty"], "avg_price": 100.0},
                    )
                if self.behavior == "reject":
                    return httpx.Response(400, json={"error": "price band"})
                if self.behavior == "timeout":
                    raise httpx.ConnectTimeout("timeout after send")
            if request.method == "GET" and request.url.path.startswith("/order/"):
                if self.behavior == "timeout":  # reconcile: broker DID get it
                    return httpx.Response(
                        200, json={"broker_order_id": "B7", "status": "acked", "filled_qty": 0}
                    )
                return httpx.Response(404)
            return httpx.Response(500)

        return httpx.MockTransport(handler)


class FakeConnections:
    def __init__(self, india: BrokerEndpoint, mt5: BrokerEndpoint):
        self._india = httpx.AsyncClient(base_url="http://openalgo.local", transport=india.transport())
        self._mt5 = httpx.AsyncClient(base_url="https://mt5.local", transport=mt5.transport())

    def get_openalgo(self):
        return self._india

    def get_mt5(self):
        return self._mt5


def build_router(
    redis=None,
    india_behavior="instant_fill",
    mt5_behavior="instant_fill",
    margin_api=None,
    signal_valid_fn=lambda s, d: True,
    band_check_fn=lambda s, p: True,
    session_open_fn=lambda leg: True,
):
    redis = redis if redis is not None else FakeRedis()
    if hasattr(redis, "store"):
        redis.store.setdefault(VAR_CACHE_KEY, "0.005")
    india_ep = BrokerEndpoint(india_behavior)
    mt5_ep = BrokerEndpoint(mt5_behavior)
    audits = []
    fills = []
    ks = KillSwitch(
        redis=redis,
        brokers={"india": MockBroker("india"), "mt5": MockBroker("mt5")},
        sentinel_path="/tmp/router_test_halt.sentinel",
        unlock_phrase="GO",
        auto_trigger_daily_loss_pct=0.03,
        auto_trigger_var_breach=True,
        max_var_daily=CFG.risk_limits.max_var_daily,
    )
    import os

    if os.path.exists("/tmp/router_test_halt.sentinel"):
        os.remove("/tmp/router_test_halt.sentinel")
    guard = AnomalyGuard(
        redis=redis,
        velocity_sigma={"s1": 6, "s5": 5, "s30": 4},
        spread_blowout_mult=3.0,
        volume_spike_mult=5.0,
        cooloff_minutes=15,
    )
    router = OrderRouter(
        config=CFG,
        kill_switch=ks,
        anomaly_guard=guard,
        margin_checker=MarginChecker(
            CFG.risk_limits,
            india_api=margin_api or MockMarginAPI(available=10_000_000, required=100_000),
            mt5_api=margin_api or MockMarginAPI(free=9_000_000, required=100_000, equity_value=10_000_000),
        ),
        connections=FakeConnections(india_ep, mt5_ep),
        redis=redis,
        balance_fn=lambda: 1_000_000.0,
        signal_valid_fn=signal_valid_fn,
        band_check_fn=band_check_fn,
        session_open_fn=session_open_fn,
        audit_fn=lambda row: audits.append(row),
        on_filled=lambda rec: fills.append(rec),
    )
    return router, {"audits": audits, "fills": fills, "india_ep": india_ep, "mt5_ep": mt5_ep, "ks": ks, "redis": redis}


def req(symbol="RELIANCE", **over):
    kw = dict(symbol=symbol, direction="buy", entry=100.0, stop=98.0, atr=1.5,
              algo_id="ALGO-REG-001", lot_size=1.0)
    kw.update(over)
    return OrderRequest(**kw)


# ---------- classification ----------

def test_three_way_market_classification():
    router, _ = build_router()
    assert router.classify_market("RELIANCE") == "india"
    assert router.classify_market("EURUSD") == "mt5_forex"
    assert router.classify_market("BTCUSD") == "mt5_crypto"


# ---------- the door ----------

async def test_happy_path_india_fill_and_exit_hook():
    router, ctx = build_router()
    result = await router.route_order(req())
    assert result.accepted and result.record.state is OrderState.FILLED
    assert ctx["fills"] and ctx["fills"][0].symbol == "RELIANCE"
    assert ctx["audits"][-1]["outcome"].startswith("accepted")


async def test_happy_path_mt5_crypto_leg():
    router, ctx = build_router()
    result = await router.route_order(req(symbol="BTCUSD", entry=60_000.0, stop=58_000.0, atr=900.0,
                                          algo_id=None, lot_size=0.01))
    assert result.accepted
    assert result.checks["leg"] == "mt5_crypto"
    assert ctx["mt5_ep"].hits and not any(m == "POST" for m, _ in ctx["india_ep"].hits)


async def test_halted_rejects_and_never_touches_broker():
    router, ctx = build_router()
    await ctx["ks"].kill_all("test halt")
    result = await router.route_order(req())
    assert not result.accepted and result.reason == "trading_halted"
    assert not any(m == "POST" for m, _ in ctx["india_ep"].hits)  # broker untouched
    assert ctx["audits"][-1]["outcome"] == "rejected:trading_halted"


async def test_redis_down_is_fail_closed_no_order():
    router, ctx = build_router(redis=FailingRedis())
    result = await router.route_order(req())
    assert not result.accepted and result.reason == "trading_halted"
    assert not any(m == "POST" for m, _ in ctx["india_ep"].hits)


async def test_entries_paused_by_anomaly_guard_rejects():
    router, ctx = build_router()
    await ctx["redis"].set(PAUSE_ENTRIES_KEY, "1")
    result = await router.route_order(req())
    assert not result.accepted and result.reason == "entries_paused_shock"


async def test_missing_sebi_algo_id_rejected_on_india_leg():
    router, _ = build_router()
    result = await router.route_order(req(algo_id=None))
    assert not result.accepted and result.reason == "missing_sebi_algo_id"


async def test_var_cache_missing_fail_closed():
    router, ctx = build_router()
    await ctx["redis"].delete(VAR_CACHE_KEY)
    result = await router.route_order(req())
    assert not result.accepted and "var_cache_missing" in result.reason


async def test_var_at_limit_rejected():
    router, ctx = build_router()
    await ctx["redis"].set(VAR_CACHE_KEY, str(CFG.risk_limits.max_var_daily))
    result = await router.route_order(req())
    assert not result.accepted and "var_at_limit" in result.reason


async def test_failed_signal_precheck_named_and_no_broker_call():
    router, ctx = build_router(signal_valid_fn=lambda s, d: False)
    result = await router.route_order(req())
    assert not result.accepted and "signal_failed" in result.reason
    assert not any(m == "POST" for m, _ in ctx["india_ep"].hits)


async def test_crashing_precheck_is_fail_closed():
    def boom(s, p):
        raise RuntimeError("band feed down")

    router, _ = build_router(band_check_fn=boom)
    result = await router.route_order(req())
    assert not result.accepted and "price_band_error_fail_closed" in result.reason


async def test_margin_rejection_propagates():
    router, _ = build_router(margin_api=MockMarginAPI(available=1_000, required=100_000))
    result = await router.route_order(req())
    assert not result.accepted and result.reason.startswith("margin:")


async def test_broker_reject_recorded():
    router, ctx = build_router(india_behavior="reject")
    result = await router.route_order(req())
    assert not result.accepted and "broker_rejected" in result.reason
    assert result.record.state is OrderState.REJECTED


async def test_timeout_reconciles_to_acked_no_double_send():
    """Chaos: POST times out; reconcile finds order ACKED at broker — accepted, no retry."""
    router, ctx = build_router(india_behavior="timeout")
    result = await router.route_order(req())
    assert result.accepted and result.record.state is OrderState.ACKED
    posts = [h for h in ctx["india_ep"].hits if h[0] == "POST"]
    assert len(posts) == 1  # never double-sent


async def test_every_outcome_is_audited():
    router, ctx = build_router()
    await router.route_order(req())                        # accept
    await router.route_order(req(algo_id=None))            # reject
    await ctx["ks"].kill_all("x")
    await router.route_order(req())                        # halted
    assert len(ctx["audits"]) == 3


async def test_happy_path_mt5_forex_leg():
    """GATE G2: third leg end-to-end (EURUSD → mt5_forex → MT5 service)."""
    router, ctx = build_router()
    result = await router.route_order(req(symbol="EURUSD", entry=1.0850, stop=1.0800, atr=0.0025,
                                          algo_id=None, lot_size=0.01))
    assert result.accepted and result.checks["leg"] == "mt5_forex"
    assert any(m == "POST" for m, _ in ctx["mt5_ep"].hits)


# ---------- L2: kill switch is structurally un-bypassable ----------

def test_l2_ast_kill_switch_is_first_await_in_route_order():
    src = inspect.getsource(order_router_module)
    tree = ast.parse(src)
    route = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "route_order"
    )
    awaits = [n for n in ast.walk(route) if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)]
    awaits.sort(key=lambda n: (n.lineno, n.col_offset))  # ast.walk is BFS — restore source order
    awaited_calls = []
    for node in awaits:
        f = node.value.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "?")
        awaited_calls.append(name)
    assert "require_trading_allowed" in awaited_calls, "kill-switch check missing from router"
    assert awaited_calls.index("require_trading_allowed") == 0, (
        "kill-switch must be the FIRST awaited call in route_order"
    )
    dispatch_idx = awaited_calls.index("_dispatch")
    assert awaited_calls.index("require_trading_allowed") < dispatch_idx
