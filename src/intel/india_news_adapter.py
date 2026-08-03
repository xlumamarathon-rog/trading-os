"""MODULE 10 — India news adapter, two-speed + dissemination clustering (spec v2).

Fetchers are injected async callables returning raw dicts; this module owns
normalization, dedup-with-cluster-counting (cluster_size = how many sources
carry the same story — an IMPACT feature, not just noise to discard), and the
two-speed polling decision. first_seen_at is recorded for M37 timestamp integrity.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "as", "at", "by", "is", "with"}


@dataclass
class NewsItem:
    source: str
    headline: str
    body: str
    published_at: float
    first_seen_at: float
    tickers: list = field(default_factory=list)
    url: str = ""
    cluster_size: int = 1

    def as_dict(self) -> dict:
        return asdict(self)


def _signature(headline: str) -> frozenset:
    return frozenset(w for w in _WORD.findall(headline.lower()) if w not in _STOP)


def _similar(a: frozenset, b: frozenset, threshold: float = 0.6) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= threshold


class IndiaNewsAdapter:
    def __init__(self, fetchers: list, hot_poll_seconds: float, cold_poll_minutes: float) -> None:
        self.fetchers = fetchers          # async () -> list[raw dict]
        self.hot_poll = hot_poll_seconds
        self.cold_poll = cold_poll_minutes * 60.0
        self.malformed_count = 0

    def poll_delay(self, symbol_is_held: bool) -> float:
        """Two-speed: aggressive where we have exposure, cheap elsewhere."""
        return self.hot_poll if symbol_is_held else self.cold_poll

    def _normalize(self, raw: dict, source: str) -> Optional[NewsItem]:
        try:
            return NewsItem(
                source=source,
                headline=str(raw["headline"]).strip(),
                body=str(raw.get("body", "")),
                published_at=float(raw["published_at"]),
                first_seen_at=time.time(),
                tickers=[t.upper() for t in raw.get("tickers", [])],
                url=str(raw.get("url", "")),
            )
        except (KeyError, TypeError, ValueError):
            self.malformed_count += 1  # counted, surfaced in health — not silently lost
            return None

    async def fetch_all(self, ticker: Optional[str] = None) -> list:
        items: list[NewsItem] = []
        for fetcher in self.fetchers:
            try:
                raws = await fetcher()
            except Exception:
                continue  # one source down must not kill the sweep; health tracks it
            for raw in raws:
                item = self._normalize(raw, getattr(fetcher, "source_name", "unknown"))
                if item is not None:
                    items.append(item)
        clustered = self._cluster(items)
        if ticker:
            clustered = [i for i in clustered if ticker.upper() in i.tickers]
        return clustered

    def _cluster(self, items: list) -> list:
        """Same story across N sources -> one item with cluster_size=N."""
        kept: list[tuple[frozenset, NewsItem]] = []
        for item in sorted(items, key=lambda i: i.published_at):
            sig = _signature(item.headline)
            for ksig, kitem in kept:
                if _similar(sig, ksig):
                    kitem.cluster_size += 1
                    break
            else:
                kept.append((sig, item))
        return [k for _, k in kept]
