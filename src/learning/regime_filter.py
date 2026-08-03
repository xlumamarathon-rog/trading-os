"""MODULE 30 — Cross-regime consistency filter (v2 thresholds)."""
from __future__ import annotations

from dataclasses import dataclass

MARKET_REGIMES = {
    "pre_2000": ("1990-01-01", "1999-12-31"),
    "dotcom_bull_2000_2008": ("2000-01-01", "2008-08-31"),
    "gfc_2008_2009": ("2008-09-01", "2009-06-30"),
    "taper_bull_2013_2019": ("2013-01-01", "2019-12-31"),
    "covid_2020": ("2020-01-01", "2020-12-31"),
    "rate_hikes_2022_2024": ("2022-01-01", "2024-12-31"),
    "current_2025_now": ("2025-01-01", "9999-12-31"),
}
WIN_RATE_BAR = 0.55
MIN_OCC_PER_REGIME = 3


@dataclass
class RegimeResult:
    regimes_passed: int
    regimes_evaluated: int
    detail: dict


def evaluate_across_regimes(occurrences: list, data_start: str) -> RegimeResult:
    """occurrences: [{date: 'YYYY-MM-DD', win: bool}]. Regimes before data_start
    are excluded from the denominator (data genuinely absent); regimes with
    <MIN_OCC occurrences inside covered data count as FAILED evidence, not skipped."""
    detail, passed, evaluated = {}, 0, 0
    for name, (start, end) in MARKET_REGIMES.items():
        if end < data_start:
            detail[name] = None            # era not covered by data — excluded
            continue
        inside = [o for o in occurrences if start <= o["date"] <= end]
        evaluated += 1
        if len(inside) < MIN_OCC_PER_REGIME:
            detail[name] = {"win_rate": None, "n": len(inside), "passed": False}
            continue
        wr = sum(1 for o in inside if o["win"]) / len(inside)
        ok = wr > WIN_RATE_BAR
        detail[name] = {"win_rate": wr, "n": len(inside), "passed": ok}
        passed += 1 if ok else 0
    return RegimeResult(passed, evaluated, detail)
