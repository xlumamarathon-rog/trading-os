"""MODULE 38b — Learning Orchestrator: autopsies, cadences, promotion gate.

Learning is continuous; DEPLOYMENT is gated. Error autopsy assigns exactly one
of five causes — three of which must NOT touch the model. Wins are audited too
(luck-vs-skill). Champion/challenger promotion needs BOTH metrics better AND a
human click (same discipline as M24).
"""
from __future__ import annotations

from dataclasses import dataclass

ERROR_CAUSES = ("bad_input", "missing_feature", "regime_shift", "model_wrong",
                "irreducible_noise")
MODEL_CHANGING = {"missing_feature", "model_wrong", "regime_shift"}


def autopsy_error(row: dict) -> str:
    """row: settled ledger entry dict + diagnostics flags."""
    if row.get("input_defect"):                    # late timestamp, stale feature
        return "bad_input"                          # fix pipeline — DON'T touch model
    if row.get("known_missing_feature"):
        return "missing_feature"
    if row.get("regime_shifted"):
        return "regime_shift"
    if row.get("in_regime") and row.get("well_fed") and row.get("confident"):
        return "model_wrong"                        # the real training signal
    return "irreducible_noise"                      # LEARN NOTHING — logged verdict


def autopsy_win(row: dict) -> str:
    """skill = right reasoning -> outcome; luck = wrong reasoning bailed out."""
    return "skill" if row.get("reasoning_validated") else "lucky_win_near_miss"


@dataclass
class PromotionDecision:
    promoted: bool
    reason: str


class ChampionChallengerGate:
    def __init__(self, require_human: bool = True) -> None:
        self.require_human = require_human

    def evaluate(self, champion: dict, challenger: dict,
                 human_approved: bool = False) -> PromotionDecision:
        """metrics: {"brier": lower better, "after_cost_pnl": higher better}."""
        better_brier = challenger["brier"] < champion["brier"]
        better_pnl = challenger["after_cost_pnl"] > champion["after_cost_pnl"]
        if not (better_brier and better_pnl):
            return PromotionDecision(False, "challenger does not beat incumbent on BOTH metrics")
        if self.require_human and not human_approved:
            return PromotionDecision(False, "human approval required")
        return PromotionDecision(True, "promoted")


CADENCES = {
    "per_prediction": ["ledger_append", "drift_monitor"],
    "nightly": ["training_store_append", "isotonic_recalibration_only"],
    "weekly": ["error_autopsies", "win_autopsies", "lesson_extraction", "rule_flags"],
    "monthly": ["full_retrain", "champion_challenger_gate"],
    "quarterly": ["feature_search", "rule_pruning", "meta_audit"],
}


def allowed_actions(cadence: str) -> list:
    return CADENCES[cadence]


def weight_changes_allowed(cadence: str) -> bool:
    """Weights may only change via the monthly gated retrain — never nightly."""
    return cadence == "monthly"
