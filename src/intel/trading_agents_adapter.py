"""TradingAgents integration adapter — wired to the VERIFIED vendor API (R1).

Verified against vendor/TradingAgents (2026-08):
  - TradingAgentsGraph.propagate(company_name, trade_date, asset_type="stock")
    -> (final_state, processed_signal)   [graph/trading_graph.py:362]
  - SignalProcessor.process_signal(text) -> one of
    Buy / Overweight / Hold / Underweight / Sell   [graph/signal_processing.py]

This adapter converts that 5-grade rating into our cache schema
{direction, confidence}. Confidence mapping is an ADAPTER POLICY (TradingAgents
emits no probability) — it seeds the ledger, which calibrates it over time.
The vendor package imports lazily: missing deps degrade to a clear error, never
a silent fallback (R4/R5).
"""
from __future__ import annotations

from typing import Optional

RATING_MAP = {
    "buy": ("BUY", 0.80),
    "overweight": ("BUY", 0.65),
    "hold": ("HOLD", 0.50),
    "underweight": ("SELL", 0.65),
    "sell": ("SELL", 0.80),
}


class TradingAgentsUnavailable(RuntimeError):
    pass


def rating_to_signal(rating: str) -> tuple:
    key = rating.strip().lower()
    if key not in RATING_MAP:
        raise ValueError(f"unknown TradingAgents rating: {rating!r}")
    return RATING_MAP[key]


class TradingAgentsAdapter:
    """graph is injected (tests) or built lazily from the vendor package (VPS)."""

    def __init__(self, graph=None) -> None:
        self._graph = graph

    def _ensure_graph(self):
        if self._graph is None:
            try:
                from tradingagents.graph.trading_graph import TradingAgentsGraph  # type: ignore
            except ImportError as exc:
                raise TradingAgentsUnavailable(
                    "tradingagents not importable — install vendor/TradingAgents deps "
                    "on the VPS (see vendor/MANIFEST.md)") from exc
            self._graph = TradingAgentsGraph()
        return self._graph

    async def compute_signal(self, ticker: str, trade_date: str) -> dict:
        """-> {ticker, direction, confidence, rating, source} for SentimentCache."""
        graph = self._ensure_graph()
        result = graph.propagate(ticker, trade_date)          # verified signature
        # propagate returns (final_state, processed_signal) per vendor source
        processed = result[1] if isinstance(result, tuple) else str(result)
        direction, confidence = rating_to_signal(processed)
        return {"ticker": ticker, "direction": direction, "confidence": confidence,
                "rating": processed, "source": "trading_agents"}
