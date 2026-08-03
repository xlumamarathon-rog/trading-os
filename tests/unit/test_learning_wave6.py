"""Wave 6 tests — M38a ledger, M19 attribution, M20 memory, M21 injector, M22 lessons,
M23 backtests (after-cost), M24 approval gate, M25 holdout+bootstrap, M26/27/28."""
import random

import pytest

from src.core.config_loader import load_config
from src.learning.backtest_runner import BacktestResult, flag_significant_moves, run_with_costs
from src.learning.case_memory import CaseMemory, cosine
from src.learning.failure_classifier import classify
from src.learning.holdout_validator import HoldoutValidator, bootstrap_p_value, max_drawdown, sharpe
from src.learning.lesson_extractor import extract_lesson, validate_lesson
from src.learning.live_failure_monitor import consecutive_losses, should_trigger
from src.learning.news_attribution import find_cause, rank_causes
from src.learning.prediction_ledger import LedgerImmutabilityError, PredictionLedger
from src.learning.retrieval_context_injector import get_grounded_decision
from src.learning.rule_auditor import weekly_audit
from src.learning.strategy_config_engine import (
    HumanApprovalRequired,
    StrategyConfigEngine,
    calculate_consistency,
)
from src.risk.pre_trade_gate import AuditLog

CFG = load_config("config/master.yaml")


# ---------- M38a ledger ----------

def test_ledger_append_settle_and_immutability():
    led = PredictionLedger()
    eid = led.record(model_version="v1", kind="news_reaction",
                     prediction={"direction": "up", "p": 0.7},
                     features={"vix": 14.2}, action_taken="acted")
    led.settle(eid, {"hit": True})
    with pytest.raises(LedgerImmutabilityError):
        led.settle(eid, {"hit": False})            # no hindsight edits
    with pytest.raises(LedgerImmutabilityError):
        led.amend_features(eid, {"vix": 99})       # features frozen


def test_ledger_brier_and_auto_demote():
    led = PredictionLedger(drift_window=20, brier_demote_threshold=0.30)
    # well-calibrated: p=0.9 and 90% hits
    rng = random.Random(1)
    for _ in range(50):
        eid = led.record(model_version="v1", kind="news_reaction",
                         prediction={"p": 0.9}, features={}, action_taken="acted")
        led.settle(eid, {"hit": rng.random() < 0.9})
    assert led.brier_score() < 0.2 and not led.should_demote()
    # confidently wrong: p=0.9, only 20% hits — must self-demote
    for _ in range(30):
        eid = led.record(model_version="v2", kind="news_reaction",
                         prediction={"p": 0.9}, features={}, action_taken="acted")
        led.settle(eid, {"hit": rng.random() < 0.2})
    assert led.should_demote() is True


# ---------- M19 attribution ----------

CANDS = [
    {"headline": "RELIANCE wins arbitration", "published_at": 900.0, "tickers": ["RELIANCE"], "sentiment": 1.0},
    {"headline": "Unrelated macro chatter", "published_at": 100_000.0, "tickers": [], "sentiment": 0.0},
    {"headline": "Analysts react to move", "published_at": 1500.0, "tickers": ["RELIANCE"], "sentiment": 1.0},
]


def test_attribution_ranks_and_flags_ex_post():
    move_ts = 1000.0
    cause, ranked = find_cause(move_ts, move_direction=1, instrument="RELIANCE", candidates=CANDS)
    assert cause is not None and "arbitration" in cause.headline
    ex_post = [c for c in ranked if c.ex_post]
    assert ex_post and all("react" in c.headline for c in ex_post)   # explanation-only flag


def test_attribution_no_clear_cause_is_explicit():
    cause, ranked = find_cause(1000.0, 1, "TCS", [
        {"headline": "way outside window", "published_at": 500_000.0, "tickers": ["TCS"], "sentiment": 1.0}])
    assert cause is None


# ---------- M20 memory + M21 injector ----------

async def fake_embed(text: str):
    rng = random.Random(hash(text) % 10_000)
    return [rng.random() for _ in range(16)]


