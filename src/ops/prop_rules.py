"""MODULE 69 — Prop-Firm Rules Guard + Challenge Math (Aug 2026).

Funded-account (FTMO-style) evaluations are risk-discipline exams with
hard, account-killing lines: a max DAILY loss (anchored to the day-start
equity at the firm's server midnight, INCLUDING floating P&L), a max
TOTAL drawdown (static from initial balance, or trailing high-water),
a profit target, and a minimum number of traded days.

Two jobs here:

1. **PropGuard** — the rule set as a live guard layer. The invariant that
   matters: OUR lines sit INSIDE the firm's lines. New entries are refused
   once the soft fraction (default 60%) of the firm's daily or total
   budget is consumed — the firm's hard line must never be the first line
   of defense. Breach detection is still tracked and reported (equity
   marks INCLUDE floating, like the firms compute it). Fail-closed.

2. **challenge_monte_carlo / optimal_challenge_risk** — the honest answer
   to "optimize the system to pass the test": given the system's OWN
   realized R distribution, a trades/day rate and the firm's rule set,
   bootstrap thousands of challenge attempts and report P(pass), P(bust),
   expected days — then sweep the risk fraction to find what actually
   maximizes pass probability. A challenge is an asymmetric one-shot bet;
   its optimal aggression is a number, not a feeling. Deterministic seeds.

Everything is config-driven (`prop_firm:` block, disabled by default) and
descriptive/refusing only — this module never places or sizes anything.
"""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field
from typing import Optional

UTC = dt.timezone.utc


@dataclass
class PropRules:
    initial_balance: float = 10_000.0
    max_daily_loss_pct: float = 0.05        # of day-start equity (firm std)
    max_total_dd_pct: float = 0.10          # of initial balance
    trailing_dd: bool = False               # True = from high-water mark
    profit_target_pct: float = 0.10         # phase-1 std
    min_trading_days: int = 4
    soft_fraction: float = 0.60             # OUR line inside the firm's line
    day_reset_utc_hour: int = 21            # firm server midnight (CE(S)T ~ 21-22 UTC)

    @classmethod
    def from_cfg(cls, block: dict) -> "PropRules":
        keys = {f.name for f in cls.__dataclass_fields__.values()} \
            if hasattr(cls, "__dataclass_fields__") else set()
        return cls(**{k: v for k, v in (block or {}).items()
                      if k in cls.__dataclass_fields__})


@dataclass
class PropState:
    day_key: str = ""
    day_start_equity: float = 0.0
    high_water: float = 0.0
    traded_days: set = field(default_factory=set)
    breached: str = ""                       # "" | reason (latched — exam over)


class PropGuard:
    def __init__(self, rules: PropRules) -> None:
        self.rules = rules
        self.state = PropState(high_water=rules.initial_balance)

    # ---------------- clock ----------------

    def _day_key(self, now: dt.datetime) -> str:
        """The firm's trading day: rolls at day_reset_utc_hour, not IST."""
        shifted = now.astimezone(UTC) - dt.timedelta(hours=self.rules.day_reset_utc_hour)
        return shifted.date().isoformat()

    def _roll_day(self, equity: float, now: dt.datetime) -> None:
        key = self._day_key(now)
        if key != self.state.day_key:
            self.state.day_key = key
            self.state.day_start_equity = equity

    # ---------------- marks ----------------

    def on_equity(self, equity: float, now: Optional[dt.datetime] = None) -> dict:
        """Mark with CURRENT equity (must include floating P&L — that is
        how the firms compute both limits). Returns the live status."""
        now = now or dt.datetime.now(UTC)
        self._roll_day(equity, now)
        r, s = self.rules, self.state
        s.high_water = max(s.high_water, equity)

        daily_budget = s.day_start_equity * r.max_daily_loss_pct
        daily_used = max(0.0, s.day_start_equity - equity)
        dd_base = s.high_water if r.trailing_dd else r.initial_balance
        total_budget = dd_base * r.max_total_dd_pct
        total_used = max(0.0, dd_base - equity)

        if not s.breached:
            if daily_used >= daily_budget:
                s.breached = "daily_loss_breached"
            elif total_used >= total_budget:
                s.breached = "max_drawdown_breached"

        target_equity = r.initial_balance * (1 + r.profit_target_pct)
        return {
            "breached": s.breached,
            "daily_used_pct": round(100 * daily_used / max(daily_budget, 1e-9)
                                    * r.max_daily_loss_pct, 3),
            "daily_budget_left": round(daily_budget - daily_used, 2),
            "daily_soft_stop": daily_used >= r.soft_fraction * daily_budget,
            "total_used_pct": round(100 * total_used / max(dd_base, 1e-9), 3),
            "total_budget_left": round(total_budget - total_used, 2),
            "total_soft_stop": total_used >= r.soft_fraction * total_budget,
            "profit_target_equity": round(target_equity, 2),
            "target_progress_pct": round(100 * (equity - r.initial_balance)
                                         / (target_equity - r.initial_balance), 1)
            if target_equity > r.initial_balance else None,
            "traded_days": len(s.traded_days),
            "min_trading_days": r.min_trading_days,
            "target_reached": equity >= target_equity,
        }

    def record_trade(self, now: Optional[dt.datetime] = None) -> None:
        """Count a traded day (fills, not marks) toward min_trading_days."""
        now = now or dt.datetime.now(UTC)
        self.state.traded_days.add(self._day_key(now))

    # ---------------- the guard layer ----------------

    def allows_new_entries(self, equity: float,
                           now: Optional[dt.datetime] = None) -> tuple:
        """(ok, reason) — refuses when breached OR when the soft fraction
        of either firm budget is consumed. Exits are NEVER gated here."""
        st = self.on_equity(equity, now)
        if st["breached"]:
            return False, f"prop:{st['breached']}"
        if st["daily_soft_stop"]:
            return False, "prop:daily_soft_stop"
        if st["total_soft_stop"]:
            return False, "prop:drawdown_soft_stop"
        return True, "ok"


