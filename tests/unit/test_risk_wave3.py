"""Wave 3 risk tests — M5 (VaR/ES/Kupiec), M6 (bands), M7 (scenarios), M8 (audit chain), M9, M39."""
import math

import pytest

from src.core.config_loader import load_config
from src.risk.gex_map import compute_gex
from src.risk.greeks_aggregator import aggregate_portfolio_greeks
from src.risk.india_risk_config import IndiaRiskConfig
from src.risk.pre_trade_gate import AuditLog, PreTradeGate
from src.risk.stress_runner import load_all, load_scenario, run_scenario
from src.risk.var_worker import (
    VarWorker,
    ewma_vol_forecast,
    expected_shortfall,
    historical_var,
    kupiec_pof,
    portfolio_returns,
)
from tests.fixtures.fakes import FakeRedis

CFG = load_config("config/master.yaml")


# ---------------- M5 VaR ----------------

def test_historical_var_known_distribution():
    # 100 returns: exactly 5 losses of -5%, rest +1% → VaR95 = 5%
    returns = [-0.05] * 5 + [0.01] * 95
    assert historical_var(returns, 0.95) == pytest.approx(0.05)
    assert historical_var(returns, 0.99) == pytest.approx(0.05)


def test_es_geq_var():
    returns = [-0.10, -0.05, -0.04] + [0.01] * 97
    var = historical_var(returns, 0.95)
    es = expected_shortfall(returns, 0.95)
    assert es >= var


def test_kupiec_accepts_correct_model_rejects_bad():
    lr_ok, ok = kupiec_pof(breaches=13, days=250, alpha=0.05)      # ~5.2% — fine
    lr_bad, bad = kupiec_pof(breaches=38, days=250, alpha=0.05)    # 15% — broken model
    assert ok is True and bad is False and lr_bad > lr_ok
    with pytest.raises(ValueError):
        kupiec_pof(-1, 250, 0.05)


def test_ewma_vol_positive_and_reacts_to_shocks():
    calm = ewma_vol_forecast([0.001] * 50)
    shocked = ewma_vol_forecast([0.001] * 49 + [0.05])
    assert shocked > calm > 0


def test_portfolio_returns_weighting():
    rets = portfolio_returns({"A": 0.5, "B": 0.5}, {"A": [0.02, -0.02], "B": [0.0, 0.0]})
    assert rets == [0.01, -0.01]


async def test_var_worker_writes_cache_and_heartbeat():
    redis = FakeRedis()

    async def positions():
        return {"A": 1.0}

    async def returns():
        return {"A": [-0.05] * 5 + [0.01] * 95}

    worker = VarWorker(redis, ttl_seconds=300, positions_fn=positions, returns_fn=returns)
    snap = await worker.refresh_once()
    assert float(redis.store["portfolio:var:95"]) == pytest.approx(0.05)
    assert "portfolio:es:95" in redis.store and "heartbeat:var_worker" in redis.store
    assert snap.vol_model in ("garch", "ewma_fallback")


# ---------------- M6 bands ----------------

def test_price_band_within_and_breach():
    rc = IndiaRiskConfig(band_map={"SMALLCAP": 0.05})
    assert rc.check_price_band("SMALLCAP", 104.0, 100.0).ok            # 4% < 5%
    d = rc.check_price_band("SMALLCAP", 106.0, 100.0)                  # 6% >= 5%
    assert not d.ok and d.reason == "would_breach_price_band"
    assert rc.check_price_band("BIGCAP", 118.0, 100.0).ok              # default 20%


def test_mwpl_ban_and_lot_validation():
    rc = IndiaRiskConfig(mwpl_banned={"BANNED"}, lot_sizes={"NIFTYFUT": 75})
    assert not rc.check_price_band("BANNED", 101.0, 100.0).ok
    assert rc.validate_lot("NIFTYFUT", 150) is True
    assert rc.validate_lot("NIFTYFUT", 100) is False
    assert rc.validate_lot("CASHEQ", 17) is True


def test_index_circuit_stages():
    assert IndiaRiskConfig.index_circuit_stage(0.05) == 0
    assert IndiaRiskConfig.index_circuit_stage(-0.11) == 1
    assert IndiaRiskConfig.index_circuit_stage(0.16) == 2
    assert IndiaRiskConfig.index_circuit_stage(-0.25) == 3


def test_illegal_band_rejected():
    rc = IndiaRiskConfig(band_map={"X": 0.07})
    with pytest.raises(ValueError):
        rc.get_band("X")


