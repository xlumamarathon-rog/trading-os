"""EOD Worker (Wave 12) — the daily evidence ritual, automated.

At session close: reconcile internal fills vs broker book, verify no naked
positions, write the daily report, advance the gate (live mode also advances
live_days_completed for the ramp), alert the summary.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from src.ops.eod_reconciler import reconcile
from src.ops.paper_report import advance_gate, generate_daily_report


async def run_eod(*, date: str, mode: str, internal_trades: list, broker_trades: list,
                  naked_positions: list, broker_state: dict, fills_today: list,
                  audit_rows: int, gate_path, reports_dir, alert_fn=None) -> dict:
    rep = reconcile(date, internal_trades, broker_trades, naked_positions)
    gate = advance_gate(gate_path, reconciliation_clean=rep.clean)
    if mode == "live":
        p = Path(gate_path)
        g = json.loads(p.read_text())
        g["live_days_completed"] = int(g.get("live_days_completed", 0)) + (1 if rep.clean else 0)
        p.write_text(json.dumps(g, indent=1))
        gate = g
    report = generate_daily_report(f"{date} ({mode})", broker_state, fills_today,
                                   rep.clean, audit_rows)
    out = Path(reports_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"report_{date}.md").write_text(report)
    summary = (f"EOD {date} [{mode}] recon={'CLEAN' if rep.clean else 'MISMATCH'} "
               f"equity={broker_state['equity']:.2f} fills={len(fills_today)} "
               f"gate_days={gate.get('paper_days_completed')} streak={gate.get('clean_reconciliation_streak')}")
    if alert_fn:
        try:
            await alert_fn(summary)
        except Exception:  # noqa: BLE001 — alert loss never blocks the ritual
            pass  # pragma: no cover
    return {"clean": rep.clean, "gate": gate, "summary": summary,
            "mismatches": {"missing_at_broker": rep.missing_at_broker,
                           "missing_internally": rep.missing_internally,
                           "qty": rep.qty_mismatches, "price": rep.price_mismatches,
                           "naked": rep.naked_positions}}
