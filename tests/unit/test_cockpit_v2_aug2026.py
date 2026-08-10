"""MODULES 58-60 — cockpit v2: market clock in the runtime entry path, the
new gateway surface (/clock /portfolio /history /brokers), and the broker
settings provider. Every test guards an invariant from spec §12:

  - india entries REFUSED off-session; exits never gated; crypto unaffected
  - new endpoints: auth required, operator-only writes, secrets never leak
  - /brokers/save structurally cannot touch the live gate
"""
import datetime as dt
import time
from pathlib import Path

import httpx
import pytest
import yaml

from src.core.kill_switch import KillSwitch
from src.ops.broker_settings import BrokerSettings
from src.ops.cockpit_gateway import create_gateway
from src.ops.market_clock import MarketClock
from src.ops.persistence import JsonlAuditLog

UTC = dt.timezone.utc

HOURS = {"india": {"open": "09:15", "close": "15:30",
                   "weekdays": [0, 1, 2, 3, 4], "holidays": []},
         "forex": {}}


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


class FakeCfg:
    """Minimal cfg carrying model_extra like the pydantic config object."""

    def __init__(self, extra):
        self.model_extra = extra


# ---------------------------------------------------------------- gateway

def make_app(tmp_path: Path, **extra_wiring):
    ks = KillSwitch(redis=MemRedis(), brokers={},
                    sentinel_path=tmp_path / "halt.sentinel",
                    unlock_phrase="GO", auto_trigger_daily_loss_pct=0.03,
                    auto_trigger_var_breach=True, max_var_daily=0.02)

    async def snapshot():
        return {"equity": 1.0}

    app = create_gateway(tokens={"VTOK1234": "viewer", "OTOK5678": "operator"},
                         kill_switch=ks,
                         audit_log=JsonlAuditLog(tmp_path / "a.jsonl"),
                         snapshot_fn=snapshot, ui_dir=None, **extra_wiring)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://gw")


V = {"Authorization": "Bearer VTOK1234"}
O = {"Authorization": "Bearer OTOK5678"}


async def test_clock_endpoint_serves_leg_status(tmp_path):
    c = make_app(tmp_path, market_clock=MarketClock(HOURS))
    r = await c.get("/clock", headers=V)
    assert r.status_code == 200
    legs = r.json()["legs"]
    assert set(legs) == {"india", "mt5_forex", "mt5_crypto"}
    assert isinstance(legs["india"]["open"], bool)


async def test_clock_requires_auth(tmp_path):
    c = make_app(tmp_path, market_clock=MarketClock(HOURS))
    assert (await c.get("/clock")).status_code == 401


async def test_clock_unwired_returns_empty_not_500(tmp_path):
    c = make_app(tmp_path)
    r = await c.get("/clock", headers=V)
    assert r.status_code == 200 and r.json()["legs"] == {}


async def test_history_filters_and_cap(tmp_path):
    rows = [
        {"symbol": "RELIANCE", "leg": "india", "exit_reason": "stop_hit",
         "date": "2026-08-01", "r": -1.0},
        {"symbol": "RELIANCE", "leg": "india", "exit_reason": "trail_stop",
         "date": "2026-08-05", "r": 2.0},
        {"symbol": "BTCUSD", "leg": "mt5_crypto", "exit_reason": "stop_hit",
         "date": "2026-08-06", "r": -0.9},
    ]

    async def history():
        return rows

    c = make_app(tmp_path, history_fn=history)
    got = (await c.get("/history", headers=V)).json()
    assert len(got) == 3
    got = (await c.get("/history?symbol=reliance", headers=V)).json()
    assert len(got) == 2                      # case-insensitive symbol match
    got = (await c.get("/history?exit_reason=stop_hit&leg=india",
                       headers=V)).json()
    assert len(got) == 1 and got[0]["date"] == "2026-08-01"
    got = (await c.get("/history?since=2026-08-05", headers=V)).json()
    assert {r["date"] for r in got} == {"2026-08-05", "2026-08-06"}
    got = (await c.get("/history?limit=1", headers=V)).json()
    assert len(got) == 1


async def test_brokers_endpoint_redacts_even_a_careless_provider(tmp_path):
    async def leaky_status():
        # a careless provider hands back an actual secret — the gateway's
        # defensive sanitizer must strip it before any client sees it
        return {"india": {"provider": "dhan", "api_key": "SUPER-SECRET-VALUE"}}

    c = make_app(tmp_path, brokers_status_fn=leaky_status)
    body = (await c.get("/brokers", headers=V)).json()
    assert body["india"]["api_key"] == "***REDACTED***"
    assert "SUPER-SECRET-VALUE" not in str(body)


async def test_broker_save_operator_only(tmp_path):
    async def save(broker, settings, actor):
        return {"saved": settings}

    c = make_app(tmp_path, broker_save_fn=save)
    r = await c.post("/brokers/save", headers=V,
                     json={"broker": "india", "settings": {"provider": "dhan"}})
    assert r.status_code == 403               # viewer refused
    r = await c.post("/brokers/save", headers=O,
                     json={"broker": "india", "settings": {"provider": "dhan"}})
    assert r.status_code == 200


async def test_broker_save_refuses_gate_keys_at_the_gateway(tmp_path):
    """THE invariant: even if a provider fn would accept it, the gateway
    refuses gate-controlled keys before the provider ever runs."""
    called = []

    async def save(broker, settings, actor):
        called.append(settings)
        return {"saved": settings}

    c = make_app(tmp_path, broker_save_fn=save)
    for key in ("static_ip_confirmed", "human_ack", "sebi_checks_passed",
                "paper_days_completed", "clean_reconciliation_streak"):
        r = await c.post("/brokers/save", headers=O,
                         json={"broker": "india", "settings": {key: True}})
        assert r.status_code == 403, key
        assert "gate-controlled" in r.json()["detail"]
    assert called == []                        # provider never invoked


