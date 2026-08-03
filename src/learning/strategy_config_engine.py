"""MODULE 24 — Rule lifecycle with a NON-BYPASSABLE human approval gate."""
from __future__ import annotations

import statistics
import time


class HumanApprovalRequired(PermissionError):
    pass


def calculate_consistency(supporting_cases: list) -> float:
    """Fraction of cases whose outcome sign matches the majority sign."""
    if not supporting_cases:
        return 0.0
    signs = [1 if c["outcome"] > 0 else -1 for c in supporting_cases]
    majority = 1 if sum(signs) >= 0 else -1
    return sum(1 for s in signs if s == majority) / len(signs)


class StrategyConfigEngine:
    def __init__(self, learning_cfg: dict, holdout_validator, audit_log) -> None:
        self.cfg = learning_cfg
        self.holdout = holdout_validator
        self.audit = audit_log
        self.active_rules: dict[str, dict] = {}

    async def propose_rule(self, candidate_rule: dict, supporting_cases: list):
        if len(supporting_cases) < int(self.cfg["min_matching_cases"]):
            return None
        consistency = calculate_consistency(supporting_cases)
        if consistency < float(self.cfg["min_consistency_score"]):
            return None
        return await self.holdout.test(candidate_rule)

    async def apply_rule(self, rule_id: str, rule: dict, approved_by_human: bool = False) -> None:
        if not approved_by_human:
            # Non-bypassable default. The override is NOT a parameter here by design —
            # it requires a separate explicit config flag + confirmation phrase (spec §12.4).
            self.audit.append({"type": "rule_apply_refused", "rule_id": rule_id,
                               "reason": "human approval missing"})
            raise HumanApprovalRequired("rules require explicit human approval")
        rule = dict(rule)
        rule["applied_at"] = time.time()
        self.active_rules[rule_id] = rule
        self.audit.append({"type": "rule_applied", "rule_id": rule_id})

    async def deactivate_rule(self, rule_id: str, approved_by_human: bool = False) -> None:
        if not approved_by_human:
            raise HumanApprovalRequired("deactivation needs the same gate as activation")
        self.active_rules.pop(rule_id, None)
        self.audit.append({"type": "rule_deactivated", "rule_id": rule_id})
