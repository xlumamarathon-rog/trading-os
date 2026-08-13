#!/usr/bin/env python3
"""Probe the MT5 bridge for per-symbol history depth (Aug 2026).

Answers: how many months/years of M1 bars and real ticks does OUR broker's
server actually serve for each mt5 symbol? This is the gate for any forex/
crypto intraday backtest (peer-reviewed intraday momentum survives ~1-2bp
MT5 costs — but only if the history exists; retention is a per-broker fact).

Run from the Linux core against the Windows-VPS bridge:

    MT5_SERVICE_TOKEN=... python3 scripts/probe_mt5_history.py \
        [--base-url https://mt5-vps.internal:8443]

Writes data/runtime/mt5_history_coverage.json (gitignored runtime state) and
prints a coverage table. First run is SLOW per symbol (the terminal pulls
tick months from the broker server on first touch) — that is the sync
working, not a hang. Exit codes: 0 all symbols probed, 1 partial, 2 none.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from src.core.config_loader import load_config

OUT = Path("data/runtime/mt5_history_coverage.json")


def mt5_symbols(cfg) -> list[str]:
    classes = cfg.broker.mt5.symbol_classes
    return list(classes.forex) + list(classes.crypto_cfd)


def probe(base_url: str, token: str, symbols: list[str],
          client: httpx.Client | None = None) -> dict:
    client = client or httpx.Client(base_url=base_url, verify=False,
                                    timeout=300.0,   # tick sync IS slow
                                    headers={"X-MT5-Auth": token})
    out = {"probed_at": dt.datetime.now(dt.timezone.utc)
           .isoformat(timespec="seconds"), "base_url": base_url, "symbols": {}}
    for sym in symbols:
        try:
            r = client.get(f"/history_depth/{sym}")
            r.raise_for_status()
            out["symbols"][sym] = r.json()
        except Exception as exc:  # noqa: BLE001 — per-symbol fail-soft
            out["symbols"][sym] = {"error": str(exc)}
    return out


def summarize(cov: dict) -> tuple[int, int]:
    ok = fail = 0
    today = dt.date.today()
    print(f"{'symbol':10s} {'M1 since':>12s} {'M1 days':>8s} "
          f"{'ticks since':>12s} {'tick days':>9s}")
    for sym, row in cov["symbols"].items():
        if "error" in row:
            fail += 1
            print(f"{sym:10s} ERROR {row['error'][:60]}")
            continue
        ok += 1
        m1, tk = row.get("m1_first_date"), row.get("tick_first_date")
        m1d = (today - dt.date.fromisoformat(m1)).days if m1 else 0
        tkd = (today - dt.date.fromisoformat(tk)).days if tk else 0
        print(f"{sym:10s} {m1 or '—':>12s} {m1d:8d} {tk or '—':>12s} {tkd:9d}")
    return ok, fail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="")
    args = ap.parse_args()
    cfg = load_config("config/master.yaml")
    base_url = args.base_url or cfg.broker.mt5.exec_service_url
    token = os.environ.get("MT5_SERVICE_TOKEN", "")
    cov = probe(base_url, token, mt5_symbols(cfg))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cov, indent=1))
    ok, fail = summarize(cov)
    print(f"\ncoverage -> {OUT} ({ok} ok, {fail} failed)")
    return 0 if fail == 0 else (1 if ok else 2)


if __name__ == "__main__":
    raise SystemExit(main())
