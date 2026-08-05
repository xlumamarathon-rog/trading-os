"""MODULE 55 tests — ring-fenced trading budget."""
import pytest

from src.core.budget_manager import BudgetManager


def test_profit_compounds_into_budget():
    b = BudgetManager(200_000)
    b.attach(1_000_000)                       # account has 10L, budget 2L
    assert b.effective(1_000_000) == 200_000
    # trading makes +50k: budget = allocation + profit
    assert b.effective(1_050_000) == 250_000


def test_loss_shrinks_budget_to_remaining_amount():
    b = BudgetManager(200_000)
    b.attach(1_000_000)
    # trading loses 80k: the remaining amount IS the budget now
    assert b.effective(920_000) == 120_000


def test_budget_never_exceeds_real_account_equity():
    b = BudgetManager(200_000)
    b.attach(150_000)                         # account smaller than allocation
    assert b.effective(150_000) == 150_000    # can't trade money that isn't there


def test_budget_never_negative_and_exhaustion_blocks_entries():
    b = BudgetManager(200_000, min_floor_pct=0.0)
    b.attach(1_000_000)
    assert b.effective(790_000) == 0.0        # lost more than the allocation
    ok, why = b.entries_allowed(790_000)
    assert not ok and why == "budget_exhausted"


def test_floor_stops_new_entries_before_budget_dies():
    b = BudgetManager(200_000, min_floor_pct=0.5)   # floor at 1L
    b.attach(1_000_000)
    assert b.entries_allowed(960_000)[0]            # eff 160k > floor 100k
    ok, why = b.entries_allowed(890_000)            # eff 90k < floor 100k
    assert not ok and why.startswith("budget_floor")


def test_external_flows_are_not_trading_pnl():
    b = BudgetManager(200_000)
    b.attach(1_000_000)
    b.external_flow(+500_000)                 # user deposits 5L
    assert b.effective(1_500_000) == 200_000  # budget unchanged — not profit
    b.external_flow(-300_000)                 # user withdraws 3L
    assert b.effective(1_200_000) == 200_000


def test_operator_top_up_grows_allocation():
    b = BudgetManager(200_000)
    b.attach(1_000_000)
    b.add_budget(100_000)
    assert b.effective(1_000_000) == 300_000


def test_snapshot_shape_for_cockpit():
    b = BudgetManager(200_000, min_floor_pct=0.5)
    b.attach(1_000_000)
    s = b.snapshot(1_030_000)
    assert s == {"allocated": 200_000, "effective": 230_000,
                 "trading_pnl": 30_000, "floor": 100_000,
                 "entries_allowed": True, "reason": "ok"}


def test_invalid_inputs_fail_closed():
    with pytest.raises(ValueError):
        BudgetManager(0)
    with pytest.raises(ValueError):
        BudgetManager(1000, min_floor_pct=1.5)
    b = BudgetManager(1000)
    assert b.effective(-5) == 0.0             # broken equity feed → zero budget
