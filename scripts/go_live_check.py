#!/usr/bin/env python3
"""GO-LIVE CHECK — the operator's final pre-flight. Prints a PASS/FAIL table
and exits non-zero unless EVERYTHING an automated check can verify is green.
The three human items are listed explicitly — this script cannot do them for you.

Run on the VPS:  python3 scripts/go_live_check.py [--gate gate_state.json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.app import LIVE_ACK_PHRASE, LiveGateError, assert_live_allowed
from src.core.config_loader import load_config
from src.ops.persistence import ChainTamperedError, JsonlAuditLog

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", default="gate_state.json")
    ap.add_argument("--audit", default="data/runtime/audit_paper.jsonl")
    args = ap.parse_args()

    # 1. config loads + no unresolved secrets
    try:
        cfg = load_config("config/master.yaml")
        check("config loads + schema valid", True)
        check("all secrets resolved (.env complete)", not cfg.unresolved_env,
              ",".join(cfg.unresolved_env))
        check("static IP confirmed in config",
              bool(cfg.model_extra["broker"]["india"].get("static_ip_confirmed")))
    except Exception as exc:
        check("config loads", False, str(exc)[:80])
        cfg = None

    # 2. full test suite + safety lint
    tests = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                           capture_output=True, text=True)
    check("full test suite green", tests.returncode == 0,
          tests.stdout.strip().splitlines()[-1] if tests.stdout else "")
    lint = subprocess.run([sys.executable, "scripts/lint_rules.py"], capture_output=True)
    check("safety lint (L1/L5) clean", lint.returncode == 0)

    # 3. paper evidence + audit chain
    gate_p = Path(args.gate)
    if gate_p.exists():
        gate = json.loads(gate_p.read_text())
        check("paper days >= 14", int(gate.get("paper_days_completed", 0)) >= 14,
              str(gate.get("paper_days_completed")))
        check("clean recon streak >= 5", int(gate.get("clean_reconciliation_streak", 0)) >= 5,
              str(gate.get("clean_reconciliation_streak")))
    else:
        check("gate_state.json exists", False, "run paper mode first")
    if Path(args.audit).exists():
        try:
            JsonlAuditLog(args.audit)
            check("audit chain intact", True)
        except ChainTamperedError as exc:
            check("audit chain intact", False, str(exc))
    else:
        check("audit log present", False, args.audit)

    # 4. the full live gate itself
    if cfg is not None:
        try:
            assert_live_allowed(cfg, args.gate)
            check("LIVE GATE: would open", True)
        except LiveGateError as exc:
            check("LIVE GATE: would open", False, str(exc)[:120])

    failed = [c for c in CHECKS if not c[1]]
    print("\n" + ("ALL AUTOMATED CHECKS GREEN — the three HUMAN items remain your signature:"
                   if not failed else f"{len(failed)} CHECK(S) FAILED — live must wait."))
    print(f"  1. SEBI Feb-2025 registration + black-box/RA determination (MODULE 17)\n"
          f"  2. Broker static IP whitelisted + config flag set\n"
          f"  3. gate_state.json human_ack set to exactly: {LIVE_ACK_PHRASE!r}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
