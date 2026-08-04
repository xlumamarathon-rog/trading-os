"""MODULE 44 — Cockpit Gateway (spec §Phase 3, NEW in v2).

The ONLY door between client apps (MODULE 45 web/Tauri cockpit) and the system.
- Token auth on every route; RBAC: viewer (read) / operator (controls).
- Control actions: kill-switch trigger + unlock (confirmation phrase), pause
  entries, approvals — every one double-confirmed and audit-logged with actor.
- Clients NEVER reach brokers or internal workers; losing a client changes
  nothing about system safety (spec §12.11).
State/stream endpoints serve snapshots; the WS event bus rides the same
snapshot provider (deltas on the VPS deployment).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel


class ControlRequest(BaseModel):
    confirm: str = ""              # confirmation phrase for destructive actions
    reason: str = ""


def create_gateway(
    *,
    tokens: dict[str, str],                    # token -> role ("viewer" | "operator")
    kill_switch,
    audit_log,
    snapshot_fn: Callable,                     # -> dict (positions, pnl, var, regime...)
    pause_entries_fn: Optional[Callable] = None,
    approvals_fn: Optional[Callable] = None,   # -> list of pending approvals
    approve_fn: Optional[Callable] = None,     # (approval_id, actor) -> None
    resume_entries_fn: Optional[Callable] = None,  # (actor) -> None — safe-start release
    ui_dir: Optional[str] = "cockpit/web",     # M45 SPA (zero-build, static)
) -> FastAPI:
    app = FastAPI(title="Trading OS Cockpit Gateway", docs_url=None, redoc_url=None)

    # ---------- M45 web UI (public shell; every DATA/CONTROL call inside it
    # still requires the Bearer token) ----------
    ui_path = Path(ui_dir) if ui_dir else None
    if ui_path is not None and ui_path.exists():
        @app.get("/ui")
        async def ui_index():
            return FileResponse(ui_path / "index.html")

        @app.get("/ui/{asset}")
        async def ui_asset(asset: str):
            target = (ui_path / asset).resolve()
            if not str(target).startswith(str(ui_path.resolve())) or not target.is_file():
                raise HTTPException(status_code=404)
            return FileResponse(target)

    def authed(authorization: str = Header(default="")) -> dict:
        token = authorization.removeprefix("Bearer ").strip()
        role = tokens.get(token)
        if role is None:
            raise HTTPException(status_code=401, detail="invalid token")
        return {"role": role, "token_tail": token[-4:] if len(token) >= 4 else "****"}

    def operator_only(actor: dict = Depends(authed)) -> dict:
        if actor["role"] != "operator":
            raise HTTPException(status_code=403, detail="operator role required")
        return actor

    # ---------- read side (viewer+) ----------

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/state")
    async def state(actor: dict = Depends(authed)):
        snap = await snapshot_fn()
        snap["halted"] = await kill_switch.is_halted()
        # per-CALLER role, not whatever placeholder the snapshot carries —
        # the cockpits gate every operator control on this field
        snap["role"] = actor["role"]
        return snap

    @app.get("/whoami")
    async def whoami(actor: dict = Depends(authed)):
        """Side-effect-free role probe for clients. Never use a control
        endpoint to discover role — that fires a real state change."""
        return {"role": actor["role"]}

    @app.get("/approvals")
    async def approvals(actor: dict = Depends(authed)):
        return await approvals_fn() if approvals_fn else []

    # ---------- control side (operator only, audited) ----------

    async def _audit(actor: dict, action: str, detail: dict) -> None:
        audit_log.append({"type": "cockpit_control", "action": action,
                          "actor_token_tail": actor["token_tail"], **detail})

    @app.post("/control/kill")
    async def kill(req: ControlRequest, actor: dict = Depends(operator_only)):
        if req.confirm != "KILL ALL POSITIONS":
            raise HTTPException(status_code=400,
                                detail='confirmation phrase required: "KILL ALL POSITIONS"')
        report = await kill_switch.kill_all(f"cockpit: {req.reason or 'manual'}")
        await _audit(actor, "kill_all", {"reason": req.reason,
                                         "orders_cancelled": len(report.orders_cancelled),
                                         "positions_closed": len(report.positions_closed)})
        return report.to_dict()

    @app.post("/control/unlock")
    async def unlock(req: ControlRequest, actor: dict = Depends(operator_only)):
        try:
            await kill_switch.unlock(req.confirm)
        except PermissionError as exc:
            await _audit(actor, "unlock_refused", {"why": str(exc)})
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        await _audit(actor, "unlock", {})
        return {"halted": await kill_switch.is_halted()}

    @app.post("/control/pause_entries")
    async def pause(req: ControlRequest, actor: dict = Depends(operator_only)):
        if pause_entries_fn is None:
            raise HTTPException(status_code=501, detail="not wired")
        await pause_entries_fn(req.reason or "cockpit manual pause")
        await _audit(actor, "pause_entries", {"reason": req.reason})
        return {"paused": True}

    @app.post("/control/resume_entries")
    async def resume(req: ControlRequest, actor: dict = Depends(operator_only)):
        """Safe-start release: a fresh LIVE process trades only after this click."""
        if resume_entries_fn is None:
            raise HTTPException(status_code=501, detail="not wired")
        await resume_entries_fn(actor["token_tail"])
        await _audit(actor, "resume_entries", {"reason": req.reason})
        return {"entries_resumed": True}

    @app.post("/control/approve/{approval_id}")
    async def approve(approval_id: str, req: ControlRequest,
                      actor: dict = Depends(operator_only)):
        if approve_fn is None:
            raise HTTPException(status_code=501, detail="not wired")
        await approve_fn(approval_id, actor["token_tail"])
        await _audit(actor, "approve", {"approval_id": approval_id})
        return {"approved": approval_id}

    return app
