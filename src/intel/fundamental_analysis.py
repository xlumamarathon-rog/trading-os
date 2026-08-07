"""MODULE 57 — Fundamental Analysis (Aug 2026).

trading-os had ZERO fundamental analysis — it trades purely on price. OpenBB's
strength is exactly here (obb.equity.fundamental.*), but OpenBB is (a) AGPL-3.0
and (b) only a data-plumbing layer: it fetches statements, it doesn't score
them. So this module owns the part that is actually ours to own — turning a
financial-statement snapshot into standard ratios and a composite health read —
behind a thin provider Protocol that a deployment wires to a real feed
(yfinance/FMP directly, or an OpenBB REST service running alongside; the repo
never imports OpenBB).

Nothing here touches the trade path or the live gate. It is an analyst aid:
ratios + a 0-100 health score + human-readable flags, all with provenance and
explicit warnings when inputs are missing (never a silent zero).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

SOURCE = "trading_os.fundamental_analysis"


@runtime_checkable
class FundamentalProvider(Protocol):
    """Deployment wires this to a real source. Must return a financials dict
    with any of the keys `ratios_from` understands (missing keys degrade
    gracefully). The repo ships NO implementation that imports OpenBB."""

    def fetch(self, symbol: str) -> dict: ...


def _safe_div(a, b) -> Optional[float]:
    try:
        if a is None or b in (None, 0):
            return None
        return a / b
    except (TypeError, ZeroDivisionError):
        return None


def ratios_from(f: dict) -> dict:
    """Standard ratios from a financials dict. Every value is Optional — a
    missing input yields None, never a fabricated number."""
    return {
        "pe": _safe_div(f.get("price"), f.get("eps")),
        "pb": _safe_div(f.get("price"), f.get("book_value_per_share")),
        "roe": _safe_div(f.get("net_income"), f.get("equity")),
        "roa": _safe_div(f.get("net_income"), f.get("total_assets")),
        "debt_to_equity": _safe_div(f.get("total_debt"), f.get("equity")),
        "current_ratio": _safe_div(f.get("current_assets"), f.get("current_liabilities")),
        "gross_margin": _safe_div(f.get("gross_profit"), f.get("revenue")),
        "net_margin": _safe_div(f.get("net_income"), f.get("revenue")),
        "interest_coverage": _safe_div(f.get("ebit"), f.get("interest_expense")),
        "fcf_yield": _safe_div(f.get("free_cash_flow"), f.get("market_cap")),
    }


# (metric, higher_is_better, good_threshold, weak_threshold)
_SCORE_RULES = [
    ("roe", True, 0.15, 0.05),
    ("net_margin", True, 0.10, 0.0),
    ("current_ratio", True, 1.5, 1.0),
    ("interest_coverage", True, 4.0, 1.5),
    ("debt_to_equity", False, 1.0, 2.5),
    ("fcf_yield", True, 0.05, 0.0),
]


def health_score(ratios: dict) -> dict:
    """Composite 0-100 from the ratios that are present. Only scores metrics
    that exist, and reports coverage so a score built on two inputs isn't
    mistaken for one built on six."""
    got = 0
    total = 0.0
    flags = []
    for metric, higher_better, good, weak in _SCORE_RULES:
        v = ratios.get(metric)
        if v is None:
            continue
        got += 1
        if higher_better:
            pts = 1.0 if v >= good else (0.5 if v >= weak else 0.0)
        else:
            pts = 1.0 if v <= good else (0.5 if v <= weak else 0.0)
        total += pts
        if pts == 0.0:
            flags.append(f"weak_{metric}")
        elif pts == 1.0:
            flags.append(f"strong_{metric}")
    if got == 0:
        return {"score": None, "coverage": 0, "flags": [], "warnings": ["no_scorable_metrics"]}
    return {"score": round(100.0 * total / got, 1), "coverage": got,
            "flags": flags, "warnings": []}


@dataclass
class FundamentalReport:
    symbol: str
    ratios: dict
    score: Optional[float]
    coverage: int
    flags: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    source: str = SOURCE

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "ratios": self.ratios, "score": self.score,
                "coverage": self.coverage, "flags": self.flags,
                "warnings": self.warnings, "source": self.source}


def analyze(symbol: str, financials: Optional[dict] = None,
            provider: Optional[FundamentalProvider] = None) -> FundamentalReport:
    """Score a symbol from a financials dict OR a wired provider. Fails soft:
    missing data -> warnings, never an exception or a fabricated score."""
    warnings = []
    if financials is None:
        if provider is None:
            return FundamentalReport(symbol, {}, None, 0, warnings=["no_data_and_no_provider"])
        try:
            financials = provider.fetch(symbol)
        except Exception as exc:  # noqa: BLE001 — provider failure must not crash analysis
            return FundamentalReport(symbol, {}, None, 0,
                                     warnings=[f"provider_error:{type(exc).__name__}"])
    ratios = ratios_from(financials or {})
    h = health_score(ratios)
    warnings += h["warnings"]
    return FundamentalReport(symbol, ratios, h["score"], h["coverage"],
                             flags=h["flags"], warnings=warnings)
