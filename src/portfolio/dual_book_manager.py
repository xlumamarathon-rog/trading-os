"""MODULE 14 — Three sub-books (INR / USD forex / USD crypto-CFD) -> one view (v2)."""
from __future__ import annotations

import statistics
from dataclasses import dataclass

CORR_WARN = 0.7


def correlation(a: list, b: list) -> float:
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    a, b = a[-n:], b[-n:]
    sa, sb = statistics.pstdev(a), statistics.pstdev(b)
    if sa == 0 or sb == 0:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / n
    return cov / (sa * sb)


@dataclass
class UnifiedAllocation:
    weights_inr_terms: dict
    cross_correlations: dict
    warnings: list


class DualBookManager:
    def __init__(self, optimizers: dict, usdinr_fn, crypto_budget_pct: float = 0.10) -> None:
        """optimizers: {"india": fn, "mt5_forex": fn, "mt5_crypto": fn} -> {symbol: weight}
        crypto sub-book gets its OWN risk budget so its vol can't consume the forex book."""
        self.optimizers = optimizers
        self.usdinr_fn = usdinr_fn
        self.crypto_budget = crypto_budget_pct

    async def optimize(self, book_returns: dict) -> UnifiedAllocation:
        usdinr = float(await self.usdinr_fn())
        weights, warnings = {}, []
        book_capital_share = {"india": 1.0, "mt5_forex": 1.0, "mt5_crypto": self.crypto_budget}
        for book, optimize in self.optimizers.items():
            w = await optimize()
            fx = 1.0 if book == "india" else usdinr
            share = book_capital_share.get(book, 1.0)
            for sym, weight in w.items():
                weights[f"{book}:{sym}"] = weight * share * (fx / usdinr if book == "india" else 1.0)
        # normalize to sum 1 in common-currency terms
        total = sum(abs(v) for v in weights.values()) or 1.0
        weights = {k: v / total for k, v in weights.items()}
        corrs = {}
        books = list(book_returns)
        for i in range(len(books)):
            for j in range(i + 1, len(books)):
                c = correlation(book_returns[books[i]], book_returns[books[j]])
                corrs[f"{books[i]}|{books[j]}"] = c
                if c > CORR_WARN:
                    warnings.append(f"{books[i]} and {books[j]} correlation {c:.2f} — hedging benefit reduced")
        return UnifiedAllocation(weights, corrs, warnings)
