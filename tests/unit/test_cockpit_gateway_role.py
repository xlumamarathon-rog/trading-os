"""Regression tests for two cockpit-gateway findings (Aug 2026 E2E sweep):

1. `/state` did not include the caller's role, but BOTH cockpit UIs gate every
   operator control on `state.role` — against the real gateway the kill
   switch, approvals and resume controls never rendered (demo-mode-only).
   `/state` must carry the per-CALLER role, overriding any placeholder the
   snapshot provider put there.

2. There was no side-effect-free way for a client to discover its role — the
   static SPA probed by POSTing `/control/pause_entries`, which REALLY pauses
   entries on a live system as a side effect of typing a token. The gateway
   now exposes GET `/whoami` so clients never need a control probe.
"""
import time
from pathlib import Path

import httpx
import pytest

from src.core.kill_switch import KillSwitch
from src.ops.cockpit_gateway import create_gateway
from src.ops.persistence import JsonlAuditLog


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


def make_app(tmp_path: Path):
    ks = KillSwitch(redis=MemRedis(), brokers={}, sentinel_path=tmp_path / "halt.sentinel",
                    unlock_phrase="GO", auto_trigger_daily_loss_pct=0.03,
                    auto_trigger_var_breach=True, max_var_daily=0.02)

    async def snapshot():
        # deliberately carries a WRONG placeholder role — /state must override
        return {"equity": 1.0, "role": "operator-placeholder"}

    app = create_gateway(tokens={"VTOK1234": "viewer", "OTOK5678": "operator"},
                         kill_switch=ks, audit_log=JsonlAuditLog(tmp_path / "a.jsonl"),
                         snapshot_fn=snapshot, ui_dir=None)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


async def test_state_carries_per_caller_role(tmp_path):
    c = make_app(tmp_path)
    r = await c.get("/state", headers={"Authorization": "Bearer VTOK1234"})
    assert r.status_code == 200 and r.json()["role"] == "viewer"
    r = await c.get("/state", headers={"Authorization": "Bearer OTOK5678"})
    assert r.json()["role"] == "operator"          # placeholder overridden


async def test_whoami_is_side_effect_free_role_probe(tmp_path):
    c = make_app(tmp_path)
    r = await c.get("/whoami", headers={"Authorization": "Bearer VTOK1234"})
    assert r.status_code == 200 and r.json() == {"role": "viewer"}
    r = await c.get("/whoami", headers={"Authorization": "Bearer OTOK5678"})
    assert r.json() == {"role": "operator"}
    r = await c.get("/whoami")
    assert r.status_code == 401                    # still authenticated


async def test_whoami_requires_valid_token(tmp_path):
    c = make_app(tmp_path)
    r = await c.get("/whoami", headers={"Authorization": "Bearer NOPE"})
    assert r.status_code == 401
