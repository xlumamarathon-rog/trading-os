"""MODULE 12 — ai-berkshire verdict -> watchlist bridge (spec §Phase 2, GLUE)."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

_REC = re.compile(r"recommendation\s*[:\-]\s*(pass|fail|grey)", re.I)
_CONV = re.compile(r"conviction\s*(?:score)?\s*[:\-]\s*([01](?:\.\d+)?)", re.I)
_TICKER = re.compile(r"ticker\s*[:\-]\s*([A-Z0-9&\-\.]+)", re.I)


@dataclass
class Verdict:
    ticker: str
    recommendation: str          # Pass | Fail | Grey
    conviction: float


def parse_ai_berkshire_report(text: str) -> Verdict:
    rec, conv, tick = _REC.search(text), _CONV.search(text), _TICKER.search(text)
    if not (rec and conv and tick):
        raise ValueError("unparseable ai-berkshire report: need Ticker/Recommendation/Conviction")
    return Verdict(ticker=tick.group(1).upper(),
                   recommendation=rec.group(1).capitalize(),
                   conviction=float(conv.group(1)))


async def process_report(text: str, watchlist_insert) -> dict | None:
    verdict = parse_ai_berkshire_report(text)
    if verdict.recommendation != "Pass":
        return None
    row = {"ticker": verdict.ticker, "conviction_score": verdict.conviction,
           "source": "ai-berkshire", "date_added": time.time()}
    await watchlist_insert(row)
    return row
