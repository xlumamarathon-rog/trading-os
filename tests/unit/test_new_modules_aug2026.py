"""MODULE 46/47/48 tests — portfolio heat manager, strategy engine registry,
daily session guard, and the regime detector's new trend_direction."""
import pytest

from src.intel.regime_detector import classify_direction
from src.ops.session_guard import SessionGuard
from src.risk.portfolio_heat import PortfolioHeatManager, position_heat
from src.strategies import SIGNALS, get_signal


# ---------- MODULE 46: portfolio heat ----------

def make_pos(direction="buy", entry=100.0, stop=98.0, qty=100, state="RISK_ON"):
    return {"direction": direction, "entry": entry, "stop": stop,
            "remaining_qty": qty, "state": state}


def test_position_heat_long_short_and_breakeven():
    assert position_heat(direction="buy", entry=100, stop=98, remaining_qty=100) == 200
    assert position_heat(direction="sell", entry=100, stop=103, remaining_qty=50) == 150
    # ratcheted past breakeven -> can only bank profit, zero heat
    assert position_heat(direction="buy", entry=100, stop=101, remaining_qty=100) == 0
    assert position_heat(direction="buy", entry=100, stop=98, remaining_qty=0) == 0


def test_heat_cap_blocks_excess_aggregate_risk():
    mgr = PortfolioHeatManager(max_heat_pct=0.06)
    equity = 100_000.0
    positions = [make_pos() for _ in range(2)]        # 2 x 200 = 400 -> 0.4% heat
    ok = mgr.check(positions=positions, equity=equity, proposed_risk=1000)
    assert ok.allowed and ok.proposed_heat == pytest.approx(0.014)
    # book already carrying 5.5% heat: a 1% add breaches the 6% cap
    hot = [make_pos(qty=2750)]                        # 5500 -> 5.5%
    blocked = mgr.check(positions=hot, equity=equity, proposed_risk=1000)
    assert not blocked.allowed and "portfolio_heat" in blocked.reason


def test_heat_fails_closed_on_garbage():
    mgr = PortfolioHeatManager(max_heat_pct=0.06)
    assert not mgr.check(positions=[], equity=0, proposed_risk=100).allowed
    assert not mgr.check(positions=[], equity=float("nan"), proposed_risk=1).allowed
    assert not mgr.check(positions=[], equity=1000, proposed_risk=float("inf")).allowed


def test_exited_positions_carry_no_heat():
    mgr = PortfolioHeatManager(max_heat_pct=0.06)
    positions = [make_pos(state="EXITED", qty=10_000)]
    assert mgr.current_heat(positions, 100_000) == 0.0


# ---------- MODULE 47: strategy engine ----------

def test_registry_has_all_validated_strategies():
    for name in ["baseline", "tsmom", "tsmom_f", "donchian", "rsi2",
                 "improved", "improved2", "improved3", "accurate", "accurate_ls"]:
        assert name in SIGNALS


def test_get_signal_unknown_name_raises():
    with pytest.raises(KeyError):
        get_signal("does_not_exist")


def test_tsmom_signals_no_lookahead_and_both_sides():
    # 80 bars falling then flat: enough history for SMA50 + 63d momentum
    bars = [{"date": f"d{k}", "open": 200 - k, "high": 201 - k,
             "low": 199 - k, "close": 200 - k} for k in range(80)]
    regime = {"trend_state": "STRONG_TREND", "vol_regime": "NORMAL"}
    assert get_signal("tsmom")(bars, 79, regime) == "sell"     # downtrend shorts
    rising = [{"date": f"d{k}", "open": 100 + k, "high": 101 + k,
               "low": 99 + k, "close": 100 + k} for k in range(80)]
    assert get_signal("tsmom")(rising, 79, regime) == "buy"


def test_shock_regime_blocks_filtered_strategies():
    rising = [{"date": f"d{k}", "open": 100 + k, "high": 101 + k,
               "low": 99 + k, "close": 100 + k} for k in range(80)]
    shock = {"trend_state": "STRONG_TREND", "vol_regime": "SHOCK"}
    for name in ["tsmom_f", "improved3", "accurate", "donchian"]:
        assert get_signal(name)(rising, 79, shock) is None


# ---------- MODULE 48: session guard ----------

def test_session_guard_banks_a_good_day():
    g = SessionGuard(profit_bank_pct=0.01, loss_stop_pct=0.01)
    g.start_session(100_000)
    assert g.allows_new_entries(100_500)              # +0.5%: keep trading
    assert not g.allows_new_entries(101_100)          # +1.1%: banked
    assert not g.allows_new_entries(100_200)          # stays tripped all day
    assert g.state.tripped == "profit_banked" and g.state.days_banked == 1
    g.start_session(101_100)                          # new day resets
    assert g.allows_new_entries(101_100)


def test_session_guard_cuts_a_bad_day():
    g = SessionGuard(profit_bank_pct=0.0, loss_stop_pct=0.01)
    g.start_session(100_000)
    assert g.allows_new_entries(99_500)
    assert not g.allows_new_entries(98_900)           # -1.1%: stopped
    assert g.state.tripped == "loss_stopped" and g.state.days_loss_stopped == 1


def test_session_guard_disabled_and_fail_closed():
    off = SessionGuard()
    assert off.allows_new_entries(1.0)                # disabled -> always true
    on = SessionGuard(profit_bank_pct=0.01)
    assert not on.allows_new_entries(100_000)         # enabled, no session -> closed


# ---------- regime detector: trend_direction ----------

def test_classify_direction_symmetric():
    up = [100 + k for k in range(60)]
    down = [200 - k for k in range(60)]
    flat = [100.0] * 60
    assert classify_direction(up) == "UP"
    assert classify_direction(down) == "DOWN"         # NOT "RANGE": the old trap
    assert classify_direction(flat) == "FLAT"
