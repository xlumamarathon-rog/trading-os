"""MODULE 39 — Dealer Gamma Exposure map from an option chain (spec §Phase 2, NEW).

GEX per strike = Gamma * OI * multiplier * S^2 * 0.01  (per 1% move convention)
Dealer convention: +call gamma (dealers long calls they sold? standard retail
convention: calls +, puts -). Net GEX < 0 => dealers short gamma => hedging
AMPLIFIES moves; > 0 => DAMPENS (pinning near big strikes at expiry).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GexResult:
    net_gex: float
    regime: str                  # "amplify" | "dampen"
    by_strike: dict
    pin_candidates: list         # strikes with largest |GEX|, descending


def compute_gex(chain: list[dict], spot: float, multiplier: float = 1.0,
                top_n_pins: int = 3) -> GexResult:
    """chain rows: {strike, call_gamma, call_oi, put_gamma, put_oi} (per-unit gammas)."""
    if spot <= 0:
        raise ValueError("spot must be positive")
    by_strike: dict = {}
    for row in chain:
        k = row["strike"]
        call_gex = row.get("call_gamma", 0.0) * row.get("call_oi", 0.0)
        put_gex = -row.get("put_gamma", 0.0) * row.get("put_oi", 0.0)
        by_strike[k] = (call_gex + put_gex) * multiplier * spot * spot * 0.01
    net = sum(by_strike.values())
    pins = sorted(by_strike, key=lambda k: abs(by_strike[k]), reverse=True)[:top_n_pins]
    return GexResult(net_gex=net, regime="dampen" if net >= 0 else "amplify",
                     by_strike=by_strike, pin_candidates=pins)
