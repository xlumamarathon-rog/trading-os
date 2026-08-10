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

import inspect
from pathlib import Path
from typing import Callable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel


async def _maybe_await(result):
    """Injected providers may be sync OR async — the same tolerance the
    guard stack and WorkerSupervisor learned in the Aug-6 seam hunt. A
    natural assembly wires plain lambdas; awaiting a list is a TypeError
    that only fires in production wiring, never in unit tests that pass
    async mocks."""
    if inspect.isawaitable(result):
        return await result
    return result

# Defense-in-depth redaction net for the /config endpoint. The provider is
# supposed to hand us an already-sanitized view, but a careless deployer who
# wires the raw config would otherwise leak broker keys / unlock phrases to
# any viewer token. We strip anything whose key looks secret, at every depth,
# so the gateway CANNOT serve a secret even if the provider slips.
_SECRET_KEY_HINTS = ("apikey", "api_key", "secret", "password", "passwd",
                     "token", "unlock_phrase", "private", "credential")


def sanitize_config_view(value):
    """Recursively redact secret-looking keys from a config view. Returns a
    new structure; never mutates the input."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if any(hint in str(k).lower() for hint in _SECRET_KEY_HINTS):
                out[k] = "***REDACTED***"
            else:
                out[k] = sanitize_config_view(v)
        return out
    if isinstance(value, (list, tuple)):
        return [sanitize_config_view(v) for v in value]
    return value


class ControlRequest(BaseModel):
    confirm: str = ""              # confirmation phrase for destructive actions
    reason: str = ""


class BrokerSaveRequest(BaseModel):
    """Body for /brokers/test and /brokers/save (MODULE 59). Must live at
    module level: `from __future__ import annotations` stringifies the
    endpoint annotations and FastAPI resolves them against module globals —
    a function-local model silently degrades to a query param (422s)."""
    broker: str = ""               # "india" | "mt5"
    settings: dict = {}


class ClosePositionRequest(BaseModel):
    """Body for /control/close_position — close ONE open position."""
    symbol: str = ""
    confirm: str = ""              # must equal "CLOSE <symbol>"
    reason: str = ""


class OrderTicketRequest(BaseModel):
    """Body for /control/order — a MANUAL entry through the FULL router
    (kill switch, session clock, guards, sizing, margin — no shortcuts)."""
    symbol: str = ""
    direction: str = ""            # "buy" | "sell"
    stop: float = 0.0
    qty: float = 0.0               # 0 = let the position sizer decide
    confirm: str = ""              # must equal "PLACE <symbol>"
    reason: str = ""


class ResearchRunRequest(BaseModel):
    """Body for /research/run — allowlisted strategy × dataset backtest."""
    strategy: str = ""
    dataset: str = ""


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
    trades_fn: Optional[Callable] = None,      # -> recent closed trades (blotter)
    pnl_history_fn: Optional[Callable] = None,  # -> [{date, equity}] daily closes
    config_view_fn: Optional[Callable] = None,  # -> SANITIZED running config dict
    ui_dir: Optional[str] = "cockpit/web",     # M45 SPA (zero-build, static)
    # ---- cockpit v2 (MODULE 59) — every provider optional: None keeps the
    # endpoint alive but empty, so older assemblies work unchanged.
    market_clock=None,                          # MODULE 58 clock -> GET /clock
    portfolio_fn: Optional[Callable] = None,    # -> portfolio snapshot (GET /portfolio)
    history_fn: Optional[Callable] = None,      # (filters) -> closed trades (GET /history)
    brokers_status_fn: Optional[Callable] = None,  # -> broker cards, NO secrets
    broker_test_fn: Optional[Callable] = None,  # (name) -> {ok, detail}
    broker_save_fn: Optional[Callable] = None,  # (name, settings, actor) -> saved
    # ---- cockpit v2.1 (MODULE 64) — trade controls + research lab
    candles_fn: Optional[Callable] = None,      # (symbol, n) -> [{ts,o,h,l,c}]
    close_position_fn: Optional[Callable] = None,   # (symbol, reason) -> dict
    place_order_fn: Optional[Callable] = None,  # (ticket dict, actor) -> dict
    research_lab=None,                          # MODULE 63 ResearchLab
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
            base = ui_path.resolve()
            target = (ui_path / asset).resolve()
            # is_relative_to (not str.startswith): a plain prefix check lets a
            # SIBLING dir sharing the prefix through — e.g. base "…/web" would
            # accept "…/web-secret/…". Confirmed traversal bypass, now closed.
            if not target.is_relative_to(base) or not target.is_file():
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
        snap = await _maybe_await(snapshot_fn())
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
        return await _maybe_await(approvals_fn()) if approvals_fn else []

    @app.get("/trades")
    async def trades(actor: dict = Depends(authed)):
        """Trade blotter (viewer+): recent closed trades with R, exit reason,
        MFE captured — the operator's ground-truth view of exit quality."""
        return await _maybe_await(trades_fn()) if trades_fn else []

    @app.get("/pnl_history")
    async def pnl_history(actor: dict = Depends(authed)):
        """Daily equity closes (viewer+) — feeds the P&L calendar."""
        return await _maybe_await(pnl_history_fn()) if pnl_history_fn else []

    @app.get("/config")
    async def config_view(actor: dict = Depends(authed)):
        """SANITIZED running config (viewer+). The provider owns redaction,
        but we ALSO redact defensively here so a careless provider cannot
        leak secrets to a viewer token."""
        raw = await _maybe_await(config_view_fn()) if config_view_fn else {}
        return sanitize_config_view(raw)

    # ---------- cockpit v2 read side (MODULE 59, viewer+) ----------

    @app.get("/clock")
    async def clock(actor: dict = Depends(authed)):
        """Per-leg market session status (MODULE 58). The cockpit uses this to
        freeze closed-market charts and badge legs OPEN/CLOSED — no more
        india candles ticking at 21:00 IST."""
        if market_clock is None:
            return {"now_utc": None, "legs": {}}
        return market_clock.status()

    @app.get("/portfolio")
    async def portfolio(actor: dict = Depends(authed)):
        """Portfolio view: open positions w/ exit states, per-leg exposure,
        realized + unrealized split — the operator's book at a glance."""
        return await _maybe_await(portfolio_fn()) if portfolio_fn else {}

    @app.get("/history")
    async def history(actor: dict = Depends(authed), symbol: str = "",
                      leg: str = "", exit_reason: str = "", since: str = "",
                      until: str = "", limit: int = 500):
        """Closed-trade screener (viewer+): filterable trade history. All
        filters optional; server caps the row count."""
        if history_fn is None:
            return []
        rows = await _maybe_await(history_fn())
        def keep(r):
            if symbol and str(r.get("symbol", "")).upper() != symbol.upper():
                return False
            if leg and str(r.get("leg", "")) != leg:
                return False
            if exit_reason and str(r.get("exit_reason", "")) != exit_reason:
                return False
            d = str(r.get("date", ""))
            if since and d and d < since:
                return False
            if until and d and d > until:
                return False
            return True
        return [r for r in rows if keep(r)][: max(1, min(int(limit), 2000))]

    @app.get("/brokers")
    async def brokers(actor: dict = Depends(authed)):
        """Broker connection cards: provider, reachability, which credential
        env-vars are SET (booleans only — never values). Defensively
        sanitized so a careless provider cannot leak a secret."""
        raw = await _maybe_await(brokers_status_fn()) if brokers_status_fn else {}
        return sanitize_config_view(raw)

    @app.get("/candles")
    async def candles(actor: dict = Depends(authed), symbol: str = "",
                      n: int = 96):
        """Real price candles from the wired feed (replay-of-real-history in
        the bundled paper assembly; broker quotes on the VPS). Never
        synthetic client-side randomness again."""
        if candles_fn is None or not symbol:
            return []
        return await _maybe_await(candles_fn(symbol, max(2, min(int(n), 500))))

    @app.get("/research/runs")
    async def research_runs(actor: dict = Depends(authed)):
        """Backtest catalog (viewer+): every run is a real research_replay
        subprocess; results carry reconciliation + audit-chain status."""
        if research_lab is None:
            return {"runs": [], "options": {"strategies": [], "datasets": [],
                                            "busy": False}}
        return {"runs": research_lab.runs(), "options": research_lab.options()}

    # ---------- control side (operator only, audited) ----------

    async def _audit(actor: dict, action: str, detail: dict) -> None:
        audit_log.append({"type": "cockpit_control", "action": action,
                          "actor_token_tail": actor["token_tail"], **detail})

    @app.post("/brokers/test")
    async def broker_test(req: BrokerSaveRequest,
                          actor: dict = Depends(operator_only)):
        """Ping a configured broker path (OpenAlgo base_url / MT5 exec
        service). Never sends credentials from the request."""
        if broker_test_fn is None:
            raise HTTPException(status_code=501, detail="not wired")
        result = await _maybe_await(broker_test_fn(req.broker))
        await _audit(actor, "broker_test", {"broker": req.broker,
                                            "ok": bool(result.get("ok"))})
        return result

    @app.post("/brokers/save")
    async def broker_save(req: BrokerSaveRequest,
                          actor: dict = Depends(operator_only)):
        """Save non-secret broker settings (provider, base_url, exec URL,
        symbol classes) to the local overlay. The provider fn owns the
        allowlist; the gateway additionally refuses gate-adjacent keys so
        this endpoint STRUCTURALLY cannot weaken the live gate (spec §12)."""
        if broker_save_fn is None:
            raise HTTPException(status_code=501, detail="not wired")
        forbidden = {"static_ip_confirmed", "human_ack", "sebi_checks_passed",
                     "paper_days_completed", "clean_reconciliation_streak"}
        bad = forbidden & set(map(str, req.settings))
        if bad:
            await _audit(actor, "broker_save_refused",
                         {"broker": req.broker, "keys": sorted(bad)})
            raise HTTPException(
                status_code=403,
                detail=f"gate-controlled keys refused: {sorted(bad)} — "
                       "these are earned on the VPS, never set from the UI")
        saved = await _maybe_await(broker_save_fn(req.broker, req.settings,
                                               actor["token_tail"]))
        await _audit(actor, "broker_save", {"broker": req.broker,
                                            "keys": sorted(map(str, req.settings))})
        return sanitize_config_view(saved)

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
        await _maybe_await(pause_entries_fn(req.reason or "cockpit manual pause"))
        await _audit(actor, "pause_entries", {"reason": req.reason})
        return {"paused": True}

    @app.post("/control/resume_entries")
    async def resume(req: ControlRequest, actor: dict = Depends(operator_only)):
        """Safe-start release: a fresh LIVE process trades only after this click."""
        if resume_entries_fn is None:
            raise HTTPException(status_code=501, detail="not wired")
        await _maybe_await(resume_entries_fn(actor["token_tail"]))
        await _audit(actor, "resume_entries", {"reason": req.reason})
        return {"entries_resumed": True}

    @app.post("/control/approve/{approval_id}")
    async def approve(approval_id: str, req: ControlRequest,
                      actor: dict = Depends(operator_only)):
        if approve_fn is None:
            raise HTTPException(status_code=501, detail="not wired")
        await _maybe_await(approve_fn(approval_id, actor["token_tail"]))
        await _audit(actor, "approve", {"approval_id": approval_id})
        return {"approved": approval_id}

    # ---------- trade controls (MODULE 64, operator only, audited) ----------

    @app.post("/control/close_position")
    async def close_position(req: ClosePositionRequest,
                             actor: dict = Depends(operator_only)):
        """Close ONE position through the real exit path (cancel resting
        stop, market-out). Typed per-symbol confirmation: closing RELIANCE
        requires the phrase CLOSE RELIANCE — cheap to type under stress,
        impossible to fat-finger onto the wrong row."""
        if close_position_fn is None:
            raise HTTPException(status_code=501, detail="not wired")
        expected = f"CLOSE {req.symbol}".strip()
        if not req.symbol or req.confirm != expected:
            raise HTTPException(status_code=400,
                                detail=f'confirmation phrase required: "{expected}"')
        try:
            result = await _maybe_await(close_position_fn(
                req.symbol, req.reason or "cockpit manual close"))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await _audit(actor, "close_position",
                     {"symbol": req.symbol, "reason": req.reason})
        return result

    @app.post("/control/order")
    async def place_order(req: OrderTicketRequest,
                          actor: dict = Depends(operator_only)):
        """Manual order ticket. The gateway does ZERO sizing or validation
        beyond intent confirmation — the ticket goes through the FULL
        OrderRouter door: kill switch, anomaly pause, session clock,
        portfolio guards, position sizer, margin. A rejection reason comes
        back verbatim; nothing is silently dropped."""
        if place_order_fn is None:
            raise HTTPException(status_code=501, detail="not wired")
        expected = f"PLACE {req.symbol}".strip()
        if not req.symbol or req.confirm != expected:
            raise HTTPException(status_code=400,
                                detail=f'confirmation phrase required: "{expected}"')
        if req.direction not in ("buy", "sell"):
            raise HTTPException(status_code=400, detail="direction must be buy|sell")
        if req.stop <= 0:
            raise HTTPException(status_code=400, detail="a protective stop is mandatory")
        result = await _maybe_await(place_order_fn(
            {"symbol": req.symbol, "direction": req.direction,
             "stop": req.stop, "qty": req.qty}, actor["token_tail"]))
        await _audit(actor, "place_order",
                     {"symbol": req.symbol, "direction": req.direction,
                      "accepted": bool(result.get("accepted"))})
        return result

    @app.post("/research/run")
    async def research_run(req: ResearchRunRequest,
                           actor: dict = Depends(operator_only)):
        """Launch an allowlisted backtest on the certified harness."""
        if research_lab is None:
            raise HTTPException(status_code=501, detail="not wired")
        try:
            meta = await research_lab.start(req.strategy, req.dataset,
                                            actor["token_tail"])
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await _audit(actor, "research_run",
                     {"strategy": req.strategy, "dataset": req.dataset,
                      "run_id": meta["id"]})
        return meta

    return app
