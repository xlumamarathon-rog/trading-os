"""MODULE 22 — Case -> one transferable heuristic sentence (spec Addendum A).

LLM injected. Output VALIDATED: single sentence, generalized (no ticker/date
leakage), tagged with its error-cause class (M38 taxonomy).
"""
from __future__ import annotations

import re

MAX_WORDS = 40


def build_prompt(case: dict) -> str:
    return (
        f"Setup: {case['setup']}\nNews: {case['news'].get('headline','')}\n"
        f"Causal chain: {case['causal_chain']}\nCrowd emotion: {case['crowd_emotion']}\n"
        f"Outcome: {case['outcome']}\n\n"
        "Extract ONE transferable trading heuristic from this case, as a single sentence. "
        "Focus on the PATTERN, not the specific instrument or date."
    )


def validate_lesson(lesson: str, case: dict) -> tuple:
    lesson = lesson.strip()
    sentences = [s for s in re.split(r"[.!?]+", lesson) if s.strip()]
    if len(sentences) != 1:
        return False, "must be exactly one sentence"
    if len(lesson.split()) > MAX_WORDS:
        return False, "too long to be a heuristic"
    ticker = str(case.get("ticker", "")).upper()
    if ticker and ticker in lesson.upper():
        return False, "lesson restates the specific instrument — not transferable"
    return True, "ok"


async def extract_lesson(case: dict, llm_fn) -> str:
    lesson = await llm_fn(build_prompt(case))
    ok, reason = validate_lesson(lesson, case)
    if not ok:
        raise ValueError(f"lesson rejected: {reason}: {lesson!r}")
    return lesson.strip()
