"""MODULE 31 — Deflated Sharpe Ratio (v2 CORRECTED: outputs a PROBABILITY).

Bailey & Lopez de Prado: DSR = PSR evaluated against the expected MAX Sharpe
under N independent trials. Gate: dsr >= config.pattern_discovery.min_dsr_probability
(default 0.95). Penalty grows with num_trials — scanning thousands of patterns
must pay for it statistically.
"""
from __future__ import annotations

import math

EULER_GAMMA = 0.5772156649015329


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Acklam rational approximation — |error| < 1.15e-9, plenty for DSR."""
    if not 0.0 < p < 1.0:
        raise ValueError("p in (0,1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q /            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def expected_max_sharpe(num_trials: int, sharpe_variance: float = 1.0) -> float:
    """E[max SR] across N independent trials with SR ~ N(0, var)."""
    if num_trials < 1:
        raise ValueError("num_trials >= 1")
    if num_trials == 1:
        return 0.0
    n = float(num_trials)
    return math.sqrt(sharpe_variance) * (
        (1 - EULER_GAMMA) * norm_ppf(1 - 1 / n) + EULER_GAMMA * norm_ppf(1 - 1 / (n * math.e))
    )


def probabilistic_sharpe_ratio(observed_sr: float, benchmark_sr: float, num_returns: int,
                               skewness: float = 0.0, kurtosis: float = 3.0) -> float:
    if num_returns < 2:
        raise ValueError("need >= 2 returns")
    denom = math.sqrt(max(1e-12, 1 - skewness * observed_sr
                          + (kurtosis - 1) / 4.0 * observed_sr ** 2))
    z = (observed_sr - benchmark_sr) * math.sqrt(num_returns - 1) / denom
    return norm_cdf(z)


def deflated_sharpe_ratio(observed_sr: float, num_trials: int, num_returns: int,
                          skewness: float = 0.0, kurtosis: float = 3.0,
                          sharpe_variance: float = 1.0) -> float:
    """PROBABILITY that the observed SR beats the best-of-N-noise benchmark."""
    benchmark = expected_max_sharpe(num_trials, sharpe_variance)
    return probabilistic_sharpe_ratio(observed_sr, benchmark, num_returns, skewness, kurtosis)
