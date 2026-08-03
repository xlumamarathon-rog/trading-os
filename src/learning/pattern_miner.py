"""MODULE 33 — Six-stage discovery pipeline orchestrator (v2 input fix).

Stage order = cost order: motifs (cheap) -> regime filter -> attribution
(survivors only) -> DSR >= 0.95 -> walk-forward -> HUMAN review. Motif search
runs on Z-NORMALIZED LOG RETURNS, never raw prices. stumpy is used when
installed; the in-repo fallback is a correlation motif finder (same interface).
Attrition per stage is logged — heavy attrition is the design working.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field


def to_log_returns(closes: list) -> list:
    return [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0]


def znorm(window: list) -> list:
    m = sum(window) / len(window)
    sd = statistics.pstdev(window) or 1e-12
    return [(x - m) / sd for x in window]


def _corr(a: list, b: list) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a)) or 1e-12
    sb = math.sqrt(sum((x - mb) ** 2 for x in b)) or 1e-12
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def find_motifs_fallback(returns: list, m: int, min_occurrences: int,
                         corr_threshold: float = 0.9, max_seeds: int = 50) -> list:
    """Simple seed-and-match motif finder over z-normalized return windows."""
    if len(returns) < m * 3:
        return []
    windows = [znorm(returns[i:i + m]) for i in range(0, len(returns) - m, m // 2 or 1)]
    motifs, used = [], set()
    for si, seed in enumerate(windows[:max_seeds]):
        if si in used:
            continue
        matches = [i for i, w in enumerate(windows) if _corr(seed, w) >= corr_threshold]
        if len(matches) >= min_occurrences:
            used.update(matches)
            motifs.append({"signature": seed, "occurrence_idx": matches})
    return motifs


@dataclass
class StageLog:
    entered: dict = field(default_factory=dict)
    survived: dict = field(default_factory=dict)

    def record(self, stage: str, entered: int, survived: int) -> None:
        self.entered[stage] = entered
        self.survived[stage] = survived


async def discover_patterns(closes: list, occurrence_dates_fn, cfg: dict, *,
                            regime_filter_fn, attribution_fn, dsr_fn,
                            walk_forward_fn, surface_fn, motif_fn=None) -> tuple:
    """Injected stage functions; returns (survivors, StageLog)."""
    log = StageLog()
    returns = to_log_returns(closes)
    motif_finder = motif_fn or find_motifs_fallback
    candidates = motif_finder(returns, m=20, min_occurrences=int(cfg["min_occurrences"]))
    log.record("motifs", 1, len(candidates))

    survivors = []
    regime_pass, attr_pass, dsr_pass = 0, 0, 0
    for pattern in candidates:
        pattern["occurrences"] = occurrence_dates_fn(pattern["occurrence_idx"])
        rr = regime_filter_fn(pattern["occurrences"])
        if rr.regimes_passed < int(cfg["min_regimes_passed"]):
            continue
        regime_pass += 1
        pattern["attribution"] = await attribution_fn(pattern)     # survivors only (cost)
        attr_pass += 1
        if dsr_fn(pattern) < float(cfg["min_dsr_probability"]):
            continue
        dsr_pass += 1
        wf = await walk_forward_fn(pattern)
        if not wf.passed:
            continue
        survivors.append(pattern)
    log.record("regime_filter", len(candidates), regime_pass)
    log.record("attribution", regime_pass, attr_pass)
    log.record("dsr", attr_pass, dsr_pass)
    log.record("walk_forward", dsr_pass, len(survivors))
    await surface_fn(survivors)                                     # HUMAN review, never live
    return survivors, log