# ---------------- M7 scenarios ----------------

PORTFOLIO = [
    {"symbol": "NIFTYBEES", "qty": 1000, "price": 250.0, "asset_class": "india_equity"},
    {"symbol": "BTCUSD", "qty": 0.5, "price": 60000.0, "asset_class": "crypto"},
    {"symbol": "GOLDBEES", "qty": 500, "price": 60.0, "asset_class": "gold"},
]


def test_all_scenarios_load_and_validate():
    scenarios = load_all("scenarios")
    assert len(scenarios) >= 9
    names = {s["name"] for s in scenarios}
    assert {"covid_crash_2020", "gfc_2008", "crypto_winter_2022"} <= names


def test_covid_scenario_hand_computed():
    s = load_scenario("scenarios/covid_crash_2020.json")
    r = run_scenario(s, PORTFOLIO)
    expected = 250_000 * -0.35 + 30_000 * -0.40 + 30_000 * 0.05
    assert r.pnl == pytest.approx(expected)
    assert r.per_position["NIFTYBEES"] == pytest.approx(-87_500)


def test_unknown_asset_class_is_zero_shock():
    s = load_scenario("scenarios/covid_crash_2020.json")
    r = run_scenario(s, [{"symbol": "X", "qty": 1, "price": 100.0, "asset_class": "weird"}])
    assert r.pnl == 0.0


# ---------------- M8 audit chain + gate ----------------

def test_audit_chain_verifies_and_detects_tamper():
    log = AuditLog()
    for i in range(5):
        log.append({"type": "test", "i": i})
    assert log.verify_chain() is True
    log._rows[2]["i"] = 999  # tamper
    assert log.verify_chain() is False


def test_gate_rejections_are_audited_with_reason():
    log = AuditLog()
    gate = PreTradeGate(CFG.risk_limits, log)
    ok = gate.check({"symbol": "X"}, var_95=0.001)
    var_reject = gate.check({"symbol": "X"}, var_95=0.03)
    sector_reject = gate.check({"symbol": "X"}, var_95=0.001, sector_exposure_pct=0.25)
    assert ok.approved
    assert not var_reject.approved and var_reject.reason == "var_at_limit"
    assert not sector_reject.approved and sector_reject.reason == "sector_exposure_cap"
    assert len(log.rows) == 3 and log.verify_chain()


# ---------------- M9 greeks ----------------

async def test_greeks_aggregation():
    async def positions():
        return [{"symbol": "NIFTY24CE", "quantity": 75}, {"symbol": "NIFTY24PE", "quantity": -75}]

    async def greeks(symbol):
        return {"delta": 0.5, "gamma": 0.01, "theta": -5.0, "vega": 10.0} if "CE" in symbol \
            else {"delta": -0.4, "gamma": 0.012, "theta": -4.0, "vega": 9.0}

    totals = await aggregate_portfolio_greeks(positions, greeks)
    assert totals["delta"] == pytest.approx(0.5 * 75 + (-0.4) * -75)
    assert totals["gamma"] == pytest.approx(0.01 * 75 + 0.012 * -75)


# ---------------- M39 GEX ----------------

CHAIN = [
    {"strike": 24000, "call_gamma": 0.0001, "call_oi": 1000, "put_gamma": 0.0002, "put_oi": 3000},
    {"strike": 25000, "call_gamma": 0.0003, "call_oi": 5000, "put_gamma": 0.0003, "put_oi": 2000},
    {"strike": 26000, "call_gamma": 0.0001, "call_oi": 2000, "put_gamma": 0.0001, "put_oi": 500},
]


def test_gex_hand_computed_and_regime():
    r = compute_gex(CHAIN, spot=25000.0)
    k25 = (0.0003 * 5000 - 0.0003 * 2000) * 25000 ** 2 * 0.01
    assert r.by_strike[25000] == pytest.approx(k25)
    assert r.regime in ("amplify", "dampen")
    # heavy put OI at 24000 makes that strike net negative
    assert r.by_strike[24000] < 0


def test_gex_pin_candidates_ranked_by_magnitude():
    r = compute_gex(CHAIN, spot=25000.0)
    mags = [abs(r.by_strike[k]) for k in r.pin_candidates]
    assert mags == sorted(mags, reverse=True)


def test_gex_invalid_spot():
    with pytest.raises(ValueError):
        compute_gex(CHAIN, spot=0)
