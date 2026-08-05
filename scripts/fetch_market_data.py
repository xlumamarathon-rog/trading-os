#!/usr/bin/env python3
"""Fetch REAL daily OHLC for one MARKET (india / forex / crypto) from Yahoo
Finance and save it in the replay-harness format, with per-symbol leg params
in symbols.json so research_replay.py can trade any symbol set.

Window: fetches from WARMUP_START so indicators (SMA50, 63d momentum) warm up
on real prior data; meta.json stats + the harness report window cover only the
REPORT_FROM → today slice ("the last 6 months").

Usage: python3 scripts/fetch_market_data.py <india|forex|crypto>
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

import os

WARMUP_START = os.environ.get("WARMUP_START", "2025-11-03")   # real lead-in
REPORT_FROM = os.environ.get("REPORT_FROM", "2026-02-05")     # metrics window
OUT_SUFFIX = os.environ.get("OUT_SUFFIX", "6m")

MARKETS = {
    "india": {
        # NSE large caps · integer lots · real India cost schedule applies
        "RELIANCE": {"yh": "RELIANCE.NS", "leg": "india", "lot": 1, "adv": 8_000_000},
        "TCS": {"yh": "TCS.NS", "leg": "india", "lot": 1, "adv": 2_500_000},
        "HDFCBANK": {"yh": "HDFCBANK.NS", "leg": "india", "lot": 1, "adv": 12_000_000},
    },
    "forex": {
        # CFD costs: half-spread in price units + per-side commission
        "EURUSD": {"yh": "EURUSD=X", "leg": "mt5_forex", "lot": 1000, "adv": 1e12,
                   "half_spread": 0.00005, "commission_pct": 0.000035},
        "GBPUSD": {"yh": "GBPUSD=X", "leg": "mt5_forex", "lot": 1000, "adv": 6e11,
                   "half_spread": 0.00007, "commission_pct": 0.000035},
        "USDJPY": {"yh": "JPY=X", "leg": "mt5_forex", "lot": 1000, "adv": 8e11,
                   "half_spread": 0.008, "commission_pct": 0.000035},
    },
    "crypto": {
        "BTCUSD": {"yh": "BTC-USD", "leg": "mt5_crypto", "lot": 0.01, "adv": 5e9,
                   "half_spread": 17.5, "commission_pct": 0.0},
        "ETHUSD": {"yh": "ETH-USD", "leg": "mt5_crypto", "lot": 0.1, "adv": 2e9,
                   "half_spread": 1.2, "commission_pct": 0.0},
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
    q = res["indicators"]["quote"][0]
    bars = []
    for i, t in enumerate(res["timestamp"]):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c) or min(o, h, l, c) <= 0:
            continue
        bars.append({"date": dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d"),
                     "open": round(o, 6), "high": round(h, 6),
                     "low": round(l, 6), "close": round(c, 6)})
    return bars


def window_stats(bars: list[dict], report_from: str) -> dict:
    win = [b for b in bars if b["date"] >= report_from]
    peak, worst = win[0]["close"], 0.0
    for b in win:
        peak = max(peak, b["close"])
        worst = max(worst, (peak - b["low"]) / peak)
    return {
        "bars_total": len(bars), "bars_in_window": len(win),
        "window": f"{win[0]['date']} → {win[-1]['date']}",
        "start_px": win[0]["close"], "end_px": win[-1]["close"],
        "period_return_pct": round((win[-1]["close"] / win[0]["close"] - 1) * 100, 2),
        "real_max_drawdown_pct": round(worst * 100, 2),
    }


def main(market: str) -> None:
    today = dt.date.today().isoformat()
    out = Path(f"data/market_{market}_{OUT_SUFFIX}")
    out.mkdir(parents=True, exist_ok=True)
    meta, symbols = {}, {}
    for sym, cfg in MARKETS[market].items():
        bars = fetch(cfg["yh"], WARMUP_START, today)
        (out / f"{sym}.json").write_text(json.dumps(bars))
        meta[sym] = window_stats(bars, REPORT_FROM)
        symbols[sym] = {k: v for k, v in cfg.items() if k != "yh"}
        print(sym, meta[sym])
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    (out / "symbols.json").write_text(json.dumps(
        {"symbols": symbols, "report_from": REPORT_FROM}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