# ------------------------------------------------------------ challenge math

def challenge_monte_carlo(rs, *, rules: PropRules, risk_pct: float,
                          trades_per_day: float = 1.0, max_days: int = 60,
                          paths: int = 3000, seed: int = 11) -> dict:
    """Bootstrap the system's own R distribution through the firm's rule
    set at risk fraction risk_pct. A path PASSES when equity reaches the
    profit target with >= min_trading_days traded and no breach; BUSTS on
    a daily-loss or max-drawdown breach; otherwise times out."""
    rs = [float(x) for x in rs if x is not None]
    if not rs or risk_pct <= 0:
        return {"paths": 0}
    rng = random.Random(seed)
    outcomes = {"pass": 0, "bust": 0, "timeout": 0}
    pass_days = []
    for _ in range(paths):
        equity = rules.initial_balance
        high = equity
        target = rules.initial_balance * (1 + rules.profit_target_pct)
        result = "timeout"
        day = 0
        while day < max_days:
            day += 1
            day_start = equity
            n_trades = max(1, round(rng.gauss(trades_per_day, trades_per_day / 3))) \
                if trades_per_day > 0 else 0
            busted = False
            for _t in range(n_trades):
                equity *= (1.0 + risk_pct * rs[rng.randrange(len(rs))])
                high = max(high, equity)
                dd_base = high if rules.trailing_dd else rules.initial_balance
                if day_start - equity >= day_start * rules.max_daily_loss_pct \
                   or dd_base - equity >= dd_base * rules.max_total_dd_pct:
                    busted = True
                    break
            if busted:
                result = "bust"
                break
            if equity >= target and day >= rules.min_trading_days:
                result = "pass"
                pass_days.append(day)
                break
        outcomes[result] += 1
    return {"paths": paths, "risk_pct": round(risk_pct, 4),
            "p_pass": round(outcomes["pass"] / paths, 4),
            "p_bust": round(outcomes["bust"] / paths, 4),
            "p_timeout": round(outcomes["timeout"] / paths, 4),
            "median_days_to_pass": (sorted(pass_days)[len(pass_days) // 2]
                                    if pass_days else None)}


def optimal_challenge_risk(rs, *, rules: PropRules, trades_per_day: float = 1.0,
                           max_days: int = 60, paths: int = 1500,
                           grid=None) -> dict:
    """Sweep risk_pct and report the pass-probability curve + its argmax.
    THE point of this function: challenge-optimal risk is usually HIGHER
    than wealth-optimal risk (the downside is capped at the fee), but it
    is still a number with a peak — beyond it, busts eat the pass rate."""
    grid = grid or [0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.04, 0.06]
    curve = []
    for f in grid:
        mc = challenge_monte_carlo(rs, rules=rules, risk_pct=f,
                                   trades_per_day=trades_per_day,
                                   max_days=max_days, paths=paths)
        curve.append({"risk_pct": f, "p_pass": mc.get("p_pass", 0.0),
                      "p_bust": mc.get("p_bust", 0.0),
                      "median_days": mc.get("median_days_to_pass")})
    best = max(curve, key=lambda c: c["p_pass"])
    return {"curve": curve, "best": best}
