"""MODULE 18 — Config loader (spec §3).

Single typed source of truth for every tunable. Rules enforced here:
- ${ENV} references resolved from environment; unresolved names are recorded
  (never silently defaulted for secrets).
- `redis_unreachable_behavior` accepts ONLY "halt" (fail-closed, spec §12.1).
- Validation errors are fatal at startup — a misconfigured system must not boot.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

_ENV_RE = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


class RiskLimits(BaseModel):
    model_config = ConfigDict(extra="allow")
    max_var_daily: float = Field(gt=0, lt=1)
    max_position_pct: float = Field(gt=0, lt=1)
    max_risk_per_trade_pct: float = Field(gt=0, lt=1)
    max_daily_loss_pct: float = Field(gt=0, lt=1)
    max_sector_exposure_pct: float = Field(gt=0, le=1)
    gap_assumption_atr: float = Field(ge=0)
    kelly_cap: float = Field(ge=0, le=1)
    margin_buffer_india: float = Field(ge=0, lt=1)
    mt5_min_free_margin_pct: float = Field(ge=0, lt=1)


class KillSwitchCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    auto_trigger_daily_loss_pct: float = Field(gt=0, lt=1)
    auto_trigger_var_breach: bool
    # Fail-closed is the ONLY permitted behavior (spec §12.1).
    redis_unreachable_behavior: Literal["halt"]
    unlock_phrase: str = ""
    sentinel_file: str = "/tmp/trading_halted.sentinel"


class IndiaCosts(BaseModel):
    model_config = ConfigDict(extra="allow")
    brokerage_flat: float = Field(ge=0)
    stt_delivery_pct: float = Field(ge=0)
    stt_intraday_sell_pct: float = Field(ge=0)
    exchange_txn_pct: float = Field(ge=0)
    stamp_duty_pct: float = Field(ge=0)
    gst_pct: float = Field(ge=0)


class ImpactModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    y_coefficient: float = Field(gt=0)


class ExecutionCosts(BaseModel):
    model_config = ConfigDict(extra="allow")
    india: IndiaCosts
    impact_model: ImpactModel


class MasterConfig(BaseModel):
    """Typed criticals; everything else rides along via extra="allow"."""

    model_config = ConfigDict(extra="allow")
    risk_limits: RiskLimits
    kill_switch: KillSwitchCfg
    execution_costs: ExecutionCosts
    unresolved_env: list[str] = Field(default_factory=list)


def _resolve_env(value: Any, unresolved: list[str]) -> Any:
    if isinstance(value, str):

        def _sub(m: re.Match[str]) -> str:
            name = m.group(1)
            got = os.environ.get(name)
            if got is None:
                unresolved.append(name)
                return ""
            return got

        return _ENV_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _resolve_env(v, unresolved) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v, unresolved) for v in value]
    return value


def load_config(path: str | Path) -> MasterConfig:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError("master.yaml must be a top-level mapping")
    unresolved: list[str] = []
    resolved = _resolve_env(raw, unresolved)
    cfg = MasterConfig(**resolved)
    cfg.unresolved_env = sorted(set(unresolved))
    return cfg
