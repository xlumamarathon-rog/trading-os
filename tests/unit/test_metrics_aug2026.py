"""Cross-validation for src/ops/metrics.py (Aug 2026).

Three guarantees:
1. Deterministic formula lock on hand-computable inputs.
2. PARITY: metrics.py reproduces the research harness's existing inline
   Sharpe / max-drawdown exactly — proof the enrichment changed no certified
   number, only added Sortino/Calmar/vol.
3. REFERENCE: on real committed equity curves, every metric equals empyrical
   (the awesome-systematic-trading-catalogued standard) to < 1e-9. Skips
   cleanly where empyrical isn't installed (mirrors the vendor-canary pattern).
"""
import json
import math
from pathlib import Path

import pytest

from src.ops import metrics as M

CURVES = ["data/real_replay/equity_curve.json", "data/covid_replay/equity_curve.json"]


def _equity(path):
    return [p["equity"] for p in json.loads(Path(path).read_text())]


# ---------- 1. deterministic ----------

def test_max_drawdown_known_curve():
    # 100 -> 120 -> 90 -> 110 : peak 120, trough 90 -> 25% DD
    assert M.max_drawdown([100, 120, 90, 110]) == pytest.approx(0.25)


def test_sharpe_zero_when_flat():
    assert M.sharpe_ratio([0.0, 0.0, 0.0]) == 0.0


def test_sortino_only_penalizes_downside():
    # all-positive returns -> no downside -> defined-as-0 by convention
    assert M.sortino_ratio([0.01, 0.02, 0.03]) == 0.0


# ---------- 2. parity with the research harness's inline formulas ----------

def _inline_sharpe_mdd(eq):
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    rets = [(eq[k] / eq[k - 1] - 1) for k in range(1, len(eq))]
    mu = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1))
    sharpe = (mu / sd * math.sqrt(252)) if sd > 0 else 0.0
    return sharpe, mdd


@pytest.mark.parametrize("path", CURVES)
def test_metrics_match_inline_harness_formulas(path):
    eq = _equity(path)
    inline_sharpe, inline_mdd = _inline_sharpe_mdd(eq)
    rets = M.simple_returns(eq)
    assert M.sharpe_ratio(rets) == pytest.approx(inline_sharpe, abs=1e-12)
    assert M.max_drawdown(eq) == pytest.approx(inline_mdd, abs=1e-12)


# ---------- 3. reference parity vs empyrical ----------

@pytest.mark.parametrize("path", CURVES)
def test_metrics_match_empyrical(path):
    emp = pytest.importorskip("empyrical")
    import numpy as np
    eq = _equity(path)
    r = np.array(M.simple_returns(eq))
    assert M.sharpe_ratio(M.simple_returns(eq)) == pytest.approx(
        emp.sharpe_ratio(r, risk_free=0, period="daily"), abs=1e-9)
    assert M.max_drawdown(eq) == pytest.approx(abs(emp.max_drawdown(r)), abs=1e-9)
    assert M.sortino_ratio(M.simple_returns(eq)) == pytest.approx(
        emp.sortino_ratio(r), abs=1e-9)
    assert M.calmar_ratio(eq) == pytest.approx(emp.calmar_ratio(r), abs=1e-9)
    assert M.annual_volatility(M.simple_returns(eq)) == pytest.approx(
        emp.annual_volatility(r), abs=1e-9)


def test_summary_block_has_all_fields():
    s = M.summary(_equity(CURVES[0]))
    for k in ("sharpe_annualized", "sortino_annualized", "calmar_ratio",
              "annual_vol_pct", "max_drawdown_pct", "cagr_pct"):
        assert k in s
