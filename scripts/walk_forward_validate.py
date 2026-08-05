#!/usr/bin/env python3
"""Out-of-sample validation of the optimized strategy variants using the
repo's OWN validation modules (no custom statistics):

  MODULE 32 walk_forward_test  — rolling 2y-train → 6m-test segments over
    2018-2026; a variant only counts a segment when it showed positive
    in-sample Sharpe on the train window (rediscover), then is scored on the
    UNSEEN forward window; passes on majority profitable with ≥5 segments.

  MODULE 25 HoldoutValidator  — untouched holdout (Aug 2024 → Aug 2025,
    never used during optimization); variant vs production baseline with
    stationary-bootstrap p < 0.1 and drawdown-not-worse.

Every datapoint is a REAL replay through the full stack (sizer, router,
ExitManager, costs) via scripts/research_replay.py.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.learning.holdout_validator import HoldoutValidator
from src.learning.walk_forward import walk_forward_test

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")
SEM = asyncio.Semaphore(5)

V1 = json.dumps({"k_trail_by_regime": {"STRONG_TREND": 2.0, "WEAK_TREND": 1.5,
                                       "RANGE": 1.0, "SHOCK": 0.5},
                 "partials": [{"at_r": 1.0, "pct": 50}]})
V2 = json.dumps({"k_trail_by_regime": {"STRONG_TREND": 3.0, "WEAK_TREND": 2.0,
                                       "RANGE": 1.25, "SHOCK": 0.75},
                 "partials": []})

CONFIGS = {
    "india": {"strategy": "tsmom_f", "exits": V1, "env": {}},
    "forex": {"strategy": "accurate", "exits": V1, "env": {}},
    "crypto": {"strategy": "tsmom_f", "exits": V2, "env": {"GIVEBACK_PCT": "0.02"}},
}
BASELINE = {"strategy": "baseline", "exits": "{}", "env": {}}
HOLDOUT = ("2024-08-01", "2025-08-01")
WARMUP_BARS = 130


def y2d(y: float) -> str:
    year = int(y)
    frac = y - year
    month = 1 + round(frac * 12)
    if month > 12:
        year, month = year + 1, month - 12
    return f"{year:04d}-{month:02d}-01"


def slice_dataset(market: str, start: str, end: str, dest: Path) -> None:
    src = ROOT / f"data/market_{market}_hist"
    dest.mkdir(parents=True, exist_ok=True)
    spec = json.loads((src / "symbols.json").read_text())
    meta = {}
    for sym in spec["symbols"]:
        bars = json.loads((src / f"{sym}.json").read_text())
        idx = next((i for i, b in enumerate(bars) if b["date"] >= start), len(bars))
        window = bars[max(0, idx - WARMUP_BARS):]
        window = [b for b in window if b["date"] < end]
        (dest / f"{sym}.json").write_text(json.dumps(window))
        inw = [b for b in window if b["date"] >= start] or window[-1:]
        meta[sym] = {"period_return_pct": round((inw[-1]["close"] / inw[0]["close"] - 1) * 100, 2)}
    (dest / "meta.json").write_text(json.dumps(meta))
    spec["report_from"] = start
    (dest / "symbols.json").write_text(json.dumps(spec))


async def replay(market: str, cfg: dict, start: str, end: str) -> dict:
    async with SEM:
        last_err = ""
        for attempt in range(2):                       # one retry on transient failure
            tmp = Path(tempfile.mkdtemp(prefix=f"wf_{market}_"))
            data_dir, out_dir = tmp / "data", tmp / "out"
            slice_dataset(market, start, end, data_dir)
            env = {"PATH": "/usr/bin:/bin", **cfg["env"], "REPORT_FROM": start}
            proc = await asyncio.create_subprocess_exec(
                PY, str(ROOT / "scripts/research_replay.py"), cfg["strategy"],
                str(data_dir), str(out_dir), cfg["exits"],
                cwd=str(ROOT), env=env,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, stderr = await proc.communicate()
            try:
                res = json.loads((out_dir / "results.json").read_text())
                curve = json.loads((out_dir / "equity_curve.json").read_text())
                res["_daily_returns"] = [
                    curve[i]["equity"] / curve[i - 1]["equity"] - 1
                    for i in range(1, len(curve)) if curve[i]["date"] >= start]
                return res
            except FileNotFoundError:
                last_err = stderr.decode()[-400:]
                print(f"[retry {attempt}] {market} {start}->{end}: {last_err}",
                      file=sys.stderr)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"replay failed twice: {market} {start}->{end}: {last_err}")


async def validate_market(market: str) -> dict:
    cfg = CONFIGS[market]

    async def rediscover_fn(pattern, train_end):
        # found only if the variant shows positive Sharpe on the 2y TRAIN
        # window (data strictly before train_end)
        res = await replay(market, cfg, y2d(train_end - 2.0), y2d(train_end))
        return res["sharpe_annualized"] > 0

    async def backtest_fn(pattern, test_start, test_end):
        res = await replay(market, cfg, y2d(test_start), y2d(test_end))
        return res["sharpe_annualized"]

    wf = await walk_forward_test({"id": f"{market}:{cfg['strategy']}"},
                                 start_year=2018.5, end_year=2026.5,
                                 rediscover_fn=rediscover_fn, backtest_fn=backtest_fn,
                                 window_years=2.0, step_years=0.5)

    async def holdout_backtest(rule):
        used = cfg if rule is not None else BASELINE
        res = await replay(market, used, HOLDOUT[0], HOLDOUT[1])
        return res["_daily_returns"]

    hv = HoldoutValidator(holdout_backtest)
    ho = await hv.test({"id": f"{market}:{cfg['strategy']}:holdout"})

    return {"market": market, "strategy": cfg["strategy"],
            "walk_forward": {"passed": wf.passed,
                             "profitable_fraction": round(wf.profitable_fraction, 3),
                             "segments": wf.segments},
            "holdout": {"passed": ho.passed, "sharpe_delta": round(ho.sharpe_delta, 4),
                        "dd_delta": round(ho.dd_delta, 4), "p_value": ho.p_value,
                        "reason": ho.reason}}


async def main():
    results = await asyncio.gather(*[validate_market(m) for m in CONFIGS])
    print(json.dumps(results, indent=1))
    Path("/tmp/wf_validation.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
