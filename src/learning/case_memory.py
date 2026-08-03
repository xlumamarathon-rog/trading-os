"""MODULE 20 — Case memory with similarity retrieval (spec Addendum A).

Embedding fn is injected (LLM/embedding-model outside). In-memory cosine store
here; pgvector backend implements the same interface on the VPS. v2: every case
carries exit-quality fields and the GEX regime at case time.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

REQUIRED = ("setup", "news", "causal_chain", "crowd_emotion", "your_action",
            "outcome", "lesson")


def cosine(a: list, b: list) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0


class CaseMemory:
    def __init__(self, embed_fn) -> None:
        self.embed_fn = embed_fn          # async text -> list[float]
        self.cases: list[dict] = []

    async def store_case(self, case: dict) -> dict:
        missing = [k for k in REQUIRED if k not in case]
        if missing:
            raise ValueError(f"case missing fields: {missing}")
        text = f"{case['setup']} {case['news'].get('headline', '')}"
        case = dict(case)
        case["embedding"] = await self.embed_fn(text)
        self.cases.append(case)
        return case

    async def query_similar(self, current_setup: str, top_k: int = 5) -> list:
        if not self.cases:
            return []
        q = await self.embed_fn(current_setup)
        scored = sorted(self.cases, key=lambda c: cosine(q, c["embedding"]), reverse=True)
        return scored[:top_k]
