"""MODULE 3 — Position Sizer (spec §Phase 1, v2-corrected math).

Sizing is derived from STOP DISTANCE (risk-per-unit), not from LLM confidence:
  1. base qty      = balance · max_risk_per_trade_pct / |entry − stop|
  2. edge scaling  = × clamp(Kelly(p, b), 0, kelly_cap)   [ONLY if a calibrated
                     edge estimate exists — LLM confidence is NEVER a probability]
  3. hard cap      = ≤ balance · max_position_pct / entry
  4. VaR headroom  = × max(0, 1 − current_var / max_var_daily)
  5. gap survival  = worst loss with a gap_assumption_atr×ATR gap ≤ daily loss budget
  6. cost gate     = expected profit must exceed total costs (MODULE 40), else 0
  7. lot floor
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from src.core.config_loader import RiskLimits


@dataclass
class SizeResult:
    qty: float
    notional: float
    reason: str  # "ok" or the named zero-reason
    factors: dict = field(default_factory=dict)


def _zero(reason: str, factors: Optional[dict] = None) -> SizeResult:
    return SizeResult(qty=0.0, notional=0.0, reason=reason, factors=factors or {})


def kelly_fraction(p_win: float, payoff_ratio: float) -> float:
    """f* = p − q/b (full Kelly). Caller clamps."""
    if payoff_ratio <= 0:
        return 0.0
    return p_win - (1.0 - p_win) / payoff_ratio


def calculate_position_size(
    *,
    entry: float,
    stop: float,
    atr: float,
    balance: float,
    current_var: float,
    risk: RiskLimits,
    lot_size: float = 1.0,
    p_win: Optional[float] = None,
    payoff_ratio: Optional[float] = None,
    expected_profit_per_unit: Optional[float] = None,
    cost_fn: Optional[Callable[[float], float]] = None,
) -> SizeResult:
    finite_checks = [entry, stop, atr, balance, current_var, lot_size]
    if p_win is not None:
        finite_checks.append(p_win)
    if payoff_ratio is not None:
        finite_checks.append(payoff_ratio)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in finite_checks):
        return _zero("non_finite_inputs")     # NaN/inf from a bad feed must NEVER size an order
    if entry <= 0 or balance <= 0 or atr < 0 or lot_size <= 0:
        return _zero("invalid_inputs")

    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        return _zero("stop_at_entry")

    factors: dict = {"risk_per_unit": risk_per_unit}

    # 1. base risk budget
    qty = (balance * risk.max_risk_per_trade_pct) / risk_per_unit
    factors["base_qty"] = qty

    # 2. calibrated-edge scaling (optional)
    if p_win is not None and payoff_ratio is not None:
        k = kelly_fraction(p_win, payoff_ratio)
        k = max(0.0, min(k, risk.kelly_cap))
        factors["kelly_clamped"] = k
        if k == 0.0:
            return _zero("no_edge_kelly", factors)
        qty *= k

    # 3. hard position cap — never exceeded regardless of anything above
    cap_qty = (balance * risk.max_position_pct) / entry
    qty = min(qty, cap_qty)
    factors["cap_qty"] = cap_qty

    # 4. VaR headroom — CLAMPED to [0, 1]: headroom is a reducer, never a booster.
    # (Fuzz-caught bug: negative current_var made this multiplier 51x — a corrupt
    # VaR cache value must shrink-or-hold size, never inflate it.)
    headroom = max(0.0, min(1.0, 1.0 - (current_var / risk.max_var_daily)))
    factors["var_headroom"] = headroom
    qty *= headroom
    if qty <= 0:
        return _zero("var_at_limit", factors)

    # 5. gap survival — a gap through the stop must stay within the daily loss budget
    worst_loss_per_unit = risk_per_unit + risk.gap_assumption_atr * atr
    gap_qty = (balance * risk.max_daily_loss_pct) / worst_loss_per_unit
    qty = min(qty, gap_qty)
    factors["gap_qty"] = gap_qty

    # 6. after-cost gate (MODULE 40)
    if cost_fn is not None and expected_profit_per_unit is not None:
        total_cost = cost_fn(qty)
        factors["total_cost"] = total_cost
        if expected_profit_per_unit * qty <= total_cost:
            return _zero("no_net_edge_after_costs", factors)

    # 7. defense-in-depth: re-assert the hard cap AFTER every multiplier, then lot floor
    qty = min(qty, cap_qty)
    qty = math.floor(qty / lot_size) * lot_size
    if qty <= 0:
        return _zero("below_min_lot", factors)

    return SizeResult(qty=qty, notional=qty * entry, reason="ok", factors=factors)
