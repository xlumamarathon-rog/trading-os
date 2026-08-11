"""MODULE 66 — Risk Optimizer (Aug 2026). The "advanced math" module, honest.

Answers the operator's real question — "how do I maximize profit?" — with the
mathematics instead of a slogan. For per-trade risk fraction f and trade
outcomes R (in R-multiples of the amount risked), terminal wealth compounds
as ∏(1 + f·Rᵢ), so long-run growth per trade is

    g(f) = E[log(1 + f·R)]

The Kelly fraction f* maximizes g over the EMPIRICAL distribution of the
system's own closed trades — no win/loss binomial approximation, the actual
R histogram. The curve's shape IS the lesson: g rises to f*, then falls;
risking beyond f* produces LESS growth with MORE violence, deterministically.
Vince's optimal-f caution and this repo's half-Kelly cap (risk_limits
kelly_cap: 0.5) are the same result.

Everything here is descriptive analytics computed from realized numbers —
this module changes NO trading behavior. It reports where the configured
risk-per-trade sits on the growth curve and what drawdowns to expect there
(bootstrap Monte Carlo), so the sizing debate happens on evidence.

Stdlib only (spec: lean runtime). Deterministic (seeded) Monte Carlo.
"""
from __future__ import annotations

import math
import random
from typing import Optional, Sequence


def edge_report(rs: Sequence[float]) -> dict:
    """Realized-edge summary with a Wilson 95% interval on win rate —
    uncertainty stated, never hidden (small samples lie confidently)."""
    n = len(rs)
    if n == 0:
        return {"n": 0}
    wins = sum(1 for r in rs if r > 0)
    p = wins / n
    z = 1.96
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    mean_r = sum(rs) / n
    var = sum((r - mean_r) ** 2 for r in rs) / n if n > 1 else 0.0
    return {"n": n, "win_rate": round(p, 4),
            "win_rate_ci95": [round(max(0.0, centre - half), 4),
                              round(min(1.0, centre + half), 4)],
            "avg_r": round(mean_r, 4), "std_r": round(math.sqrt(var), 4),
            "best_r": round(max(rs), 2), "worst_r": round(min(rs), 2)}


def growth_per_trade(rs: Sequence[float], f: float) -> Optional[float]:
    """g(f) = mean log-growth per trade at risk fraction f. None where any
    single trade would wipe the account (f beyond the domain)."""
    if f < 0:
        return None
    if f == 0:
        return 0.0
    total = 0.0
    for r in rs:
        w = 1.0 + f * r
        if w <= 0:
            return None
        total += math.log(w)
    return total / len(rs)


def kelly_fraction(rs: Sequence[float]) -> dict:
    """Empirical Kelly f*: numeric maximization of g(f) on the domain
    (0, f_max) where f_max keeps the worst realized trade survivable.
    Golden-section search — g is concave on the domain."""
    rs = [r for r in rs if r is not None]
    if not rs:
        return {"f_star": None, "reason": "no trades"}
    worst = min(rs)
    if worst >= 0:
        # no realized losing trade: Kelly is unbounded on this sample —
        # that's a SAMPLE artifact, never a licence to lever up
        return {"f_star": None,
                "reason": "no losing trade in sample — Kelly undefined "
                          "(unbounded); collect more history"}
    if all(r <= 0 for r in rs) or sum(rs) / len(rs) <= 0:
        return {"f_star": 0.0,
                "reason": "non-positive expectancy — the growth-optimal "
                          "risk is ZERO; no sizing fixes a negative edge"}
    f_max = 0.999 / abs(worst)
    lo, hi = 0.0, f_max
    invphi = (math.sqrt(5) - 1) / 2
    a, b = hi - invphi * (hi - lo), lo + invphi * (hi - lo)
    ga, gb = growth_per_trade(rs, a), growth_per_trade(rs, b)
    for _ in range(80):
        if ga is None or (gb is not None and gb > ga):
            lo, a, ga = a, b, gb
            b = lo + invphi * (hi - lo)
            gb = growth_per_trade(rs, b)
        else:
            hi, b, gb = b, a, ga
            a = hi - invphi * (hi - lo)
            ga = growth_per_trade(rs, a)
    f_star = (lo + hi) / 2
    return {"f_star": round(f_star, 4), "f_max_survivable": round(f_max, 4),
            "g_at_f_star": round(growth_per_trade(rs, f_star) or 0.0, 6)}


def growth_curve(rs: Sequence[float], f_star: float, points: int = 25) -> list:
    """(f, g) samples from 0 to 2×Kelly — the shape that shows the cliff."""
    out = []
    top = max(f_star * 2, 0.02)
    for i in range(points + 1):
        f = top * i / points
        g = growth_per_trade(rs, f)
        out.append({"f": round(f, 4),
                    "g": None if g is None else round(g, 6)})
    return out


