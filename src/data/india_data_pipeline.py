"""india_data_pipeline — one OHLCV schema out, whatever the source (v2 sources).

Sources are injected callables (broker candles / jugaad-data / openchart /
bhavcopy archive / paid vendor). Gaps are DOCUMENTED, never interpolated.
"""
from __future__ import annotations

from dataclasses import dataclass, field

OHLCV_KEYS = ("ts", "open", "high", "low", "close", "volume")


@dataclass
class DataQualityReport:
    instrument: str
    rows: int
    gaps: list = field(default_factory=list)
    dropped_malformed: int = 0


def normalize_ohlcv(instrument: str, raw_rows: list, expected_step: float = None) -> tuple:
    """Returns (rows, DataQualityReport). Rows sorted by ts, malformed dropped+counted."""
    clean, dropped = [], 0
    for r in raw_rows:
        try:
            row = {k: float(r[k]) for k in OHLCV_KEYS}
        except (KeyError, TypeError, ValueError):
            dropped += 1
            continue
        if row["high"] < row["low"] or row["low"] < 0 or row["volume"] < 0:
            dropped += 1
            continue
        clean.append(row)
    clean.sort(key=lambda r: r["ts"])
    report = DataQualityReport(instrument, rows=len(clean), dropped_malformed=dropped)
    if expected_step:
        for a, b in zip(clean, clean[1:]):
            if b["ts"] - a["ts"] > expected_step * 1.5:
                report.gaps.append((a["ts"], b["ts"]))
    return clean, report
