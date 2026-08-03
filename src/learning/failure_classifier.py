"""MODULE 27 — Six-way failure taxonomy (spec Addendum B; v2 adds GEX evidence)."""
from __future__ import annotations

FAILURE_TYPES = {
    "news_blindspot": "Traded on technical signal without checking relevant news",
    "regime_mismatch": "Strategy tuned for wrong volatility/trend regime",
    "mechanical_move_misread": "Treated leverage-unwind/forced-liquidation as organic trend",
    "correlation_breakdown": "Positions more correlated than risk model assumed",
    "stale_signal_cache": "Cache served outdated signal during fast-breaking news",
    "overfit_historical_rule": "A previously-applied learned rule is now hurting performance",
}


def classify(cases: list, signal_ttl: float) -> str:
    """Ordered checks — cheap/infrastructure causes first. Exactly one class out."""
    if any(c.get("signal_cache_age_at_trade", 0) > signal_ttl for c in cases):
        return "stale_signal_cache"
    if any(c.get("rule_active_during_trade") for c in cases):
        return "overfit_historical_rule"
    if any(c.get("crowd_emotion", {}).get("mechanical_flag")
           or c.get("gex_regime") == "amplify" and c.get("no_news_found") for c in cases):
        return "mechanical_move_misread"
    if any(c.get("news_published_before_trade") for c in cases):
        return "news_blindspot"
    if any(c.get("portfolio_correlation_spiked") for c in cases):
        return "correlation_breakdown"
    return "regime_mismatch"
