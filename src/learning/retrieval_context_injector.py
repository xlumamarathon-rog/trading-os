"""MODULE 21 — Precedent injection with mandatory A/B logging (spec Addendum A)."""
from __future__ import annotations


def format_cases_as_precedent(cases: list) -> str:
    lines = ["Similar historical precedents (use as calibration, not gospel):"]
    for i, c in enumerate(cases, 1):
        lines.append(f"{i}. Setup: {c['setup']} | News: {c['news'].get('headline','-')} | "
                     f"Outcome: {c['outcome']:+.2%} | Lesson: {c['lesson']}")
    return "\n".join(lines)


async def get_grounded_decision(ticker: str, current_setup: str, case_memory,
                                decide_fn, ab_log: list, use_precedent: bool = True) -> dict:
    """decide_fn(ticker, context|None) -> decision dict. Every call A/B-logged (M38 meta-loop)."""
    cases = await case_memory.query_similar(current_setup) if use_precedent else []
    context = format_cases_as_precedent(cases) if cases else None
    decision = await decide_fn(ticker, context)
    ab_log.append({"ticker": ticker, "with_precedent": bool(cases), "n_cases": len(cases),
                   "decision": decision})
    return decision
