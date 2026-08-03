"""MODULE 13 — ai-berkshire conviction -> Black-Litterman views (spec §Phase 3)."""
from __future__ import annotations

FAIL_HAIRCUT = 0.3
GREY_CONFIDENCE = 0.3
FAIL_CONFIDENCE = 0.6
PASS_BOOST = 0.5


def conviction_to_bl_view(ticker: str, verdict: str, conviction: float,
                          baseline_return: float) -> dict:
    if verdict not in ("Pass", "Fail", "Grey"):
        raise ValueError(f"unknown verdict {verdict}")
    if not 0.0 <= conviction <= 1.0:
        raise ValueError("conviction must be 0..1")
    if verdict == "Pass":
        view, conf = baseline_return * (1 + conviction * PASS_BOOST), conviction
    elif verdict == "Fail":
        view, conf = baseline_return * (1 - FAIL_HAIRCUT), FAIL_CONFIDENCE
    else:
        view, conf = baseline_return, GREY_CONFIDENCE
    return {"ticker": ticker, "view_return": view, "confidence": conf}