def monte_carlo_drawdown(rs: Sequence[float], f: float, *, paths: int = 2000,
                         horizon: Optional[int] = None,
                         seed: int = 11) -> dict:
    """Bootstrap the system's own R distribution into equity paths at risk
    fraction f; report the max-drawdown distribution. Deterministic seed —
    the cockpit shows the same numbers on every refresh."""
    if not rs or f <= 0:
        return {"f": f, "paths": 0}
    rng = random.Random(seed)
    horizon = horizon or max(len(rs), 50)
    dds, ruined = [], 0
    for _ in range(paths):
        equity, peak, max_dd = 1.0, 1.0, 0.0
        for _ in range(horizon):
            r = rs[rng.randrange(len(rs))]
            equity *= max(1.0 + f * r, 0.0)
            if equity <= 0.0:
                max_dd = 1.0
                break
            peak = max(peak, equity)
            max_dd = max(max_dd, 1.0 - equity / peak)
        dds.append(max_dd)
        if max_dd >= 0.5:
            ruined += 1
    dds.sort()
    q = lambda p: dds[min(len(dds) - 1, int(p * len(dds)))]
    return {"f": round(f, 4), "paths": paths, "horizon": horizon,
            "dd_p50_pct": round(q(0.50) * 100, 1),
            "dd_p95_pct": round(q(0.95) * 100, 1),
            "p_dd_over_10pct": round(sum(1 for d in dds if d > 0.10) / paths, 3),
            "p_dd_over_20pct": round(sum(1 for d in dds if d > 0.20) / paths, 3),
            "p_ruin_50pct_dd": round(ruined / paths, 4)}


def report(rs: Sequence[float], configured_risk_pct: float = 0.01) -> dict:
    """The full risk-math report the cockpit renders. configured_risk_pct is
    risk_limits.max_risk_per_trade_pct — where the operator ACTUALLY sits."""
    rs = [float(r) for r in rs if r is not None]
    edge = edge_report(rs)
    if edge.get("n", 0) < 10:
        return {"edge": edge,
                "verdict": f"only {edge.get('n', 0)} closed trades — the "
                           "math needs more history before any sizing claim "
                           "(minimum 10, honest minimum ~50)"}
    kelly = kelly_fraction(rs)
    out = {"edge": edge, "kelly": kelly,
           "configured_risk_pct": configured_risk_pct}
    f_star = kelly.get("f_star")
    if f_star is None:
        out["verdict"] = kelly.get("reason", "Kelly undefined on this sample")
        return out
    if f_star == 0.0:
        out["verdict"] = kelly["reason"]
        out["mc_at_configured"] = monte_carlo_drawdown(rs, configured_risk_pct)
        return out
    half, quarter = f_star / 2, f_star / 4
    out["fractions"] = {
        "kelly": round(f_star, 4), "half_kelly": round(half, 4),
        "quarter_kelly": round(quarter, 4),
        "configured": configured_risk_pct,
        "configured_vs_kelly": round(configured_risk_pct / f_star, 3),
    }
    out["growth"] = {
        "at_configured": round(growth_per_trade(rs, configured_risk_pct) or 0, 6),
        "at_half_kelly": round(growth_per_trade(rs, half) or 0, 6),
        "at_kelly": round(growth_per_trade(rs, f_star) or 0, 6),
        "at_2x_kelly": (lambda g: None if g is None else round(g, 6))(
            growth_per_trade(rs, 2 * f_star)),
    }
    out["curve"] = growth_curve(rs, f_star)
    out["mc_at_configured"] = monte_carlo_drawdown(rs, configured_risk_pct)
    out["mc_at_half_kelly"] = monte_carlo_drawdown(rs, half)
    out["mc_at_kelly"] = monte_carlo_drawdown(rs, f_star)
    ratio = configured_risk_pct / f_star
    if ratio > 1.0:
        out["verdict"] = (f"configured risk {configured_risk_pct:.1%} is "
                          f"{ratio:.1f}× Kelly — mathematically OVER the "
                          "growth peak: lower risk would raise growth AND "
                          "cut drawdowns. Reduce.")
    elif ratio > 0.5:
        out["verdict"] = (f"configured risk {configured_risk_pct:.1%} is "
                          f"{ratio:.0%} of Kelly — inside the aggressive "
                          "half-to-full-Kelly band; acceptable only if the "
                          "P95 drawdown shown is genuinely tolerable.")
    else:
        out["verdict"] = (f"configured risk {configured_risk_pct:.1%} is "
                          f"{ratio:.0%} of empirical Kelly ({f_star:.1%}) — "
                          "conservative by design; growth headroom exists "
                          "but so does estimation error. The cap that "
                          "protects you from a bad sample is the same cap "
                          "that costs growth on a good one.")
    return out
