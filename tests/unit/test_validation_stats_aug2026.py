"""MODULE 71 — validation statistics pinned to the PUBLISHED anchors.
Every number below appears in the Bailey / Lopez de Prado papers; if an edit
ever moves one of these, the module no longer computes the published math.
Units: psr/dsr/mintrl take PER-PERIOD Sharpe (annualized / sqrt(bars_per_yr));
minbtl takes N trials and a target ANNUALIZED Sharpe, returns years."""
import math

import pytest

from src.ops.validation_stats import dsr, emax_z, minbtl, mintrl, neff, psr


def test_emax_of_100_trials_matches_paper():
    assert emax_z(100) == pytest.approx(2.5306, abs=1e-3)     # ~2.53 published


def test_dsr_worked_example_matches_paper():
    # JPM 2014 worked example: annSR 2.5 (250d year), T=1250, skew -3,
    # kurt 10, N=100, Var[{SR}]=0.5 ann -> DSR ~ 0.9004
    got, sr0 = dsr(2.5 / math.sqrt(250), 1250, -3.0, 10.0, 100, 0.5 / 250)
    assert got == pytest.approx(0.9004, abs=2e-3)
    assert sr0 == pytest.approx(0.1132, abs=2e-3)             # paper's SR0


def test_mintrl_normal_case_closed_form():
    # annSR 1.0 daily, normal returns: ~685 observations (~2.7y)
    got = mintrl(1.0 / math.sqrt(252), 0.0, 3.0)
    assert got == pytest.approx(685, abs=5)


def test_minbtl_paper_anchor():
    # AMS 2014 Thm 2: N=45 trials -> ~5 years for target annSR 1.0
    assert minbtl(45, 1.0) == pytest.approx(5.0, abs=0.05)


def test_minbtl_campaign_reality():
    # THIS campaign: 32 disclosed trials -> ~4.4 years required. Six months
    # of data supports 2 independent trials. Gate-0 encodes this.
    assert minbtl(32, 1.0) == pytest.approx(4.41, abs=0.05)


def test_noise_alone_sharpe_at_our_data_length():
    # expected best in-sample annualized Sharpe from PURE NOISE with 32
    # trials on 0.5 years: emax_z(32)/sqrt(0.5) ~ 2.97
    assert emax_z(32) / math.sqrt(0.5) == pytest.approx(2.97, abs=0.02)


def test_neff_interpolation():
    assert neff(32, 0.0) == pytest.approx(32.0)
    assert neff(32, 1.0) == pytest.approx(1.0)
    assert neff(32, 0.7) == pytest.approx(0.7 + 0.3 * 32)


def test_psr_monotone_in_track_length():
    a = psr(1.5 / math.sqrt(252), 126, 0.0, 3.0)
    b = psr(1.5 / math.sqrt(252), 1260, 0.0, 3.0)
    assert b > a
