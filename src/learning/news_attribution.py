"""MODULE 19 — Find the likely cause of a price move (spec Addendum A).

Ranked candidates with scores — never a single blind guess; explicit
no-clear-cause verdict (mechanical/technical move). v2 rule: candidates AFTER
the move are usable for EXPLANATION only — flagged ex_post, excluded from any
ex-ante rule derivation.
"""
from __future__ import annotations

from dataclasses import dataclass

WINDOW_BEFORE_H = 4.0
WINDOW_AFTER_H = 1.0
MIN_CONFIDENCE = 0.35


@dataclass
class Cause:
    headline: str
    score: float
    temporal_score: float
    entity_score: float
    direction_score: float
    ex_post: bool                  # published after the move — explanation only


def _temporal(move_ts: float, pub_ts: float) -> float:
    dt_h = (move_ts - pub_ts) / 3600.0
    if -WINDOW_AFTER_H <= dt_h <= WINDOW_BEFORE_H:
        return 1.0 - abs(dt_h) / max(WINDOW_BEFORE_H, WINDOW_AFTER_H)
    return 0.0


def rank_causes(move_ts: float, move_direction: int, instrument: str,
                candidates: list) -> list:
    """candidates: [{headline, published_at, tickers, sentiment (-1..1)}]."""
    out = []
    for c in candidates:
        t = _temporal(move_ts, c["published_at"])
        if t == 0.0:
            continue
        e = 1.0 if instrument.upper() in [x.upper() for x in c.get("tickers", [])] else 0.2
        s = c.get("sentiment", 0.0)
        d = 1.0 if s * move_direction > 0 else (0.5 if s == 0 else 0.0)
        score = 0.4 * t + 0.35 * e + 0.25 * d
        out.append(Cause(c["headline"], score, t, e, d, ex_post=c["published_at"] > move_ts))
    return sorted(out, key=lambda c: c.score, reverse=True)


def find_cause(move_ts: float, move_direction: int, instrument: str, candidates: list):
    ranked = rank_causes(move_ts, move_direction, instrument, candidates)
    if not ranked or ranked[0].score < MIN_CONFIDENCE:
        return None, ranked        # explicit "no clear news cause" (mechanical move)
    return ranked[0], ranked
