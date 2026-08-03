"""TradeTheEvent (EDT) dataset loader — wired to the VERIFIED format (R1).

Verified against vendor/TradeTheEvent/run_backtest.py: items carry
  title, text, pub_time, labels{start_time, ticker, ...}
Produces M37 training rows (headline + tickers + timestamps), enforcing the
no-lookahead rule: rows whose labels precede publication are excluded+counted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EdtLoadReport:
    total: int = 0
    usable: int = 0
    skipped_no_labels: int = 0
    skipped_bad_time: int = 0
    skipped_malformed: int = 0


def load_evaluate_news(path) -> tuple:
    """-> (rows, EdtLoadReport); rows: {headline, body, pub_time, ticker, start_time}."""
    items = json.loads(Path(path).read_text())
    rows, rep = [], EdtLoadReport(total=len(items))
    for item in items:
        try:
            if not isinstance(item, dict) or "title" not in item or "pub_time" not in item:
                rep.skipped_malformed += 1
                continue
            labels = item.get("labels") or {}
            if not labels or "ticker" not in labels:
                rep.skipped_no_labels += 1
                continue
            pub, start = item["pub_time"], labels.get("start_time")
            if start is not None and start < pub:
                rep.skipped_bad_time += 1          # lookahead-defective row
                continue
            rows.append({"headline": item["title"], "body": item.get("text", ""),
                         "pub_time": pub, "ticker": labels["ticker"],
                         "start_time": start})
            rep.usable += 1
        except (KeyError, TypeError):
            rep.skipped_malformed += 1
    return rows, rep
