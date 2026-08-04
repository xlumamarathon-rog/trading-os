#!/usr/bin/env python3
"""Run every scenarios/*.json shock through src.risk.stress_runner against a
representative fully-invested strategy-lab book (median entry sizes observed
in the COVID replay, marked at recent prices). Answers: if ALL shocks landed
the instant we were fully loaded, what does the book lose vs ₹10L equity?"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.risk.stress_runner import load_all, run_scenario

EQUITY = 1_000_000.0

# median entry sizes from the COVID combo replay (real router/sizer output),
# marked at approximate current prices; asset classes per scenario schema
BOOK = [
    {"symbol": "RELIANCE", "qty": 54, "price": 1308.0, "asset_class": "india_equity"},
    {"symbol": "EURUSD", "qty": 33000, "price": 1.151, "asset_class": "fx_usdinr"},
    {"symbol": "BTCUSD", "qty": 4.28, "price": 63875.0, "asset_class": "crypto"},
]

rows = []
for sc in load_all("scenarios"):
    res = run_scenario(sc, BOOK)
    rows.append({"scenario": sc["name"], "book_pnl": round(res.pnl, 0),
                 "pnl_pct_of_gross": res.pnl_pct,
                 "pnl_pct_of_equity": round(res.pnl / EQUITY * 100, 2),
                 "per_position": {k: round(v, 0) for k, v in res.per_position.items()}})
    print(f"{sc['name']:26} pnl ₹{res.pnl:>12,.0f}   {res.pnl / EQUITY * 100:>6.2f}% of equity")

json.dump(rows, open("/tmp/stress_book_results.json", "w"), indent=1)