async def test_broker_test_operator_only_and_audited(tmp_path):
    async def probe(broker):
        return {"ok": True, "detail": "HTTP 200"}

    audit = JsonlAuditLog(tmp_path / "a2.jsonl")
    ks = KillSwitch(redis=MemRedis(), brokers={},
                    sentinel_path=tmp_path / "h2.sentinel",
                    unlock_phrase="GO", auto_trigger_daily_loss_pct=0.03,
                    auto_trigger_var_breach=True, max_var_daily=0.02)

    async def snapshot():
        return {}

    app = create_gateway(tokens={"VTOK1234": "viewer", "OTOK5678": "operator"},
                         kill_switch=ks, audit_log=audit, snapshot_fn=snapshot,
                         ui_dir=None, broker_test_fn=probe)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                          base_url="http://gw")
    assert (await c.post("/brokers/test", headers=V,
                         json={"broker": "india"})).status_code == 403
    r = await c.post("/brokers/test", headers=O, json={"broker": "india"})
    assert r.status_code == 200 and r.json()["ok"] is True
    rows = [r for r in audit.rows if r.get("action") == "broker_test"]
    assert len(rows) == 1


# ------------------------------------------------------- broker settings

def make_settings(tmp_path: Path) -> BrokerSettings:
    cfg = FakeCfg({"broker": {
        "india": {"provider": "dhan", "base_url": "http://127.0.0.1:5",
                  "static_ip_confirmed": False, "api_key": "${X}"},
        "mt5": {"exec_service_url": "", "symbol_classes": {"forex": ["EURUSD"]}},
    }})
    return BrokerSettings(cfg, overlay_path=tmp_path / "brokers_local.yaml")


async def test_status_reports_env_booleans_never_values(tmp_path, monkeypatch):
    monkeypatch.setenv("INDIA_BROKER_API_KEY", "actual-secret-key")
    monkeypatch.delenv("INDIA_BROKER_SECRET", raising=False)
    st = await make_settings(tmp_path).status()
    assert st["india"]["env"]["INDIA_BROKER_API_KEY"] is True
    assert st["india"]["env"]["INDIA_BROKER_SECRET"] is False
    assert "actual-secret-key" not in str(st)


async def test_save_allowlist_enforced(tmp_path):
    s = make_settings(tmp_path)
    with pytest.raises(PermissionError, match="allowlist"):
        await s.save("india", {"api_key": "x"}, "beef")
    with pytest.raises(PermissionError, match="allowlist"):
        await s.save("india", {"static_ip_confirmed": True}, "beef")
    with pytest.raises(ValueError, match="unknown broker"):
        await s.save("nope", {}, "beef")


async def test_save_provider_validated_and_persisted(tmp_path):
    s = make_settings(tmp_path)
    with pytest.raises(ValueError, match="provider must be one of"):
        await s.save("india", {"provider": "robinhood"}, "beef")
    out = await s.save("india", {"provider": "zerodha"}, "beef")
    assert out["saved"]["provider"] == "zerodha"
    on_disk = yaml.safe_load((tmp_path / "brokers_local.yaml").read_text())
    assert on_disk["india"]["provider"] == "zerodha"
    # overlay merges over base config in status()
    st = await s.status()
    assert st["india"]["provider"] == "zerodha"


async def test_save_never_touches_master_yaml(tmp_path):
    before = Path("config/master.yaml").read_text()
    s = make_settings(tmp_path)
    await s.save("india", {"provider": "fyers"}, "beef")
    assert Path("config/master.yaml").read_text() == before


async def test_test_unconfigured_endpoint_reports_not_crashes(tmp_path):
    s = make_settings(tmp_path)
    out = await s.test("mt5")
    assert out["ok"] is False and "no endpoint" in out["detail"]


# --------------------------------------------- runtime session gate seam

async def test_build_runtime_default_wires_market_clock(tmp_path, monkeypatch):
    """The seam that was missing for 40 modules: trading_hours in config must
    reach the router's session_open_fn without any explicit wiring."""
    from src import runtime as rtmod

    cfg = _mini_cfg()
    rt = await _mini_runtime(rtmod, cfg, tmp_path)
    assert rt.market_clock is not None
    assert rt.router.session_open_fn is not None
    # India refused at the video moment (Mon 21:01 IST == 15:31 UTC)…
    night = dt.datetime(2026, 8, 10, 15, 31, tzinfo=UTC)
    assert rt.market_clock.is_open("india", night) is False
    # …while crypto is always tradable
    assert rt.market_clock.is_open("mt5_crypto", night) is True


async def test_explicit_session_open_fn_still_wins(tmp_path):
    """Simulations/tests that pass their own session fn keep exact control —
    the clock is a DEFAULT, not an override."""
    from src import runtime as rtmod

    async def always_open(leg):
        return True

    rt = await _mini_runtime(rtmod, _mini_cfg(), tmp_path,
                             session_open_fn=always_open)
    assert rt.market_clock is None
    assert rt.router.session_open_fn is always_open


def _mini_cfg():
    import copy
    from src.core.config_loader import load_config
    return load_config("config/master.yaml")


async def _mini_runtime(rtmod, cfg, tmp_path, **kw):
    class _Conn:
        def get_openalgo(self):
            return None

        def get_mt5(self):
            return None

    async def balance():
        return 1_000_000.0

    return await rtmod.build_runtime(
        cfg, mode="paper", redis=MemRedis(), connections=_Conn(),
        kill_brokers={}, india_margin_api=None, mt5_margin_api=None,
        balance_fn=balance, data_dir=tmp_path / "runtime",
        gate_path=tmp_path / "gate_state.json", **kw)
