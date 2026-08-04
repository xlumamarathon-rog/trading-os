#!/usr/bin/env python3
"""Fetch REAL daily OHLC from Yahoo Finance for a scenario window and save it
in the data/real format the replay harnesses consume (+ meta.json)."""
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

WINDOWS = {
    # scenario windows padded with ~3 months of lead-in for indicator warmup
    "gfc_2008": {
        "start": "2008-05-19", "end": "2009-03-31",
        "symbols": {"RELIANCE": "RELIANCE.NS", "EURUSD": "EURUSD=X"},
    },
    "flash_crash_2012": {
        "start": "2012-06-18", "end": "2012-12-31",
        "symbols": {"RELIANCE": "RELIANCE.NS", "EURUSD": "EURUSD=X"},
    },
}


def epoch(d: str) -> int:
    return int(dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())


def fetch(yh: str, start: str, end: str) -> list[dict]:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{yh}"
           f"?period1={epoch(start)}&period2={epoch(end)}&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = json.loads(urllib.request.urlopen(req, timeout=30).read())
    res = raw["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    bars = []
    for i, t in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c) or min(o, h, l, c) <= 0:
            continue
        bars.append({"date": dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d"),
                     "open": round(o, 6), "high": round(h, 6),
                     "low": round(l, 6), "close": round(c, 6)})
    return bars


def mdd(bars: list[dict]) -> float:
    peak, worst = bars[0]["close"], 0.0
    for b in bars:
        peak = max(peak, b["close"])
        worst = max(worst, (peak - b["low"]) / peak)
    return worst


def main(window: str) -> None:
    w = WINDOWS[window]
    out = Path(f"data/scenario_{window}")
    out.mkdir(parents=True, exist_ok=True)
    meta = {}
    for sym, yh in w["symbols"].items():
        bars = fetch(yh, w["start"], w["end"])
        (out / f"{sym}.json").write_text(json.dumps(bars))
        meta[sym] = {
            "bars": len(bars), "first": bars[0]["date"], "last": bars[-1]["date"],
            "start_px": bars[0]["close"], "end_px": bars[-1]["close"],
            "period_return_pct": round((bars[-1]["close"] / bars[0]["close"] - 1) * 100, 2),
            "real_max_drawdown_pct": round(mdd(bars) * 100, 2),
        }
        print(sym, meta[sym])
    (out / "meta.json").write_text(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
