#!/usr/bin/env python3
"""Intraday bar archiver (Aug 2026) — TIME-CRITICAL data harvest.

Why this exists (orderflow research, ledger 2026-08-13): Yahoo's chart API
serves NSE 1-minute bars only inside a ROLLING ~30-DAY WINDOW (verified live:
"must be within the last 30 days", max ~8 days per request) and 5m/15m only
inside ~60 days. Every session that passes unharvested is intraday history
nobody can buy back at retail tier — NSE brokers sell no tick/depth archive
at any price reachable here. Run this daily (cron) and in a year the system
owns a 1m NSE dataset that cannot be reconstructed later.

    python3 scripts/intraday_archiver.py                    # default universe
    python3 scripts/intraday_archiver.py --intervals 1m,5m  # subset
    cron:  15 11 * * 1-5  cd /path/to/repo && python3 scripts/intraday_archiver.py

Design contract:
  - DEDUP-SAFE + RESUMABLE: bars are merged by epoch timestamp into one JSON
    file per symbol+interval; re-runs and overlapping windows are harmless.
  - ATOMIC: files are written to a temp path then os.replace()d — a killed
    run never corrupts the archive.
  - HONEST STORAGE: raw Yahoo OHLCV, no adjustment, no resampling. The
    archive is data/intraday/ and is GITIGNORED — it is a runtime asset that
    grows daily, never a committed fixture.
  - LEAN: stdlib only (urllib), same pedigree as scripts/fetch_market_data.py.

Exit codes: 0 = every requested symbol produced/kept data; 1 = partial
failures (cron alerts can grep the summary); 2 = nothing succeeded.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.fetch_market_data import MARKETS  # noqa: E402 — yh symbol map

UTC = dt.timezone.utc
OUT_DIR = Path("data/intraday")

# Verified live 2026-08-13 (ledger): per-interval fetch constraints.
#   1m: <=8 days/request AND window must sit inside the last ~30 days.
#   5m/15m: window must sit inside the last ~60 days (single request is fine).
INTERVALS = {
    "1m": {"lookback_days": 29, "chunk_days": 7},
    "5m": {"lookback_days": 59, "chunk_days": 59},
    "15m": {"lookback_days": 59, "chunk_days": 59},
}

# Default universe: the 15-name pre-registered NIFTY set + the index itself
# (^NSEI prices are real; its volume is zero at source — stored as-is).
def default_symbols() -> dict:
    syms = {s: c["yh"] for s, c in MARKETS["india_wide"].items()}
    syms["NSEI"] = "^NSEI"
    return syms


# ------------------------------------------------------------ pure logic

def chunk_windows(now_epoch: int, interval: str) -> list[tuple[int, int]]:
    """(period1, period2) request windows, oldest first, inside the API wall."""
    spec = INTERVALS[interval]
    start = now_epoch - spec["lookback_days"] * 86_400
    step = spec["chunk_days"] * 86_400
    out = []
    p1 = start
    while p1 < now_epoch:
        p2 = min(p1 + step, now_epoch)
        out.append((p1, p2))
        p1 = p2
    return out


def parse_chart(payload: dict) -> list[list]:
    """Yahoo chart JSON -> [[ts, o, h, l, c, v], ...] (nulls skipped)."""
    res = payload["chart"]["result"][0]
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    vols = q.get("volume") or []
    bars = []
    for i, t in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c) or min(o, h, l, c) <= 0:
            continue
        v = vols[i] if i < len(vols) else None
        bars.append([int(t), round(o, 6), round(h, 6), round(l, 6),
                     round(c, 6), int(v) if v else 0])
    return bars


def merge_bars(existing: list[list], new: list[list]) -> list[list]:
    """Timestamp-keyed union; NEW wins on collision (fresher snapshot of a
    possibly still-forming bar); returns sorted by ts."""
    by_ts = {b[0]: b for b in existing}
    by_ts.update({b[0]: b for b in new})
    return [by_ts[t] for t in sorted(by_ts)]


def manifest_entry(symbol: str, interval: str, bars: list[list],
                   added: int) -> dict:
    dates = {}
    for b in bars:
        d = dt.datetime.fromtimestamp(b[0], UTC).date().isoformat()
        dates[d] = dates.get(d, 0) + 1
    per = sorted(dates.values())
    return {
        "symbol": symbol, "interval": interval, "bars": len(bars),
        "first_ts": bars[0][0] if bars else None,
        "last_ts": bars[-1][0] if bars else None,
        "sessions": len(dates),
        "median_bars_per_session": per[len(per) // 2] if per else 0,
        "added_last_run": added,
        "updated_at": dt.datetime.now(UTC).isoformat(timespec="seconds"),
    }


def atomic_write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    os.replace(tmp, path)


# ------------------------------------------------------------ fetch layer

def fetch_chart(yh: str, interval: str, p1: int, p2: int) -> dict:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{yh}"
           f"?period1={p1}&period2={p2}&interval={interval}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def archive_symbol(symbol: str, yh: str, interval: str, out_dir: Path,
                   fetch=fetch_chart, now_epoch: int | None = None,
                   pause_s: float = 0.4) -> dict:
    """Harvest one symbol+interval into the archive; returns manifest entry."""
    now_epoch = now_epoch or int(time.time())
    f = out_dir / f"{symbol}_{interval}.json"
    existing = json.loads(f.read_text())["bars"] if f.exists() else []
    before = len(existing)
    merged = existing
    for p1, p2 in chunk_windows(now_epoch, interval):
        try:
            merged = merge_bars(merged, parse_chart(fetch(yh, interval, p1, p2)))
        except Exception as exc:  # noqa: BLE001 — window-level fail-soft
            print(f"  WARN {symbol} {interval} window {p1}->{p2}: {exc}")
        time.sleep(pause_s)
    entry = manifest_entry(symbol, interval, merged, len(merged) - before)
    atomic_write_json(f, {"symbol": symbol, "interval": interval, "yahoo": yh,
                          "bars": merged})
    return entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervals", default="1m,5m,15m")
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--pause", type=float, default=0.4)
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    symbols = default_symbols()
    intervals = [i.strip() for i in args.intervals.split(",") if i.strip()]
    ok = fail = 0
    for sym, yh in symbols.items():
        for iv in intervals:
            if iv not in INTERVALS:
                print(f"  SKIP unknown interval {iv!r}")
                continue
            try:
                entry = archive_symbol(sym, yh, iv, out_dir, pause_s=args.pause)
                manifest[f"{sym}_{iv}"] = entry
                ok += 1
                print(f"  {sym:10s} {iv:3s} bars={entry['bars']:6d} "
                      f"(+{entry['added_last_run']}) sessions={entry['sessions']}")
            except Exception as exc:  # noqa: BLE001 — symbol-level fail-soft
                fail += 1
                print(f"  FAIL {sym} {iv}: {exc}")
    atomic_write_json(manifest_path, manifest)
    print(f"\narchive: {ok} ok, {fail} failed -> {out_dir}")
    return 0 if fail == 0 else (1 if ok else 2)


if __name__ == "__main__":
    raise SystemExit(main())
