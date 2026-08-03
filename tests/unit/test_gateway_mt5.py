"""M44 gateway + mt5_service tests — RBAC, confirmation phrases, audit-with-actor,
viewer lockout, kill round-trip, MT5 disconnected 503, order lookup 404."""
import httpx
import pytest

from mt5_service.app import create_mt5_service
from src.core.kill_switch import KillSwitch
from src.ops.cockpit_gateway import create_gateway
from src.risk.pre_trade_gate import AuditLog
from tests.fixtures.fakes import FakeRedis, MockBroker

TOKENS = {"op-token-1234": "operator", "view-token-9999": "viewer"}


def make_app(tmp_path):
    audit = AuditLog()
    ks = KillSwitch(
        redis=FakeRedis(),
        brokers={"india": MockBroker("india", orders=[{"id": "O1"}], positions=[{"id": "P1"}])},
        sentinel_path=tmp_path / "halt.sentinel",
        unlock_phrase="RESUME NOW",
        auto_trigger_daily_loss_pct=0.03, auto_trigger_var_breach=True, max_var_daily=0.02,
    )

    async def snapshot():
        return {"pnl": 123.0, "positions": 1}

    app = create_gateway(tokens=TOKENS, kill_switch=ks, audit_log=audit, snapshot_fn=snapshot)
    return app, audit, ks


def client_for(app, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://gw", headers=headers)


async def test_auth_required_everywhere(tmp_path):
    app, _, _ = make_app(tmp_path)
    async with client_for(app) as c:
        assert (await c.get("/state")).status_code == 401
        assert (await c.post("/control/kill", json={})).status_code == 401


async def test_viewer_can_read_but_never_control(tmp_path):
    app, audit, _ = make_app(tmp_path)
    async with client_for(app, "view-token-9999") as c:
        state = await c.get("/state")
        assert state.status_code == 200 and state.json()["halted"] is False
        kill = await c.post("/control/kill",
                            json={"confirm": "KILL ALL POSITIONS", "reason": "x"})
        assert kill.status_code == 403                       # RBAC lockout
    assert not [r for r in audit.rows if r.get("action") == "kill_all"]


async def test_kill_requires_exact_confirmation_phrase(tmp_path):
    app, audit, ks = make_app(tmp_path)
    async with client_for(app, "op-token-1234") as c:
        bad = await c.post("/control/kill", json={"confirm": "yes please", "reason": "r"})
        assert bad.status_code == 400 and not await ks.is_halted()
        good = await c.post("/control/kill",
                            json={"confirm": "KILL ALL POSITIONS", "reason": "drawdown"})
        assert good.status_code == 200
        body = good.json()
        assert body["orders_cancelled"] and body["positions_closed"]
    assert await ks.is_halted() is True
    row = [r for r in audit.rows if r.get("action") == "kill_all"][0]
    assert row["actor_token_tail"] == "1234" and audit.verify_chain()


async def test_unlock_via_gateway_wrong_then_right(tmp_path):
    app, audit, ks = make_app(tmp_path)
    await ks.kill_all("setup")
    async with client_for(app, "op-token-1234") as c:
        wrong = await c.post("/control/unlock", json={"confirm": "wrong"})
        assert wrong.status_code == 403 and await ks.is_halted()
        right = await c.post("/control/unlock", json={"confirm": "RESUME NOW"})
        assert right.status_code == 200 and right.json()["halted"] is False
    assert [r for r in audit.rows if r.get("action") == "unlock_refused"]


# ---------- mt5_service ----------

class FakeMt5:
    def __init__(self, connected=True):
        self.connected = connected
        self.orders = {}

    async def is_connected(self):
        return self.connected

    async def place_order(self, body):
        self.orders[body["client_order_id"]] = body
        return {"broker_order_id": "MT5-1", "filled_qty": body["qty"], "avg_price": 1.1}

    async def lookup_order(self, coid):
        if coid not in self.orders:
            return None
        return {"broker_order_id": "MT5-1", "status": "filled",
                "filled_qty": self.orders[coid]["qty"], "avg_price": 1.1}

    async def set_stop(self, symbol, lots, sl):
        return "POS-9"

    async def modify_stop(self, position_id, sl):
        self.last_modify = (position_id, sl)

    async def close(self, symbol, lots):
        self.closed = (symbol, lots)


def mt5_client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mt5")


async def test_mt5_order_place_lookup_and_404():
    app = create_mt5_service(FakeMt5())
    async with mt5_client(app) as c:
        r = await c.post("/order", json={"client_order_id": "C1", "symbol": "EURUSD",
                                         "direction": "buy", "qty": 0.1})
        assert r.status_code == 200 and r.json()["filled_qty"] == 0.1
        assert (await c.get("/order/C1")).status_code == 200
        assert (await c.get("/order/NOPE")).status_code == 404


async def test_mt5_disconnected_terminal_rejects_orders_503():
    app = create_mt5_service(FakeMt5(connected=False))
    async with mt5_client(app) as c:
        r = await c.post("/order", json={"client_order_id": "C1", "symbol": "EURUSD",
                                         "direction": "buy", "qty": 0.1})
        assert r.status_code == 503                    # fail-closed, router reconciles


async def test_mt5_stop_endpoints_match_m35_adapter_contract():
    fake = FakeMt5()
    app = create_mt5_service(fake)
    async with mt5_client(app) as c:
        placed = await c.post("/position/stop", json={"symbol": "BTCUSD", "lots": 0.5, "sl": 58000})
        assert placed.json()["position_id"] == "POS-9"
        assert (await c.post("/position/modify", json={"position_id": "POS-9", "sl": 59000})).status_code == 200
        assert fake.last_modify == ("POS-9", 59000)
        assert (await c.post("/position/close", json={"symbol": "BTCUSD", "lots": 0.5})).status_code == 200