CASE = {"ticker": "RELIANCE", "setup": "gap up on arbitration win, high volume",
        "news": {"headline": "RELIANCE wins arbitration"}, "causal_chain": "legal win → re-rating",
        "crowd_emotion": {"greed": 0.7}, "your_action": {"did": "nothing"},
        "outcome": 0.05, "lesson": "Legal wins with volume confirmation tend to drift for days."}


async def test_case_store_requires_context_and_retrieves_similar():
    mem = CaseMemory(fake_embed)
    with pytest.raises(ValueError):
        await mem.store_case({"setup": "incomplete"})
    await mem.store_case(CASE)
    await mem.store_case({**CASE, "setup": "totally different chop day", "outcome": -0.01})
    top = await mem.query_similar("gap up on arbitration win, high volume", top_k=1)
    assert len(top) == 1
    assert cosine([1, 0], [1, 0]) == 1.0 and cosine([1, 0], [0, 1]) == 0.0


async def test_injector_ab_logs_both_arms():
    mem = CaseMemory(fake_embed)
    await mem.store_case(CASE)
    ab = []

    async def decide(ticker, context):
        return {"direction": "BUY", "had_context": context is not None}

    d1 = await get_grounded_decision("RELIANCE", CASE["setup"], mem, decide, ab, use_precedent=True)
    d2 = await get_grounded_decision("RELIANCE", CASE["setup"], mem, decide, ab, use_precedent=False)
    assert d1["had_context"] and not d2["had_context"]
    assert [r["with_precedent"] for r in ab] == [True, False]


# ---------- M22 lessons ----------

def test_lesson_validation_rules():
    ok, _ = validate_lesson("Volume-confirmed legal wins tend to drift upward for days.", CASE)
    assert ok
    assert not validate_lesson("Two sentences. Here is another.", CASE)[0]
    assert not validate_lesson("RELIANCE went up after the arbitration.", CASE)[0]  # not transferable
    assert not validate_lesson(" ".join(["word"] * 50) + ".", CASE)[0]


async def test_extract_lesson_rejects_bad_llm_output():
    async def bad_llm(prompt):
        return "RELIANCE will always go up. Buy it."

    with pytest.raises(ValueError):
        await extract_lesson(CASE, bad_llm)


# ---------- M23 backtests (after-cost, L4) ----------

def test_backtest_is_after_cost_and_flags_moves():
    prices = [{"ts": i, "close": 100 + i} for i in range(10)]
    prices.append({"ts": 10, "close": 113.0})       # +2.6% jump flagged
    signals = [{"ts": i, "action": "buy" if i == 0 else ("sell" if i == 9 else "hold")}
               for i in range(11)]
    res = run_with_costs(prices, signals, qty=100, india_costs=CFG.execution_costs.india)
    assert isinstance(res, BacktestResult)
    assert res.total_costs > 0 and res.net_pnl == pytest.approx(res.gross_pnl - res.total_costs)
    assert any(m["move_pct"] > 0.02 for m in res.flagged_moves)
    assert flag_significant_moves([{"ts": 0, "close": 100}, {"ts": 1, "close": 100.5}]) == []


# ---------- M24 approval gate ----------

async def test_rule_gate_evidence_bar_and_human_approval():
    audit = AuditLog()

    class AlwaysPassHoldout:
        async def test(self, rule):
            return {"passed": True}

    eng = StrategyConfigEngine(CFG.model_extra["learning_loop"], AlwaysPassHoldout(), audit)
    weak = await eng.propose_rule({"id": "r1"}, [{"outcome": 0.01}])          # < min cases
    assert weak is None
    inconsistent = await eng.propose_rule({"id": "r1"},
                                          [{"outcome": 0.01}, {"outcome": -0.02}, {"outcome": -0.03}])
    assert inconsistent is None                                               # consistency 0.67 < 0.7
    ok = await eng.propose_rule({"id": "r1"},
                                [{"outcome": 0.01}, {"outcome": 0.02}, {"outcome": 0.03}])
    assert ok == {"passed": True}
    with pytest.raises(HumanApprovalRequired):
        await eng.apply_rule("r1", {"rule": "x"})                             # no human
    await eng.apply_rule("r1", {"rule": "x"}, approved_by_human=True)
    assert "r1" in eng.active_rules
    with pytest.raises(HumanApprovalRequired):
        await eng.deactivate_rule("r1")                                       # same gate both ways
    refused = [r for r in audit.rows if r["type"] == "rule_apply_refused"]
    assert refused and audit.verify_chain()


