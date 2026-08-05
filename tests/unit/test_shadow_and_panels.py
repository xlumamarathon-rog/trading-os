"""MODULE 54 (shadow mode) + gateway panel endpoints tests."""
import httpx
import pytest

from src.ops.shadow_runner import ShadowRunner, diff_decisions
from src.strategies import get_signal


def rising_bars(n=80):
    return [{"date": f"2026-01-{k+1:02d}" if k < 28 else f"2026-02-{k-27:02d}",
             "open": 100 + k, "high": 101 + k, "low": 99 + k, "close": 100 + k}
            for k in range(n)]


def regime_fn(bars, i):
    return {"trend_state": "STRONG_TREND", "vol_regime": "NORMAL"}


# ---------- MODULE 54: shadow runner ----------

def test_shadow_records_signals_and_intents():
    sr = ShadowRunner(signal_fn=get_signal("baseline"), regime_fn=regime_fn)
    bars = rising_bars()
    d = sr.on_bar("X", bars, 79)
    assert d.signal == "buy" and d.admitted
    assert sr.intents() == [{"date": bars[79]["date"], "symbol": "X", "direction": "buy"}]


def test_shadow_guard_rejections_are_logged_not_hidden():
    sr = ShadowRunner(signal_fn=get_signal("baseline"), regime_fn=regime_fn,
                      entry_allowed_fn=lambda: (False, "portfolio_heat"))
    d = sr.on_bar("X", rising_bars(), 79)
    assert d.signal == "buy" and not d.admitted and d.reject_reason == "portfolio_heat"
    assert sr.intents() == []                       # rejected intent never routed


def test_parity_diff_flags_every_divergence_kind():
    shadow = [{"date": "d1", "symbol": "A", "direction": "buy"},
              {"date": "d1", "symbol": "B", "direction": "buy"},
              {"date": "d2", "symbol": "C", "direction": "sell"}]
    live = [{"date": "d1", "symbol": "A", "direction": "buy"},       # match
            {"date": "d2", "symbol": "C", "direction": "buy"},       # wrong side
            {"date": "d3", "symbol": "D", "direction": "buy"}]       # shadow silent
    rep = diff_decisions(shadow, live)
    assert rep.matched == 1
    assert rep.missing_live[0]["symbol"] == "B"
    assert rep.direction_mismatch[0]["symbol"] == "C"
    assert rep.missing_shadow[0]["symbol"] == "D"
    assert not rep.clean


def test_parity_clean_when_streams_identical():
    intents = [{"date": "d1", "symbol": "A", "direction": "buy"}]
    assert diff_decisions(intents, list(intents)).clean


# ---------- gateway: pnl history + config viewer ----------

@pytest.fixture
def gw(tmp_path):
    import time as _time
    from src.core.kill_switch import KillSwitch
    from src.ops.cockpit_gateway import create_gateway
    from src.ops.persistence import JsonlAuditLog

    class MemRedis:
        def __init__(self): self.store = {}
        async def get(self, k): return self.store.get(k)
        async def set(self, k, v): self.store[k] = v
        async def setex(self, k, ttl, v): self.store[k] = v
        async def delete(self, k): self.store.pop(k, None)

    ks = KillSwitch(redis=MemRedis(), brokers={}, sentinel_path=tmp_path / "h.s",
                    unlock_phrase="GO", auto_trigger_daily_loss_pct=0.03,
                    auto_trigger_var_breach=True, max_var_daily=0.02)

    async def snapshot():
        return {"equity": 1.0}

    async def pnl_fn():
        return [{"date": "2026-08-01", "equity": 1_000_000.0},
                {"date": "2026-08-04", "equity": 1_012_950.0}]

    async def config_fn():
        return {"risk_limits": {"max_risk_per_trade_pct": 0.01},
                "exit_manager": {"breakeven_at_r": 1.0}}

    app = create_gateway(tokens={"V1234567": "viewer"}, kill_switch=ks,
                         audit_log=JsonlAuditLog(tmp_path / "a.jsonl"),
                         snapshot_fn=snapshot, pnl_history_fn=pnl_fn,
                         config_view_fn=config_fn, ui_dir=None)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


async def test_pnl_history_viewer_readable(gw):
    r = await gw.get("/pnl_history", headers={"Authorization": "Bearer V1234567"})
    assert r.status_code == 200 and r.json()[1]["equity"] == 1_012_950.0
    assert (await gw.get("/pnl_history")).status_code == 401


async def test_config_view_sanitized_and_authed(gw):
    r = await gw.get("/config", headers={"Authorization": "Bearer V1234567"})
    body = r.json()
    assert r.status_code == 200
    assert body["risk_limits"]["max_risk_per_trade_pct"] == 0.01
    assert "apikey" not in str(body).lower()        # provider owns redaction
    assert (await gw.get("/config")).status_code == 401
