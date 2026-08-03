"""MODULE 23 — Backtest runner: flag significant moves -> attribution -> cases.

Engines injected (vectorbt/backtesting.py/zipline on the VPS). EVERY run prices
trades through MODULE 40 (lint L4) — gross-P&L backtests do not exist here.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.core.transaction_cost_model import india_round_trip_cost  # L4: after-cost only

SIGNIFICANT_MOVE = 0.02


@dataclass
class BacktestResult:
    trades: list
    net_pnl: float
    gross_pnl: float
    total_costs: float
    flagged_moves: list


def run_with_costs(price_series: list, strategy_signals: list, qty: float,
                   india_costs, product: str = "intraday") -> BacktestResult:
    """price_series: [{ts, close}]; signals: [{ts, action(buy/sell/flat)}] aligned."""
    trades, gross, costs = [], 0.0, 0.0
    entry = None
    for bar, sig in zip(price_series, strategy_signals):
        if sig["action"] == "buy" and entry is None:
            entry = bar["close"]
        elif sig["action"] in ("sell", "flat") and entry is not None:
            pnl = (bar["close"] - entry) * qty
            cost = india_round_trip_cost(india_costs, qty, entry, bar["close"], product)
            trades.append({"entry": entry, "exit": bar["close"], "pnl": pnl, "cost": cost})
            gross += pnl
            costs += cost
            entry = None
    flagged = flag_significant_moves(price_series)
    return BacktestResult(trades, gross - costs, gross, costs, flagged)


def flag_significant_moves(price_series: list, threshold: float = SIGNIFICANT_MOVE) -> list:
    out = []
    for a, b in zip(price_series, price_series[1:]):
        if a["close"] > 0:
            move = (b["close"] - a["close"]) / a["close"]
            if abs(move) >= threshold:
                out.append({"ts": b["ts"], "move_pct": move,
                            "direction": 1 if move > 0 else -1})
    return out


async def nightly_case_update(price_series, instrument, news_candidates,
                              case_memory, lesson_llm, crowd_fn=None) -> int:
    """Flag yesterday's big moves -> attribute -> store cases. Returns cases stored."""
    from src.learning.lesson_extractor import extract_lesson
    from src.learning.news_attribution import find_cause

    stored = 0
    for move in flag_significant_moves(price_series):
        cause, _ranked = find_cause(move["ts"], move["direction"], instrument, news_candidates)
        crowd = (await crowd_fn(cause)) if (crowd_fn and cause) else {"mechanical_flag": cause is None}
        case = {
            "ticker": instrument, "setup": f"move {move['move_pct']:+.2%} at {move['ts']}",
            "news": {"headline": cause.headline} if cause else {"headline": ""},
            "causal_chain": "attributed" if cause else "no_clear_cause_mechanical",
            "crowd_emotion": crowd, "your_action": {}, "outcome": move["move_pct"],
            "lesson": "",
        }
        try:
            case["lesson"] = await extract_lesson(case, lesson_llm)
        except ValueError:
            case["lesson"] = "no transferable lesson (noise)"
        await case_memory.store_case(case)
        stored += 1
    return stored
