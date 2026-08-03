"""Wave 7 tests — M31 DSR (probability!), M30 regime filter, M32 walk-forward,
M33 miner attrition, M29 loader, M37 labels/fusion/abstain, M38b orchestrator."""
import pytest

from src.core.config_loader import load_config
from src.data.historical_data_loader import DATA_SOURCES, load_full_history
from src.learning.deflated_sharpe import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    norm_cdf,
    norm_ppf,
    probabilistic_sharpe_ratio,
)
from src.learning.learning_orchestrator import (
    MODEL_CHANGING,
    ChampionChallengerGate,
    autopsy_error,
    autopsy_win,
    weight_changes_allowed,
)
from src.learning.pattern_miner import discover_patterns, find_motifs_fallback, to_log_returns
from src.learning.regime_filter import RegimeResult, evaluate_across_regimes
from src.learning.walk_forward import walk_forward_test
from src.ml.news_reaction_model import (
    ModelOutput,
    apply_abstain,
    assert_no_lookahead,
    build_features,
    build_labels,
    entry_context_allowed,
    fuse,
)

CFG = load_config("config/master.yaml")
PD = CFG.model_extra["pattern_discovery"]


# ---------- M31 DSR ----------

def test_norm_functions_sanity():
    assert norm_ppf(0.975) == pytest.approx(1.9599, abs=1e-3)
    assert norm_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
    assert norm_cdf(norm_ppf(0.3)) == pytest.approx(0.3, abs=1e-6)


def test_dsr_is_probability_and_penalizes_trials():
    few = deflated_sharpe_ratio(observed_sr=0.15, num_trials=5, num_returns=2000,
                                sharpe_variance=0.01)
    many = deflated_sharpe_ratio(observed_sr=0.15, num_trials=5000, num_returns=2000,
                                 sharpe_variance=0.01)
    assert 0.0 <= many <= few <= 1.0
    assert few > 0.9 and many < few   # multiple-testing penalty bites


def test_psr_half_at_benchmark():
    assert probabilistic_sharpe_ratio(0.1, 0.1, 252) == pytest.approx(0.5)


def test_expected_max_sharpe_grows_with_trials():
    assert expected_max_sharpe(1) == 0.0
    assert expected_max_sharpe(1000) > expected_max_sharpe(10) > 0


def test_v2_gate_is_probability_scale():
    assert 0 < PD["min_dsr_probability"] <= 1.0   # the v1 "1.5" bug stays dead


# ---------- M30 regime filter ----------

def occurrences(dates_wins):
    return [{"date": d, "win": w} for d, w in dates_wins]


def test_regime_filter_min_occurrence_and_coverage():
    occs = occurrences([
        ("2003-01-01", True), ("2004-01-01", True), ("2005-01-01", True),   # dotcom bull: 3 wins
        ("2015-01-01", True), ("2016-01-01", True), ("2017-01-01", False),  # taper: 2/3
        ("2020-05-01", True), ("2020-06-01", True),                          # covid: only 2 → fails bar
    ])
    r = evaluate_across_regimes(occs, data_start="2000-01-01")
    assert isinstance(r, RegimeResult)
    assert r.detail["pre_2000"] is None                     # excluded — data starts 2000
    assert r.detail["dotcom_bull_2000_2008"]["passed"] is True
    assert r.detail["taper_bull_2013_2019"]["passed"] is True          # 2/3 = 0.667 > 0.55
    assert r.detail["covid_2020"]["passed"] is False                    # n<3 counts as FAILED
    assert r.regimes_evaluated == 6                                     # 7 minus uncovered era


# ---------- M32 walk-forward ----------

