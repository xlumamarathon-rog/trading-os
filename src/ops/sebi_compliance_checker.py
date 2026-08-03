"""MODULE 17 — SEBI compliance gate, REWRITTEN for the Feb-2025 framework (v2).

Circular: SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 (Feb 4, 2025) — "Safer
participation of retail investors in Algorithmic trading". Phased from
Oct 1 2025; fully applicable Apr 1 2026. Each check cites its clause area.
BLOCKS deployment (raises) on any failure — incl. the black-box/RA question,
which is a HUMAN/legal determination this code refuses to self-resolve.
"""
from __future__ import annotations


class ComplianceError(RuntimeError):
    pass


def validate_sebi_compliance(strategy: dict, exchange_ops_threshold: int) -> dict:
    """strategy keys: algo_id, algo_registered, routes_via_broker_api,
    static_ip_confirmed, max_orders_per_sec, is_black_box,
    black_box_ra_registration_resolved, audit_retention_years."""
    checks = {
        # Para: algo registration & exchange-issued Algo ID tagged on every order
        "algo_registered_with_exchange": bool(strategy.get("algo_registered")),
        "algo_id_present": bool(strategy.get("algo_id")),
        # Para: all retail algo orders flow through the registered broker API
        "routes_via_broker_api": bool(strategy.get("routes_via_broker_api")),
        # Para: static IP whitelisting for API order placement
        "static_ip_whitelisted": bool(strategy.get("static_ip_confirmed")),
        # Para: orders-per-second threshold for unregistered algos
        "ops_within_threshold": int(strategy.get("max_orders_per_sec", 0)) <= exchange_ops_threshold,
        # Para: black-box algos (non-disclosable logic — LLM/ML signals plausibly qualify)
        # require provider Research Analyst registration. Human/legal decision required.
        "black_box_question_resolved": (not strategy.get("is_black_box", True))
                                        or bool(strategy.get("black_box_ra_registration_resolved")),
        # Audit trail retention (5 years)
        "audit_retention_5y": int(strategy.get("audit_retention_years", 0)) >= 5,
    }
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        raise ComplianceError(f"SEBI Feb-2025 checks failed: {failed}")
    return checks
