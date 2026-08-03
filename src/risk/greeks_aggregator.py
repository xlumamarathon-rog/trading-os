"""MODULE 9 — Portfolio Greeks aggregation (spec §Phase 2). F&O only."""
from __future__ import annotations

GREEKS = ("delta", "gamma", "theta", "vega")


async def aggregate_portfolio_greeks(positions_fn, greeks_fn) -> dict:
    """positions_fn -> [{symbol, quantity}]; greeks_fn(symbol) -> {delta,...} per unit."""
    totals = {g: 0.0 for g in GREEKS}
    for pos in await positions_fn():
        greeks = await greeks_fn(pos["symbol"])
        for g in GREEKS:
            totals[g] += float(greeks.get(g, 0.0)) * pos["quantity"]
    return totals
