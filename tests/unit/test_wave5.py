"""Wave 5 tests — M13 (BL views), M14 (3 books), M15 (rebalance guards), M16 (reconciler), M17 (SEBI), pipeline."""
import pytest

from src.data.india_data_pipeline import normalize_ohlcv
from src.ops.eod_reconciler import reconcile
from src.ops.sebi_compliance_checker import ComplianceError, validate_sebi_compliance
from src.portfolio.conviction_to_views import conviction_to_bl_view
from src.portfolio.dual_book_manager import DualBookManager, correlation
from src.portfolio.rebalance_scheduler import plan_rebalance


# ---------- M13 ----------

def test_bl_views_all_three_verdicts():
    p = conviction_to_bl_view("HDFCBANK", "Pass", 0.8, baseline_return=0.10)
    f = conviction_to_bl_view("X", "Fail", 0.8, baseline_return=0.10)
    g = conviction_to_bl_view("Y", "Grey", 0.8, baseline_return=0.10)
    assert p["view_return"] == pytest.approx(0.10 * 1.4) and p["confidence"] == 0.8
    assert f["view_return"] == pytest.approx(0.07) and f["confidence"] == 0.6
    assert g["view_return"] == pytest.approx(0.10) and g["confidence"] == 0.3
    with pytest.raises(ValueError):
        conviction_to_bl_view("Z", "Maybe", 0.5, 0.1)
    with pytest.raises(ValueError):
        conviction_to_bl_view("Z", "Pass", 1.5, 0.1)


# ---------- M14 ----------

async def test_three_books_unified_with_crypto_budget_and_corr_warning():
    async def india():
        return {"NIFTYBEES": 0.6, "HDFCBANK": 0.4}

    async def forex():
        return {"EURUSD": 1.0}

    async def crypto():
        return {"BTCUSD": 1.0}

    async def usdinr():
        return 84.0

    mgr = DualBookManager({"india": india, "mt5_forex": forex, "mt5_crypto": crypto},
                          usdinr, crypto_budget_pct=0.10)
    correlated = [0.01, -0.02, 0.03, -0.01, 0.02, 0.015, -0.005]
    alloc = await mgr.optimize({"india": correlated, "mt5_forex": correlated,
                                "mt5_crypto": [0.05, -0.06, 0.01, 0.02, -0.03, 0.04, 0.0]})
    assert sum(abs(v) for v in alloc.weights_inr_terms.values()) == pytest.approx(1.0)
    # crypto sub-book scaled to its own 10% budget → smaller than forex weight
    assert alloc.weights_inr_terms["mt5_crypto:BTCUSD"] < alloc.weights_inr_terms["mt5_forex:EURUSD"]
    assert any("hedging benefit reduced" in w for w in alloc.warnings)  # india|forex corr = 1.0