async def test_walk_forward_majority_rule_and_min_segments():
    async def rediscover(pattern, train_end):
        return True

    async def backtest_good(pattern, s, e):
        return 0.5 if s % 4 else -0.1     # profitable in 3 of 4 segments

    async def backtest_bad(pattern, s, e):
        return -0.5

    good = await walk_forward_test({}, 1996, 2026, rediscover, backtest_good)
    bad = await walk_forward_test({}, 1996, 2026, rediscover, backtest_bad)
    assert good.passed and good.profitable_fraction > 0.5
    assert not bad.passed

    async def rarely_found(pattern, train_end):
        return train_end in (2016, 2017)   # only 2 segments < MIN 5

    few = await walk_forward_test({}, 1996, 2026, rarely_found, backtest_good)
    assert not few.passed


# ---------- M33 miner ----------

def repeating_series(n_cycles=40, m=20):
    """Embed a repeating shape in noise so the motif finder has something real."""
    import random
    rng = random.Random(2)
    shape = [0.01 * ((i % 5) - 2) for i in range(m)]
    closes = [100.0]
    for _ in range(n_cycles):
        for s in shape:
            closes.append(closes[-1] * (1 + s + rng.gauss(0, 0.0005)))
    return closes


async def test_miner_attrition_logged_and_expensive_stage_gated():
    closes = repeating_series()
    attribution_calls = []

    def occurrence_dates(idx):
        return [{"date": "2015-01-01", "win": True} for _ in idx]

    def regime_pass(occs):
        return RegimeResult(regimes_passed=6, regimes_evaluated=7, detail={})

    def regime_fail(occs):
        return RegimeResult(regimes_passed=1, regimes_evaluated=7, detail={})

    async def attribution(pattern):
        attribution_calls.append(1)
        return {"cause": "x"}

    async def wf_pass(pattern):
        from src.learning.walk_forward import WalkForwardResult
        return WalkForwardResult(True, [{}] * 6, 1.0)

    surfaced = []

    async def surface(pats):
        surfaced.extend(pats)

    survivors, log = await discover_patterns(
        closes, occurrence_dates, {"min_occurrences": 3, "min_regimes_passed": 5,
                                   "min_dsr_probability": 0.0},
        regime_filter_fn=regime_pass, attribution_fn=attribution,
        dsr_fn=lambda p: 1.0, walk_forward_fn=wf_pass, surface_fn=surface)
    assert log.survived["motifs"] >= 1
    assert len(attribution_calls) == log.survived["regime_filter"]  # expensive stage gated
    assert surfaced == survivors and len(survivors) >= 1

    attribution_calls.clear()
    survivors2, log2 = await discover_patterns(
        closes, occurrence_dates, {"min_occurrences": 3, "min_regimes_passed": 5,
                                   "min_dsr_probability": 0.95},
        regime_filter_fn=regime_fail, attribution_fn=attribution,
        dsr_fn=lambda p: 1.0, walk_forward_fn=wf_pass, surface_fn=surface)
    assert survivors2 == [] and attribution_calls == []             # attrition by design


def test_motif_finder_needs_repetition():
    import random
    rng = random.Random(3)
    noise = [100.0]
    for _ in range(800):
        noise.append(noise[-1] * (1 + rng.gauss(0, 0.01)))
    assert find_motifs_fallback(to_log_returns(noise), m=20, min_occurrences=10) == []


# ---------- M29 loader ----------

