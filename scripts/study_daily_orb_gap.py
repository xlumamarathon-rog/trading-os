#!/usr/bin/env python3
"""Daily-OHLC studies (Aug 2026): Holmberg-style ORB + demeaned gap fade.

ANALYSIS-ONLY — intrabar fills at a threshold are outside the engine's
next-open contract, so these numbers are study results, never certified
replay claims. Rules and adjudication bars were PRE-REGISTERED in the ledger
(commit 8d76933) before this script produced a single number.

Study A  Holmberg, Lönnbark & Lundström (Finance Research Letters 2013):
         psi = open × (1 ± z·sigma), sigma = trailing 20d std of ln returns
         from bars[:i] ONLY (causal — HONEST_INPUTS principle). Long at
         psi_up if high reaches it, short at psi_dn if low reaches it, exit
         at the same close, no stop. Both-breach days skipped and counted.
         COSTS ADDED (the paper assumed zero): India intraday schedule from
         config at the engine's actual capped notional; MT5 legs pay
         2×half_spread + 2×commission.

Study B  Size-conditioned gap fade on india_wide, intraday returns DEMEANED
         per symbol (else the bear-window drift masquerades as edge),
         signed-gap quintiles, fade = short Q5 / long Q1, net of costs.

Usage: python3 scripts/study_daily_orb_gap.py [--out /tmp/daily_studies]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config_loader import load_config  # noqa: E402

REPORT_FROM = "2026-02-05"
SIGMA_WINDOW = 20
SEED = 20260813
DATASETS = ["data/market_india_wide_6m", "data/market_forex_6m",
            "data/market_forex_wide_6m", "data/market_crypto_6m"]


# ------------------------------------------------------------- cost model

def india_intraday_cost_pct(cfg, notional: float) -> float:
    """Round-trip India intraday cost as a fraction of notional."""
    c = cfg.execution_costs.india
    brokerage = 2 * c.brokerage_flat
    txn = 2 * notional * c.exchange_txn_pct
    stt = notional * c.stt_intraday_sell_pct          # sell side only
    stamp = notional * c.stamp_duty_pct               # buy side only
    gst = c.gst_pct * (brokerage + txn)
    return (brokerage + txn + stt + stamp + gst) / notional


def mt5_cost_pct(meta: dict, price: float) -> float:
    """Round-trip MT5 cost fraction: full spread + two commissions."""
    spread = 2 * meta.get("half_spread", 0.0) / price
    comm = 2 * meta.get("commission_pct", 0.0)
    return spread + comm


# ------------------------------------------------------------- helpers

def load_universe() -> list[tuple[str, dict, list[dict]]]:
    out = []
    for d in DATASETS:
        spec = json.loads((Path(d) / "symbols.json").read_text())
        for sym, meta in spec["symbols"].items():
            bars = json.loads((Path(d) / f"{sym}.json").read_text())
            out.append((sym, meta, bars))
    return out


def trailing_sigma(bars: list[dict], i: int, n: int = SIGMA_WINDOW):
    """Std of ln returns over bars[i-n .. i-1] — bars[:i] only (causal)."""
    if i < n + 1:
        return None
    rets = [math.log(bars[k]["close"] / bars[k - 1]["close"])
            for k in range(i - n + 1, i)]
    if len(rets) < 2:
        return None
    return statistics.stdev(rets)


def bootstrap_ci(values: list[float], n_boot: int = 2000,
                 seed: int = SEED) -> tuple[float, float]:
    """Seeded 95% bootstrap CI on the mean."""
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(n_boot))
    return means[int(0.025 * n_boot)], means[int(0.975 * n_boot)]


# ------------------------------------------------------------- Study A

def orb_trades(sym: str, meta: dict, bars: list[dict], z: float,
               cost_pct: float) -> tuple[list[dict], int]:
    """Holmberg trades for one symbol; returns (trades, both_breach_count)."""
    trades, both = [], 0
    for i in range(1, len(bars)):
        b = bars[i]
        if b["date"] < REPORT_FROM:
            continue
        sig = trailing_sigma(bars, i)
        if sig is None or sig <= 0:
            continue
        psi_up = b["open"] * (1 + z * sig)
        psi_dn = b["open"] * (1 - z * sig)
        hit_up, hit_dn = b["high"] >= psi_up, b["low"] <= psi_dn
        if hit_up and hit_dn:
            both += 1                    # daily OHLC cannot order them
            continue
        if not (hit_up or hit_dn):
            continue
        entry = psi_up if hit_up else psi_dn
        gross = (b["close"] / entry - 1) if hit_up else (entry / b["close"] - 1)
        # cost model uses entry-price notional (MT5 spread scales w/ price)
        cp = cost_pct if cost_pct is not None else mt5_cost_pct(meta, entry)
        trades.append({"sym": sym, "date": b["date"],
                       "side": "long" if hit_up else "short",
                       "gross": gross, "net": gross - cp})
    return trades, both


def run_orb(cfg, universe, z: float, india_notional: float) -> dict:
    india_cp = india_intraday_cost_pct(cfg, india_notional)
    all_trades, both_total = [], 0
    for sym, meta, bars in universe:
        cp = india_cp if meta["leg"] == "india" else None
        t, both = orb_trades(sym, meta, bars, z, cp)
        all_trades += t
        both_total += both
    n = len(all_trades)
    if n == 0:
        return {"z": z, "n": 0}
    nets = [t["net"] for t in all_trades]
    wins = sum(1 for x in nets if x > 0)
    lo, hi = bootstrap_ci(nets)
    return {"z": z, "n": n, "win_pct": round(100 * wins / n, 1),
            "mean_net_bp": round(1e4 * statistics.mean(nets), 2),
            "sum_net_pct": round(100 * sum(nets), 2),
            "ci95_mean_net_bp": [round(1e4 * lo, 2), round(1e4 * hi, 2)],
            "both_breach_skipped": both_total,
            "longs": sum(1 for t in all_trades if t["side"] == "long"),
            "india_cost_bp": round(1e4 * india_cp, 1),
            "trades": all_trades}


# ------------------------------------------------------------- Study B

def gap_study(cfg, universe, india_notional: float) -> dict:
    cp = india_intraday_cost_pct(cfg, india_notional)
    rows = []
    artifacts = []
    for sym, meta, bars in universe:
        if meta["leg"] != "india":
            continue
        intr = [(b["close"] / b["open"] - 1)
                for k, b in enumerate(bars)
                if k and b["date"] >= REPORT_FROM]
        mu = statistics.mean(intr) if intr else 0.0
        for k in range(1, len(bars)):
            b, p = bars[k], bars[k - 1]
            if b["date"] < REPORT_FROM:
                continue
            gap = b["open"] / p["close"] - 1
            if abs(gap) > 0.05:
                artifacts.append((sym, b["date"], round(100 * gap, 2)))
            rows.append({"sym": sym, "date": b["date"], "gap": gap,
                         "intr_demeaned": (b["close"] / b["open"] - 1) - mu})
    rows.sort(key=lambda r: r["gap"])
    n = len(rows)
    quints = [rows[int(q * n / 5):int((q + 1) * n / 5)] for q in range(5)]
    table = []
    for qi, qr in enumerate(quints):
        vals = [r["intr_demeaned"] for r in qr]
        table.append({"quintile": f"Q{qi+1}",
                      "mean_gap_bp": round(1e4 * statistics.mean(r["gap"] for r in qr), 1),
                      "mean_intr_demeaned_bp": round(1e4 * statistics.mean(vals), 2),
                      "pos_pct": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1),
                      "n": len(vals)})
    # fade strategy: LONG Q1 days, SHORT Q5 days, net of costs
    fade = [r["intr_demeaned"] for r in quints[0]] + \
           [-r["intr_demeaned"] for r in quints[4]]
    fade_net = [x - cp for x in fade]
    lo, hi = bootstrap_ci(fade_net)
    return {"quintiles": table, "artifact_gaps_over_5pct": artifacts,
            "fade": {"n": len(fade_net),
                     "mean_net_bp": round(1e4 * statistics.mean(fade_net), 2),
                     "ci95_mean_net_bp": [round(1e4 * lo, 2), round(1e4 * hi, 2)],
                     "win_pct": round(100 * sum(1 for x in fade_net if x > 0)
                                      / len(fade_net), 1),
                     "cost_bp": round(1e4 * cp, 1)}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/daily_studies")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg = load_config("config/master.yaml")
    universe = load_universe()

    results = {"orb": {}, "gap": None}
    for z, notional in [(1.0, 50_000), (0.5, 50_000), (1.5, 50_000),
                        (1.0, 500_000)]:
        key = f"z{z}_n{notional//1000}k"
        r = run_orb(cfg, universe, z, notional)
        trades = r.pop("trades", [])
        (out / f"orb_trades_{key}.json").write_text(json.dumps(trades))
        results["orb"][key] = r
        print(f"ORB {key}: n={r.get('n',0)} win={r.get('win_pct','-')}% "
              f"meanNet={r.get('mean_net_bp','-')}bp CI={r.get('ci95_mean_net_bp','-')} "
              f"sumNet={r.get('sum_net_pct','-')}% bothSkip={r.get('both_breach_skipped','-')}")
    results["gap"] = gap_study(cfg, universe, 50_000)
    print("\nGAP quintiles (india_wide, demeaned, bp):")
    for q in results["gap"]["quintiles"]:
        print(f"  {q['quintile']}: gap={q['mean_gap_bp']:+7.1f} "
              f"intr={q['mean_intr_demeaned_bp']:+7.2f} pos%={q['pos_pct']} n={q['n']}")
    f = results["gap"]["fade"]
    print(f"FADE Q1+Q5 net: n={f['n']} mean={f['mean_net_bp']}bp "
          f"CI={f['ci95_mean_net_bp']} win={f['win_pct']}% cost={f['cost_bp']}bp")
    print(f"artifact gaps >5%: {len(results['gap']['artifact_gaps_over_5pct'])}")
    (out / "results.json").write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
