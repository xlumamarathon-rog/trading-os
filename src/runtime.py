"""Live/Paper Runtime Assembly (Wave 12 — live-trading readiness).

ONE function builds the ENTIRE component graph from config for either mode:
    runtime = build_runtime(cfg, mode="paper"|"live", ...)
The only differences between the modes:
  - live passes assert_live_allowed() first (evidence gate — no bypass)
  - live boots SAFE-STARTED: entries are PAUSED until an operator explicitly
    resumes from the cockpit (a fresh live process never trades on its own)
  - live applies the RAMP: for the first live_ramp.days, max_position_pct is
    capped to live_ramp.max_position_pct (code-enforced small size, not advice)
Everything else — router, exits, guard, workers, gateway — is the SAME code
that the paper evidence was earned on.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.app import GATE_FILE, WorkerSupervisor, assert_live_allowed
from src.core.guard_stack import make_portfolio_guard
from src.core.kill_switch import KillSwitch
from src.core.margin_checker import MarginChecker
from src.core.order_router import OrderRouter
from src.exits.adapters.composite import CompositeStopAdapter
from src.exits.adapters.india_stops import IndiaStopAdapter
from src.exits.adapters.mt5_stops import Mt5StopAdapter
from src.exits.exit_manager import ExitManager
from src.intel.anomaly_guard import PAUSE_ENTRIES_KEY, AnomalyGuard
from src.ops.persistence import JsonlAuditLog

LIVE_RAMP_DEFAULTS = {"days": 5, "max_position_pct": 0.01}


class RampedRisk:
    """Proxy over RiskLimits: during the live ramp, position cap is the ramp cap."""

    def __init__(self, risk, ramp_cap: Optional[float]) -> None:
        self._risk = risk
        self._cap = ramp_cap

    def __getattr__(self, name):
        value = getattr(self._risk, name)
        if name == "max_position_pct" and self._cap is not None:
            return min(value, self._cap)
        return value


def ramp_cap_for(cfg, gate_path: str | Path, mode: str) -> Optional[float]:
    if mode != "live":
        return None
    ramp = dict(LIVE_RAMP_DEFAULTS)
    ramp.update(cfg.model_extra.get("live_ramp", {}) or {})
    gate = json.loads(Path(gate_path).read_text()) if Path(gate_path).exists() else {}
    live_days = int(gate.get("live_days_completed", 0))
    return float(ramp["max_position_pct"]) if live_days < int(ramp["days"]) else None


@dataclass
class Runtime:
    mode: str
    router: OrderRouter
    exit_mgr: ExitManager
    guard: AnomalyGuard
    kill_switch: KillSwitch
    audit: JsonlAuditLog
    supervisor: WorkerSupervisor
    redis: object
    risk: object
    safe_started: bool = False
    boot_log: list = field(default_factory=list)
    budget: object = None            # MODULE 55 ring-fenced budget (None = not configured)
    session_guard: object = None     # MODULE 48 day-P&L guard
    heat_mgr: object = None          # MODULE 46 portfolio heat cap


async def build_runtime(cfg, *, mode: str, redis, connections, kill_brokers: dict,
                        india_margin_api, mt5_margin_api, balance_fn,
                        data_dir: str | Path = "data/runtime",
                        gate_path: str | Path = GATE_FILE,
                        signal_valid_fn=None, band_check_fn=None,
                        session_open_fn=None, alert_fn=None,
                        budget=None, session_guard=None, heat_mgr=None,
                        positions_fn=None, equity_fn=None,
                        india_apikey: str = "", algo_id: str = "") -> Runtime:
    if mode not in ("paper", "live"):
        raise ValueError("mode must be paper|live")
    if mode == "live":
        assert_live_allowed(cfg, gate_path)          # evidence gate — raises, no bypass

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    audit = JsonlAuditLog(data_dir / f"audit_{mode}.jsonl")

    ks = KillSwitch(
        redis=redis, brokers=kill_brokers,
        sentinel_path=data_dir / "halt.sentinel",
        unlock_phrase=cfg.kill_switch.unlock_phrase or "SET-UNLOCK-PHRASE",
        auto_trigger_daily_loss_pct=cfg.kill_switch.auto_trigger_daily_loss_pct,
        auto_trigger_var_breach=cfg.kill_switch.auto_trigger_var_breach,
        max_var_daily=cfg.risk_limits.max_var_daily,
        alert_fn=alert_fn,
        audit_fn=lambda r: audit.append({"type": "kill", **r}),
    )
    ag_cfg = cfg.model_extra["anomaly_guard"]
    guard = AnomalyGuard(
        redis=redis, velocity_sigma=ag_cfg["velocity_sigma"],
        spread_blowout_mult=ag_cfg["spread_blowout_mult"],
        volume_spike_mult=ag_cfg["volume_spike_mult"],
        cooloff_minutes=ag_cfg["cooloff_minutes"], alert_fn=alert_fn)

    exit_mgr = ExitManager(cfg.model_extra["exit_manager"], CompositeStopAdapter(
        india_adapter=IndiaStopAdapter(connections.get_openalgo(),
                                       apikey=india_apikey, algo_id=algo_id),
        mt5_adapter=Mt5StopAdapter(connections.get_mt5())))

    risk = RampedRisk(cfg.risk_limits, ramp_cap_for(cfg, gate_path, mode))

    # Portfolio-level guard stack (MODULES 46/48/55) — the ONE gate every NEW
    # entry must clear before sizing. Aug 6 seam hunt: the modules, the router
    # hook and the composition all existed, but the production assembly never
    # wired them together (only research scripts and tests did). None of the
    # three configured -> no guard, exact legacy behavior.
    portfolio_guard_fn = None
    if budget is not None or session_guard is not None or heat_mgr is not None:
        portfolio_guard_fn = make_portfolio_guard(
            equity_fn=equity_fn or balance_fn,       # async ok — guard tolerates both
            risk_limits=risk,                        # ramped view, same as sizing
            budget=budget, session_guard=session_guard, heat_mgr=heat_mgr,
            positions_fn=positions_fn or (lambda: exit_mgr.positions.values()))

    router = OrderRouter(
        config=cfg, kill_switch=ks, anomaly_guard=guard,
        margin_checker=MarginChecker(cfg.risk_limits, india_api=india_margin_api,
                                     mt5_api=mt5_margin_api),
        connections=connections, redis=redis, balance_fn=balance_fn,
        signal_valid_fn=signal_valid_fn, band_check_fn=band_check_fn,
        session_open_fn=session_open_fn,
        audit_fn=lambda row: audit.append({"type": "order", **row}),
        portfolio_guard_fn=portfolio_guard_fn,
        on_filled=None)
    router.cfg = _with_risk(cfg, risk)               # ramped cap flows into sizing

    runtime = Runtime(mode=mode, router=router, exit_mgr=exit_mgr, guard=guard,
                      kill_switch=ks, audit=audit, redis=redis, risk=risk,
                      budget=budget, session_guard=session_guard, heat_mgr=heat_mgr,
                      supervisor=WorkerSupervisor(redis=redis, alert_fn=alert_fn))

    if mode == "live":
        # SAFE-START: a fresh live process NEVER trades until a human resumes.
        await redis.set(PAUSE_ENTRIES_KEY, "1")       # no TTL — explicit resume only
        runtime.safe_started = True
        audit.append({"type": "boot", "mode": "live",
                      "safe_start": "entries paused until operator resume",
                      "ramp_cap": risk.max_position_pct, "at": time.time()})
    else:
        audit.append({"type": "boot", "mode": "paper", "at": time.time()})
    return runtime


class _CfgView:
    """cfg proxy replacing risk_limits with the ramped view (read-only use)."""

    def __init__(self, cfg, risk):
        self._cfg = cfg
        self.risk_limits = risk

    def __getattr__(self, name):
        return getattr(self._cfg, name)


def _with_risk(cfg, risk):
    return _CfgView(cfg, risk)


async def resume_entries(redis, audit: JsonlAuditLog, actor: str) -> None:
    """Operator action (cockpit /control/resume_entries) — the safe-start release."""
    await redis.delete(PAUSE_ENTRIES_KEY)
    audit.append({"type": "cockpit_control", "action": "resume_entries", "actor": actor})
