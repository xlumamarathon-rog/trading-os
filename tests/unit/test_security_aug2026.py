"""Aug 7 final security sweep — regression tests for the three findings fixed.

1. Cockpit /ui/{asset} used a str.startswith prefix check to contain path
   traversal; a SIBLING directory sharing the prefix (base "…/web" vs
   "…/web-secret") slipped through. Now is_relative_to.
2. MT5 execution service (places+closes REAL broker orders) had ZERO auth on
   every endpoint — pure network trust. Now an optional shared-secret header,
   enforced when configured (constant-time), no-op when not (dev/tests).
3. /config trusted the provider to redact secrets. Now the gateway ALSO
   redacts defensively so a careless provider cannot leak keys to a viewer.
"""
import httpx
import pytest

from mt5_service.app import create_mt5_service
from src.ops.cockpit_gateway import create_gateway, sanitize_config_view


def _client(app, base="http://t", **kw):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base, **kw)


# ---------- 1. path-traversal: sibling-prefix directory must NOT be served ----

async def test_ui_sibling_prefix_directory_is_blocked(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>TRADING</h1>")
    (web / "app.js").write_text("ok")
    # a sibling dir that SHARES the 'web' prefix — the old startswith guard
    # would have served this; is_relative_to must reject it.
    secret = tmp_path / "web-secret"
    secret.mkdir()
    (secret / "keys.txt").write_text("BROKER_SECRET=leak")

    async def snapshot():
        return {"ok": True}

    app = create_gateway(tokens={"V": "viewer"}, kill_switch=_DummyKS(),
                         audit_log=_DummyAudit(), snapshot_fn=snapshot,
                         ui_dir=str(web))
    async with _client(app) as c:
        assert (await c.get("/ui/app.js")).status_code == 200          # legit asset
        leak = await c.get("/ui/..%2Fweb-secret%2Fkeys.txt")           # decoded traversal
        assert leak.status_code in (400, 404)
        assert b"leak" not in leak.content


def test_sanitize_helper_rejects_sibling_prefix_paths(tmp_path):
    """Unit-level proof of the containment predicate itself."""
    base = (tmp_path / "web").resolve()
    (tmp_path / "web").mkdir()
    sibling = (tmp_path / "web" / ".." / "web-secret" / "x").resolve()
    inside = (tmp_path / "web" / "app.js").resolve()
    assert not sibling.is_relative_to(base)      # the bug the old check missed
    assert inside.is_relative_to(base)


# ---------- 2. MT5 service auth ----------

class _FakeMt5:
    async def is_connected(self): return True
    async def place_order(self, body): return {"broker_order_id": "X", "filled_qty": body["qty"], "avg_price": 1.1}
    async def lookup_order(self, coid): return None
    async def set_stop(self, *a): return "POS-1"
    async def modify_stop(self, *a): pass
    async def close(self, *a): pass


_ORDER = {"client_order_id": "C1", "symbol": "EURUSD", "direction": "buy", "qty": 0.1}


async def test_mt5_service_rejects_orders_without_token_when_configured():
    app = create_mt5_service(_FakeMt5(), auth_token="s3cret-mt5")
    async with _client(app, base="http://mt5") as c:
        assert (await c.get("/health")).status_code == 200            # liveness open
        assert (await c.post("/order", json=_ORDER)).status_code == 401
        assert (await c.post("/order", json=_ORDER,
                             headers={"X-MT5-Auth": "wrong"})).status_code == 401
        assert (await c.post("/position/close",
                             json={"symbol": "EURUSD", "lots": 0.1})).status_code == 401
        ok = await c.post("/order", json=_ORDER, headers={"X-MT5-Auth": "s3cret-mt5"})
        assert ok.status_code == 200 and ok.json()["filled_qty"] == 0.1


async def test_mt5_service_unconfigured_is_backward_compatible():
    app = create_mt5_service(_FakeMt5())                               # no token
    async with _client(app, base="http://mt5") as c:
        assert (await c.post("/order", json=_ORDER)).status_code == 200
        assert (await c.post("/position/close",
                             json={"symbol": "EURUSD", "lots": 0.1})).status_code == 200


async def test_mt5_service_reads_token_from_env(monkeypatch):
    monkeypatch.setenv("MT5_SERVICE_TOKEN", "env-token")
    app = create_mt5_service(_FakeMt5())
    async with _client(app, base="http://mt5") as c:
        assert (await c.post("/order", json=_ORDER)).status_code == 401
        assert (await c.post("/order", json=_ORDER,
                             headers={"X-MT5-Auth": "env-token"})).status_code == 200


# ---------- 3. /config defensive redaction ----------

def test_sanitize_config_view_redacts_secrets_at_every_depth():
    raw = {
        "risk_limits": {"max_risk_per_trade_pct": 0.01},
        "broker": {"india": {"apikey": "LIVEKEY123", "static_ip_confirmed": False}},
        "kill_switch": {"unlock_phrase": "RESUME NOW"},
        "creds": [{"password": "hunter2"}, {"api_key": "AK"}],
    }
    clean = sanitize_config_view(raw)
    flat = str(clean)
    for leaked in ("LIVEKEY123", "hunter2", "RESUME NOW", "AK"):
        assert leaked not in flat
    assert clean["risk_limits"]["max_risk_per_trade_pct"] == 0.01     # non-secrets kept
    assert clean["broker"]["india"]["static_ip_confirmed"] is False
    assert raw["broker"]["india"]["apikey"] == "LIVEKEY123"           # input not mutated


async def test_config_endpoint_redacts_even_a_careless_provider(tmp_path):
    async def snapshot(): return {"ok": True}

    async def leaky_config():
        # a deployer who wires the RAW config by mistake
        return {"risk_limits": {"max_risk_per_trade_pct": 0.01},
                "broker": {"india": {"apikey": "SHOULD-NOT-LEAK"}}}

    app = create_gateway(tokens={"V1234567": "viewer"}, kill_switch=_DummyKS(),
                         audit_log=_DummyAudit(), snapshot_fn=snapshot,
                         config_view_fn=leaky_config, ui_dir=None)
    async with _client(app, base="http://gw") as c:
        r = await c.get("/config", headers={"Authorization": "Bearer V1234567"})
        assert r.status_code == 200
        assert "SHOULD-NOT-LEAK" not in str(r.json())
        assert r.json()["risk_limits"]["max_risk_per_trade_pct"] == 0.01


# ---------- 4. kill-switch unlock: constant-time compare, semantics intact ----

def _kill_switch(unlock_phrase):
    from src.core.kill_switch import KillSwitch

    class MemRedis:
        def __init__(self): self.store = {"TRADING_HALTED": "1"}
        async def get(self, k): return self.store.get(k)
        async def set(self, k, v): self.store[k] = v
        async def delete(self, k): self.store.pop(k, None)

    return KillSwitch(redis=MemRedis(), brokers={}, sentinel_path="/tmp/_ks_none.sentinel",
                      unlock_phrase=unlock_phrase, auto_trigger_daily_loss_pct=0.03,
                      auto_trigger_var_breach=True, max_var_daily=0.02)


async def test_unlock_accepts_exact_phrase_and_clears_halt():
    ks = _kill_switch("RESUME NOW")
    assert await ks.is_halted() is True
    await ks.unlock("RESUME NOW")                     # exact -> succeeds
    assert await ks.is_halted() is False


@pytest.mark.parametrize("wrong", ["RESUME NO", "RESUME NOW ", "resume now", "", "X"])
async def test_unlock_rejects_wrong_phrase(wrong):
    ks = _kill_switch("RESUME NOW")
    with pytest.raises(PermissionError):
        await ks.unlock(wrong)
    assert await ks.is_halted() is True               # stays halted (fail-closed)


async def test_unlock_refuses_when_phrase_unconfigured():
    ks = _kill_switch("")                              # empty config
    with pytest.raises(PermissionError):
        await ks.unlock("")                            # even empty attempt refused
    assert await ks.is_halted() is True


def test_unlock_uses_constant_time_compare():
    """Guard the hardening itself: a byte-by-byte != would satisfy the tests
    above too, so assert the constant-time primitive is actually on the path."""
    import inspect

    from src.core import kill_switch
    src = inspect.getsource(kill_switch.KillSwitch.unlock)
    assert "compare_digest" in src


# ---------- 5. go-live preflight: pytest basetemp is private (PYSEC-2026-1845) -

def test_go_live_preflight_uses_private_basetemp():
    import os
    import scripts.go_live_check as glc

    bt = glc._private_basetemp()
    parent = os.path.dirname(bt)
    mode = os.stat(parent).st_mode & 0o777
    assert mode == 0o700, f"basetemp parent must be private, got {oct(mode)}"
    assert "/tmp/pytest-of-" not in bt                # not the world-predictable path


# ---------- minimal doubles ----------

class _DummyKS:
    async def is_halted(self): return False


class _DummyAudit:
    def append(self, row): pass
