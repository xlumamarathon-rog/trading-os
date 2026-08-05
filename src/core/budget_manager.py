"""MODULE 55 — Budget Manager: ring-fenced trading capital (Aug 2026).

The operator allocates a BUDGET — the only money the system may trade with,
regardless of how much sits in the broker account. The whole strategy sizes
itself around that amount:

    effective budget = allocated budget + trading P&L since allocation

  profit  → the system trades budget + profit (compounding inside the fence)
  loss    → the remaining amount IS the new budget (losses shrink the fence)
  floor   → below min_floor_pct of the allocation, NEW entries stop — the
            budget version of the kill switch, so a dying allocation cannot
            bleed to zero

Hard invariants (fail-closed):
  · effective budget can never exceed real account equity (you cannot trade
    money that is not there)
  · effective budget can never go below zero
  · external deposits/withdrawals are NOT trading P&L — record them via
    external_flow() so they never inflate or fake the budget's performance
"""
from __future__ import annotations


class BudgetManager:
    def __init__(self, initial_budget: float, *, min_floor_pct: float = 0.0) -> None:
        if initial_budget <= 0:
            raise ValueError("budget must be positive")
        if not 0.0 <= min_floor_pct < 1.0:
            raise ValueError("min_floor_pct must be in [0, 1)")
        self.initial = float(initial_budget)
        self.min_floor_pct = float(min_floor_pct)
        self.baseline_equity: float | None = None    # account equity at allocation

    # ---------- lifecycle ----------

    def attach(self, account_equity: float) -> None:
        """Anchor the budget to the account's current equity. P&L is measured
        from this baseline — call once when the allocation starts."""
        if account_equity <= 0:
            raise ValueError("account equity must be positive at attach")
        self.baseline_equity = float(account_equity)

    def external_flow(self, amount: float) -> None:
        """Record a deposit (+) or withdrawal (−) on the ACCOUNT. Moves the
        baseline so the flow never masquerades as trading P&L."""
        if self.baseline_equity is None:
            raise RuntimeError("attach() before recording flows")
        self.baseline_equity += float(amount)

    def add_budget(self, amount: float) -> None:
        """Operator tops up (or trims) the allocation itself."""
        if self.initial + amount <= 0:
            raise ValueError("budget must stay positive")
        self.initial += float(amount)

    # ---------- the numbers the rest of the system consumes ----------

    def trading_pnl(self, account_equity: float) -> float:
        if self.baseline_equity is None:
            self.attach(account_equity)
        return account_equity - self.baseline_equity

    def effective(self, account_equity: float) -> float:
        """budget + P&L, clamped to [0, account equity]. THIS is the number
        the position sizer must treat as the whole world."""
        if account_equity <= 0:
            return 0.0
        eff = self.initial + self.trading_pnl(account_equity)
        return max(0.0, min(eff, account_equity))

    def entries_allowed(self, account_equity: float) -> tuple[bool, str]:
        """Fail-closed entry gate: floor breached or budget exhausted → no
        NEW entries (open positions keep their stops — risk-off, not panic)."""
        eff = self.effective(account_equity)
        floor = self.initial * self.min_floor_pct
        if eff <= 0:
            return False, "budget_exhausted"
        if eff < floor:
            return False, f"budget_floor {eff:.2f} < {floor:.2f}"
        return True, "ok"

    def snapshot(self, account_equity: float) -> dict:
        ok, why = self.entries_allowed(account_equity)
        return {"allocated": round(self.initial, 2),
                "effective": round(self.effective(account_equity), 2),
                "trading_pnl": round(self.trading_pnl(account_equity), 2),
                "floor": round(self.initial * self.min_floor_pct, 2),
                "entries_allowed": ok, "reason": why}
