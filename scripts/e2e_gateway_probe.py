#!/usr/bin/env python3
"""E2E probe of the cockpit gateway: auth, RBAC, typed confirmations,
kill/unlock drill, pause/resume, approvals, audit trail, path traversal."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

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


PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {detail}")


async def main():
    out = Path("/tmp/e2e_gateway")
    out.mkdir(exist_ok=True)
    (out / "halt.sentinel").unlink(missing_ok=True)
    redis = MemRedis()
    ks = KillSwitch(redis=redis, brokers={}, sentinel_path=out / "halt.sentinel",
                    unlock_phrase="RESUME TRADING NOW", auto_trigger_daily_loss_pct=0.03,
                    auto_trigger_var_breach=True, max_var_daily=0.02)
    audit = JsonlAuditLog(out / "audit.jsonl")
    paused, approvals = [], [{"id": "AP-1", "kind": "rule_change", "detail": "demo"}]
    approved = []

    async def snapshot():
        return {"equity": 1_000_000.0, "positions": [], "var": 0.005, "regime": "RANGE"}

    async def pause_fn(reason):
        paused.append(reason)

    async def resume_fn(actor):
        paused.clear()

    async def approvals_fn():
        return approvals

    async def approve_fn(aid, actor):
        approved.append((aid, actor))

    app = create_gateway(tokens={"VIEW1234": "viewer", "OPER5678": "operator"},
                         kill_switch=ks, audit_log=audit, snapshot_fn=snapshot,
                         pause_entries_fn=pause_fn, approvals_fn=approvals_fn,
                         approve_fn=approve_fn, resume_entries_fn=resume_fn,
                         ui_dir="cockpit/web")
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")
    V = {"Authorization": "Bearer VIEW1234"}
    O = {"Authorization": "Bearer OPER5678"}

    r = await c.get("/health")
    check("health open (no auth)", r.status_code == 200)
    r = await c.get("/state")
    check("state w/o token -> 401", r.status_code == 401, r.text)
    r = await c.get("/state", headers={"Authorization": "Bearer WRONG"})
    check("state bad token -> 401", r.status_code == 401)
    r = await c.get("/state", headers=V)
    check("viewer reads state", r.status_code == 200 and r.json()["halted"] is False)
    r = await c.get("/approvals", headers=V)
    check("viewer reads approvals", r.status_code == 200 and r.json()[0]["id"] == "AP-1")

    # RBAC: viewer provably cannot control
    for path in ["/control/kill", "/control/unlock", "/control/pause_entries",
                 "/control/resume_entries", "/control/approve/AP-1"]:
        r = await c.post(path, headers=V, json={"confirm": "KILL ALL POSITIONS"})
        check(f"viewer blocked on {path} -> 403", r.status_code == 403, r.text)

    # typed confirmation on kill
    r = await c.post("/control/kill", headers=O, json={"confirm": "kill all positions"})
    check("wrong-case confirm refused -> 400", r.status_code == 400)
    r = await c.post("/control/kill", headers=O, json={"confirm": "", "reason": "x"})
    check("empty confirm refused -> 400", r.status_code == 400)
    r = await c.post("/control/kill", headers=O,
                     json={"confirm": "KILL ALL POSITIONS", "reason": "e2e drill"})
    check("kill with exact phrase -> 200", r.status_code == 200, r.text)
    r = await c.get("/state", headers=V)
    check("state shows halted", r.json()["halted"] is True)

    # unlock drill
    r = await c.post("/control/unlock", headers=O, json={"confirm": "wrong phrase"})
    check("unlock wrong phrase -> 403", r.status_code == 403)
    r = await c.post("/control/unlock", headers=O, json={"confirm": "RESUME TRADING NOW"})
    check("unlock exact phrase -> 200 unhalted",
          r.status_code == 200 and r.json()["halted"] is False, r.text)

    # pause / resume + approvals
    r = await c.post("/control/pause_entries", headers=O, json={"reason": "e2e"})
    check("operator pause entries", r.status_code == 200 and paused == ["e2e"])
    r = await c.post("/control/resume_entries", headers=O, json={})
    check("operator resume entries", r.status_code == 200 and paused == [])
    r = await c.post("/control/approve/AP-1", headers=O, json={})
    check("operator approve", r.status_code == 200 and approved and approved[0][0] == "AP-1")

    # audit trail: every control action recorded with actor
    actions = [row["action"] for row in audit.rows if row.get("type") == "cockpit_control"]
    for a in ["kill_all", "unlock_refused", "unlock", "pause_entries",
              "resume_entries", "approve"]:
        check(f"audit contains {a}", a in actions, str(actions))
    check("audit chain verifies", audit.verify_chain())

    # path traversal on /ui
    r = await c.get("/ui/../pyproject.toml")
    check("ui path traversal blocked", r.status_code in (404, 400), f"got {r.status_code}")
    r = await c.get("/ui/%2e%2e/pyproject.toml")
    check("ui encoded traversal blocked", r.status_code in (404, 400), f"got {r.status_code}")

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
