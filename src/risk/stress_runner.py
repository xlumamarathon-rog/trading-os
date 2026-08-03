"""MODULE 7 — Stress-test runner over scenarios/*.json (spec §Phase 2)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REQUIRED_KEYS = {"name", "date_range", "description", "shocks"}


@dataclass
class StressResult:
    scenario: str
    pnl: float
    pnl_pct: float
    per_position: dict


def load_scenario(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text())
    missing = REQUIRED_KEYS - set(data)
    if missing:
        raise ValueError(f"scenario {path} missing keys: {missing}")
    return data


def load_all(directory: str | Path = "scenarios") -> list[dict]:
    return [load_scenario(p) for p in sorted(Path(directory).glob("*.json"))]


def run_scenario(scenario: dict, positions: list[dict]) -> StressResult:
    """positions: [{symbol, qty, price, asset_class}] — asset_class maps to shocks."""
    per, total, gross = {}, 0.0, 0.0
    for pos in positions:
        notional = pos["qty"] * pos["price"]
        shock = scenario["shocks"].get(pos.get("asset_class", ""), 0.0)
        pnl = notional * shock
        per[pos["symbol"]] = pnl
        total += pnl
        gross += abs(notional)
    return StressResult(scenario["name"], total, total / gross if gross else 0.0, per)
