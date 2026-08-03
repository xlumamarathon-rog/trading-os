#!/usr/bin/env python3
"""Wave 0 — CI lint rules L1/L3/L5 (build plan §1).

L1: only connection_manager.py / order_router.py / mt5_service may import broker clients.
L3: numeric-literal scan on decision modules (report-only until Wave 2 hardening).
L5: bare `except:` or `except ...: pass` fails the build.
Exit code 1 on any hard violation (L1, L5).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

BROKER_IMPORT_RE = re.compile(r"^\s*(import|from)\s+(openalgo|aiomql|MetaTrader5)\b")
L1_ALLOWED = {"connection_manager.py", "order_router.py"}

BARE_EXCEPT_RE = re.compile(r"^\s*except\s*:\s*$")
EXCEPT_PASS_RE = re.compile(r"^\s*except[^\n]*:\s*\n\s*pass\b", re.MULTILINE)

DECISION_FILES = {"kill_switch.py", "position_sizer.py", "margin_checker.py", "order_router.py"}
NUM_RE = re.compile(r"(?<![\w.])(\d+\.\d+|\d{2,})(?![\w.])")
NUM_ALLOW = {"0", "1", "2", "100", "0.0", "1.0", "1000", "1e-9"}

hard, soft = [], []

for py in sorted(SRC.rglob("*.py")):
    text = py.read_text()
    rel = py.relative_to(SRC.parent)

    # L1
    for i, line in enumerate(text.splitlines(), 1):
        if BROKER_IMPORT_RE.match(line) and py.name not in L1_ALLOWED:
            hard.append(f"L1 {rel}:{i} broker import outside allowlist: {line.strip()}")

    # L5
    for i, line in enumerate(text.splitlines(), 1):
        if BARE_EXCEPT_RE.match(line):
            hard.append(f"L5 {rel}:{i} bare except")
    if EXCEPT_PASS_RE.search(text):
        hard.append(f"L5 {rel} except-pass detected")

    # L3 (report-only)
    if py.name in DECISION_FILES:
        for i, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            for m in NUM_RE.finditer(code):
                if m.group(1) not in NUM_ALLOW:
                    soft.append(f"L3 {rel}:{i} literal {m.group(1)} — should this be config?")

print(f"lint_rules: {len(hard)} hard, {len(soft)} soft findings")
for f in hard:
    print("  HARD:", f)
for f in soft:
    print("  soft:", f)

sys.exit(1 if hard else 0)