def test_correlation_bounds():
    assert correlation([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert correlation([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)
    assert correlation([1, 1], [2, 2]) == 0.0


# ---------- M15 ----------

def test_rebalance_guards_and_min_trades():
    current = {"A": 0.10, "B": 0.20, "C": 0.05, "D": 0.10, "E": 0.10}
    target = {"A": 0.15, "B": 0.10, "C": 0.055, "D": 0.16, "E": 0.16}
    trades = plan_rebalance(
        current, target,
        liquidity_ok=lambda s, d: s != "A",
        near_expiry=lambda s: s == "B",
        event_locked=lambda s: s == "D",
        trade_cost_fn=lambda s, d: 100.0,
        expected_benefit_fn=lambda s, d: 50.0 if s == "E" else 500.0,
    )
    by = {t.symbol: t for t in trades}
    assert "C" not in by                                    # 0.5% drift < 2% threshold
    assert by["A"].skipped_reason == "illiquid"
    assert by["B"].skipped_reason == "near_fo_expiry"
    assert by["D"].skipped_reason == "event_lockout"
    assert by["E"].skipped_reason == "cost_exceeds_benefit"
    executable = [t for t in trades if not t.skipped_reason]
    assert executable == []                                  # every skip has a named reason


def test_rebalance_executes_clean_trades():
    trades = plan_rebalance({"A": 0.0}, {"A": 0.10},
                            liquidity_ok=lambda s, d: True, near_expiry=lambda s: False,
                            event_locked=lambda s: False,
                            trade_cost_fn=lambda s, d: 10.0,
                            expected_benefit_fn=lambda s, d: 1000.0)
    assert len(trades) == 1 and trades[0].direction == "buy" and not trades[0].skipped_reason


# ---------- M16 ----------

def test_reconciler_flags_every_mismatch_class():
    internal = [
        {"client_order_id": "1", "symbol": "A", "qty": 100, "price": 100.0},
        {"client_order_id": "2", "symbol": "B", "qty": 50, "price": 200.0},
        {"client_order_id": "3", "symbol": "C", "qty": 10, "price": 300.0},
    ]
    broker = [
        {"client_order_id": "1", "symbol": "A", "qty": 100, "price": 100.2},   # within 0.5% tol
        {"client_order_id": "2", "symbol": "B", "qty": 40, "price": 200.0},    # qty mismatch
        {"client_order_id": "4", "symbol": "D", "qty": 5, "price": 50.0},      # unknown internally
    ]
    rep = reconcile("2026-08-04", internal, broker, naked_positions=["Z"])
    assert rep.missing_at_broker == ["3"]
    assert rep.missing_internally == ["4"]
    assert rep.qty_mismatches == [("2", 50, 40)]
    assert rep.price_mismatches == []
    assert rep.naked_positions == ["Z"] and not rep.clean


def test_reconciler_clean_day():
    rows = [{"client_order_id": "1", "symbol": "A", "qty": 1, "price": 10.0}]
    assert reconcile("d", rows, rows, []).clean


def test_price_mismatch_beyond_tolerance():
    internal = [{"client_order_id": "1", "symbol": "A", "qty": 1, "price": 100.0}]
    broker = [{"client_order_id": "1", "symbol": "A", "qty": 1, "price": 101.0}]  # 1% > 0.5%
    assert reconcile("d", internal, broker, []).price_mismatches


# ---------- M17 ----------

GOOD = dict(algo_id="ALGO-1", algo_registered=True, routes_via_broker_api=True,
            static_ip_confirmed=True, max_orders_per_sec=5, is_black_box=True,
            black_box_ra_registration_resolved=True, audit_retention_years=5)


def test_sebi_all_checks_pass():
    checks = validate_sebi_compliance(GOOD, exchange_ops_threshold=10)
    assert all(checks.values())


@pytest.mark.parametrize("mutation,expected", [
    ({"algo_registered": False}, "algo_registered_with_exchange"),
    ({"algo_id": ""}, "algo_id_present"),
    ({"routes_via_broker_api": False}, "routes_via_broker_api"),
    ({"static_ip_confirmed": False}, "static_ip_whitelisted"),
    ({"max_orders_per_sec": 50}, "ops_within_threshold"),
    ({"black_box_ra_registration_resolved": False}, "black_box_question_resolved"),
    ({"audit_retention_years": 1}, "audit_retention_5y"),
])
def test_sebi_each_check_blocks(mutation, expected):
    bad = dict(GOOD)
    bad.update(mutation)
    with pytest.raises(ComplianceError) as e:
        validate_sebi_compliance(bad, exchange_ops_threshold=10)
    assert expected in str(e.value)


def test_black_box_cannot_be_self_resolved_by_default():
    """The RA determination is a human/legal call — unset ⇒ hard block."""
    strategy = dict(GOOD)
    strategy.pop("black_box_ra_registration_resolved")
    with pytest.raises(ComplianceError):
        validate_sebi_compliance(strategy, exchange_ops_threshold=10)


# ---------- pipeline ----------

def test_normalize_ohlcv_gaps_documented_not_interpolated():
    rows = [
        {"ts": 1, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
        {"ts": 2, "open": 10.5, "high": 12, "low": 10, "close": 11, "volume": 200},
        {"ts": 10, "open": 11, "high": 13, "low": 11, "close": 12, "volume": 150},   # gap
        {"ts": 3, "open": "bad", "high": 1, "low": 2, "close": 1, "volume": 1},       # malformed
        {"ts": 4, "open": 10, "high": 9, "low": 10, "close": 9.5, "volume": 5},       # high<low
    ]
    clean, report = normalize_ohlcv("RELIANCE", rows, expected_step=1.0)
    assert report.rows == 3 and report.dropped_malformed == 2
    assert report.gaps == [(2, 10)]                       # documented
    assert [r["ts"] for r in clean] == [1, 2, 10]          # sorted, no synthetic rows
