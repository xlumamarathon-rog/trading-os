"""MODULE 28 — Weekly shadow audit of every active rule (spec Addendum B)."""
from __future__ import annotations

import time


async def weekly_audit(active_rules: dict, performance_since_fn, shadow_backtest_fn,
                       audit_period_days: int) -> list:
    """Flags (never auto-deactivates — same human gate as activation)."""
    flags = []
    now = time.time()
    for rule_id, rule in active_rules.items():
        age_days = (now - rule.get("applied_at", now)) / 86400.0
        if age_days < audit_period_days:
            continue                     # too soon to judge
        actual = await performance_since_fn(rule_id)
        shadow = await shadow_backtest_fn(rule_id)
        if actual < shadow:
            flags.append({"rule_id": rule_id, "reason": "underperforming shadow simulation",
                          "actual_pnl": actual, "shadow_pnl": shadow})
    return flags
