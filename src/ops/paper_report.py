"""Paper-session evidence (Wave 9): daily report + live-gate progression.

Every paper day produces a markdown report AND advances gate_state.json — the
file src/app.assert_live_allowed() demands before live mode starts. A day only
counts toward the gate when EOD reconciliation was CLEAN; a dirty day resets
the streak (evidence is strict, spec §12.6).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

GATE_FILE = "gate_state.json"


def generate_daily_report(date: str, broker_state: dict, fills: list,
                          reconciliation_clean: bool, audit_row_count: int) -> str:
    lines = [
        f"# Paper Trading Report — {date}",
        "",
        f"| Equity | {broker_state['equity']:.2f} |",
        f"| Cash | {broker_state['cash']:.2f} |",
        f"| Fills | {len(fills)} |",
        f"| Costs charged | {broker_state['total_costs']:.2f} |",
        f"| Open positions | {len(broker_state['positions'])} |",
        f"| Resting stops | {len(broker_state['resting'])} |",
        f"| Audit rows | {audit_row_count} |",
        f"| EOD reconciliation | {'CLEAN' if reconciliation_clean else 'MISMATCHES - day does NOT count'} |",
        "",
        "## Fills",
    ]
    for f in fills:
        lines.append(f"- {f['action']} {f['qty']} {f['symbol']} @ {f['price']:.2f}")
    return "\n".join(lines)


def advance_gate(gate_path=GATE_FILE, *, reconciliation_clean: bool,
                 sebi_checks_passed: bool = False) -> dict:
    path = Path(gate_path)
    gate = json.loads(path.read_text()) if path.exists() else {
        "paper_days_completed": 0, "clean_reconciliation_streak": 0,
        "sebi_checks_passed": False, "human_ack": "", "history": []}
    if reconciliation_clean:
        gate["paper_days_completed"] += 1
        gate["clean_reconciliation_streak"] += 1
    else:
        gate["clean_reconciliation_streak"] = 0
    gate["sebi_checks_passed"] = gate["sebi_checks_passed"] or sebi_checks_passed
    gate["history"].append({"ts": time.time(), "clean": reconciliation_clean})
    path.write_text(json.dumps(gate, indent=1))
    return gate