def test_consistency_math():
    assert calculate_consistency([{"outcome": 1}, {"outcome": 1}, {"outcome": -1}]) == pytest.approx(2 / 3)


# ---------- M25 holdout ----------

async def test_holdout_bootstrap_accepts_real_edge_rejects_noise():
    rng = random.Random(5)
    base = [rng.gauss(0.0, 0.01) for _ in range(120)]

    async def backtest_real(rule):
        if rule is None:
            return base
        return [r + 0.004 for r in base]           # genuine uplift

    async def backtest_noise(rule):
        if rule is None:
            return base
        return [r + rng.gauss(0, 0.0001) for r in base]

    real = await HoldoutValidator(backtest_real).test({"id": "edge"})
    noise = await HoldoutValidator(backtest_noise).test({"id": "noise"})
    assert real.passed is True and real.p_value < 0.1
    assert noise.passed is False


async def test_holdout_consumed_once_per_quarter():
    async def bt(rule):
        return [0.01] * 30

    hv = HoldoutValidator(bt)
    first = await hv.test({"id": "r"})
    second = await hv.test({"id": "r"})
    assert second.reason == "holdout_already_consumed_this_quarter" and not second.passed


def test_sharpe_and_drawdown_helpers():
    assert sharpe([0.01] * 10) == 0.0 or sharpe([0.01] * 10) > 0  # zero-sd guard
    assert max_drawdown([0.5, -0.5]) == pytest.approx(0.5)
    assert bootstrap_p_value([-0.01] * 20) == 1.0                  # negative mean can't pass


# ---------- M26/M27/M28 ----------

def test_failure_triggers():
    losses = [{"pnl": -1}, {"pnl": -1}, {"pnl": -1}]
    assert consecutive_losses([{"pnl": 2}] + losses) == 3
    assert should_trigger(losses, False, 0.01, 0.05)[0] is True
    assert should_trigger([{"pnl": 1}], True, 0.0, 0.0) == (True, "daily_loss_breached")
    assert should_trigger([{"pnl": 1}], False, 0.12, 0.05)[0] is True   # 2x drawdown
    assert should_trigger([{"pnl": 1}], False, 0.01, 0.05)[0] is False


def test_failure_classifier_priority_order():
    ttl = CFG.model_extra["cache_ttl"]["sentiment_signal"]
    assert classify([{"signal_cache_age_at_trade": ttl + 1}], ttl) == "stale_signal_cache"
    assert classify([{"rule_active_during_trade": True}], ttl) == "overfit_historical_rule"
    assert classify([{"crowd_emotion": {"mechanical_flag": True}}], ttl) == "mechanical_move_misread"
    assert classify([{"news_published_before_trade": True}], ttl) == "news_blindspot"
    assert classify([{"portfolio_correlation_spiked": True}], ttl) == "correlation_breakdown"
    assert classify([{}], ttl) == "regime_mismatch"


async def test_rule_auditor_flags_only_mature_underperformers():
    import time as _t
    rules = {
        "young": {"applied_at": _t.time() - 10 * 86400},
        "mature_bad": {"applied_at": _t.time() - 100 * 86400},
        "mature_good": {"applied_at": _t.time() - 100 * 86400},
    }

    async def perf(rule_id):
        return {"young": 100.0, "mature_bad": 50.0, "mature_good": 500.0}[rule_id]

    async def shadow(rule_id):
        return {"young": 999.0, "mature_bad": 200.0, "mature_good": 100.0}[rule_id]

    flags = await weekly_audit(rules, perf, shadow,
                               CFG.model_extra["learning_loop"]["rule_audit_period_days"])
    assert [f["rule_id"] for f in flags] == ["mature_bad"]