async def test_loader_registry_and_quality_report():
    async def fetch(source, start):
        return [{"ts": 1, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
                {"ts": 2, "open": 1.5, "high": 2, "low": 1, "close": 1.8, "volume": 12},
                {"ts": 9, "open": 1.8, "high": 2, "low": 1.5, "close": 1.9, "volume": 8}]

    rows, report = await load_full_history("nifty50", fetch, expected_step=1.0)
    assert report.rows == 3 and report.gaps == [(2, 9)]
    with pytest.raises(KeyError):
        await load_full_history("unknown", fetch)
    assert "btcusd" in DATA_SOURCES                     # crypto history registered


# ---------- M37 ----------

def test_labels_direction_magnitude_and_fade_drift():
    prices = {"t0": 100.0, "5m": 100.4, "1h": 100.9, "1d": 103.0, "5d": 104.0}
    lab = build_labels(0.0, prices, atr_at_event=2.0)
    assert lab["1h"]["direction"] == "up" and lab["1h"]["magnitude"] == "small"   # 0.45 ATR
    assert lab["1d"]["magnitude"] == "large"                                      # 1.5 ATR boundary => large
    assert lab["persistence"] == "drift"                                          # 1h and 1h→1d same sign
    fade = build_labels(0.0, {"t0": 100, "5m": 101, "1h": 102, "1d": 100.5, "5d": 100.0}, 2.0)
    assert fade["persistence"] == "fade"


def test_no_lookahead_assert():
    with pytest.raises(ValueError):
        assert_no_lookahead(feature_ts=200.0, event_ts=100.0)


def test_features_include_vix_interaction_and_cluster():
    f = build_features({"cluster_size": 4, "first_seen_at": 50.0},
                       {"vol_percentile": 0.8, "trend_state": "RANGE", "hurst": 0.5},
                       vix=20.0, event_ts=100.0)
    assert f["cluster_size"] == 4 and f["vix_x_vol"] == pytest.approx(16.0)


def out(horizon="1d", conf=0.8, mag="large", p_fade=0.3):
    return ModelOutput(horizon=horizon, p_direction=0.7, direction="up",
                       magnitude=mag, p_fade=p_fade, confidence=conf)


def test_fusion_table_and_tier0_invariant():
    st, floor = 7, 0.6
    assert fuse(9, out(conf=0.4), severity_threshold=st, abstain_floor=floor) == "fallback_severity_rules"
    assert fuse(9, out(mag="small"), severity_threshold=st, abstain_floor=floor) == "false_alarm_filter"
    assert fuse(9, out(p_fade=0.7), severity_threshold=st, abstain_floor=floor) == "tighten_exits_book_partials"
    assert fuse(9, out(), severity_threshold=st, abstain_floor=floor) == "pause_entries_then_reassess"
    # Tier-0 invariant: no fusion output can resume entries or override the guard
    from src.ml.news_reaction_model import FUSION_ACTIONS
    assert not any("resume" in a or "override" in a for a in FUSION_ACTIONS)


def test_5m_head_is_risk_only():
    assert entry_context_allowed(out(horizon="5m")) is False
    assert entry_context_allowed(out(horizon="1d")) is True
    assert apply_abstain(out(conf=0.59), 0.6) is True


# ---------- M38b ----------

def test_error_autopsy_five_causes_and_noise_learns_nothing():
    assert autopsy_error({"input_defect": True}) == "bad_input"
    assert autopsy_error({"known_missing_feature": True}) == "missing_feature"
    assert autopsy_error({"regime_shifted": True}) == "regime_shift"
    assert autopsy_error({"in_regime": True, "well_fed": True, "confident": True}) == "model_wrong"
    assert autopsy_error({}) == "irreducible_noise"
    assert "bad_input" not in MODEL_CHANGING and "irreducible_noise" not in MODEL_CHANGING


def test_win_autopsy_luck_vs_skill():
    assert autopsy_win({"reasoning_validated": True}) == "skill"
    assert autopsy_win({}) == "lucky_win_near_miss"


def test_promotion_gate_needs_both_metrics_and_human():
    gate = ChampionChallengerGate()
    champ = {"brier": 0.20, "after_cost_pnl": 100.0}
    better = {"brier": 0.15, "after_cost_pnl": 150.0}
    half = {"brier": 0.15, "after_cost_pnl": 50.0}
    assert not gate.evaluate(champ, half, human_approved=True).promoted
    assert not gate.evaluate(champ, better, human_approved=False).promoted
    assert gate.evaluate(champ, better, human_approved=True).promoted


def test_weights_only_change_monthly():
    assert weight_changes_allowed("monthly") is True
    for cadence in ("per_prediction", "nightly", "weekly", "quarterly"):
        assert weight_changes_allowed(cadence) is False
