"""MODULE 37 — News-Reaction Model: labels, features, abstain, fusion (Addendum D).

The LEARNED part (LightGBM heads) trains on the VPS with FNSPID/GDELT data —
gated behind champion/challenger. What lives HERE and is fully tested:
  - label construction (ATR-normalized forward returns -> direction/magnitude/
    fade-vs-drift), strict <=T feature discipline helpers
  - abstain logic (confidence floor)
  - the FUSION table combining severity + model heads into ONE protective/
    informative action — which can NEVER veto Tier 0/1 (no such output exists).
Evidence encoded: 5m head is risk-only (Lopez-Lira); vol-regime interaction
mandatory (Conrad 2025); cluster_size is an impact feature (dissemination paper).
"""
from __future__ import annotations

from dataclasses import dataclass

MAGNITUDE_BUCKETS = ((0.5, "small"), (1.5, "medium"))     # in ATR units; above = "large"
FUSION_ACTIONS = ("pause_entries_then_reassess", "tighten_exits_book_partials",
                  "false_alarm_filter", "informed_entry_context", "fallback_severity_rules")
ENTRY_HORIZONS = ("1d", "5d")                              # 5m/1h are risk-only


def magnitude_bucket(abs_move_atr: float) -> str:
    for bound, name in MAGNITUDE_BUCKETS:
        if abs_move_atr < bound:
            return name
    return "large"


def build_labels(event_ts: float, prices: dict, atr_at_event: float) -> dict:
    """prices: {"t0": p, "5m": p, "1h": p, "1d": p, "5d": p} — forward snapshots.
    Returns per-horizon direction/magnitude + fade_vs_drift (1h vs 1h->1d)."""
    if atr_at_event <= 0 or prices["t0"] <= 0:
        raise ValueError("invalid atr/price")
    out = {}
    for h in ("5m", "1h", "1d", "5d"):
        move = (prices[h] - prices["t0"]) / prices["t0"]
        move_atr = move * prices["t0"] / atr_at_event
        out[h] = {"direction": "up" if move > 0 else ("down" if move < 0 else "none"),
                  "magnitude": magnitude_bucket(abs(move_atr)), "move_atr": move_atr}
    initial = prices["1h"] - prices["t0"]
    drift_leg = prices["1d"] - prices["1h"]
    out["persistence"] = "drift" if initial * drift_leg > 0 else "fade"
    return out


def assert_no_lookahead(feature_ts: float, event_ts: float) -> None:
    if feature_ts > event_ts:
        raise ValueError(f"lookahead leak: feature at {feature_ts} > event {event_ts}")


def build_features(news: dict, regime: dict, vix: float, event_ts: float) -> dict:
    for key in ("first_seen_at",):
        assert_no_lookahead(news.get(key, 0.0), event_ts + 1.0)
    feats = {
        "cluster_size": int(news.get("cluster_size", 1)),          # dissemination impact
        "source_count": int(news.get("cluster_size", 1)),
        "vol_percentile": float(regime.get("vol_percentile", 0.5)),
        "trend_state": regime.get("trend_state", "RANGE"),
        "hurst": float(regime.get("hurst", 0.5)),
        "vix": float(vix),
        "vix_x_vol": float(vix) * float(regime.get("vol_percentile", 0.5)),  # Conrad 2025
    }
    return feats


@dataclass
class ModelOutput:
    horizon: str
    p_direction: float          # max class prob
    direction: str
    magnitude: str
    p_fade: float
    confidence: float


def apply_abstain(output: ModelOutput, floor: float) -> bool:
    """True = ABSTAIN (fall back to severity rules)."""
    return output.confidence < floor


def fuse(severity: int, output: ModelOutput, *, severity_threshold: int,
         abstain_floor: float) -> str:
    """Protective/informative action ONLY — there is no output that resumes
    entries or overrides Tier 0/1 (spec §12.3 invariant by construction)."""
    if apply_abstain(output, abstain_floor):
        return "fallback_severity_rules"
    if severity < severity_threshold:
        return "false_alarm_filter" if output.magnitude == "small" else "fallback_severity_rules"
    if output.magnitude == "small":
        return "false_alarm_filter"
    if output.p_fade >= 0.5:
        return "tighten_exits_book_partials"
    if output.horizon in ENTRY_HORIZONS:
        return "pause_entries_then_reassess"        # entry context arrives AFTER reassess
    return "pause_entries_then_reassess"            # 5m/1h heads are risk-only


def entry_context_allowed(output: ModelOutput) -> bool:
    """5m/1h heads may never produce entry context (Lopez-Lira evidence)."""
    return output.horizon in ENTRY_HORIZONS
