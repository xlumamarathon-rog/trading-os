"""MODULE 2 — Connection Manager (spec §Phase 1).

Warm, persistent clients for both execution legs so orders never pay
connection-setup cost. Singletons created at app startup, reused everywhere.
Latency self-test at startup logs per-leg round-trip (acceptance criterion).

NOTE (lint L1): this file and order_router.py are the ONLY places allowed to
hold broker transport clients.
"""
from __future__ import annotations

import logging
from time import perf_counter
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(
        self,
        openalgo_base_url: str,
        mt5_service_url: str,
        timeout_seconds: float = 5.0,
        max_keepalive: int = 10,
        openalgo_transport: Optional[httpx.AsyncBaseTransport] = None,
        mt5_transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._openalgo_base_url = openalgo_base_url
        self._mt5_service_url = mt5_service_url
        self._timeout = timeout_seconds
        self._max_keepalive = max_keepalive
        self._openalgo_transport = openalgo_transport
        self._mt5_transport = mt5_transport
        self.openalgo: Optional[httpx.AsyncClient] = None
        self.mt5: Optional[httpx.AsyncClient] = None
        self.latency_ms: dict[str, Optional[float]] = {}
        self._started = False

    async def startup(self) -> None:
        limits = httpx.Limits(max_keepalive_connections=self._max_keepalive)
        self.openalgo = httpx.AsyncClient(
            base_url=self._openalgo_base_url,
            timeout=self._timeout,
            limits=limits,
            transport=self._openalgo_transport,
        )
        self.mt5 = httpx.AsyncClient(
            base_url=self._mt5_service_url,
            timeout=self._timeout,
            limits=limits,
            transport=self._mt5_transport,
        )
        for name, client in (("openalgo", self.openalgo), ("mt5", self.mt5)):
            t0 = perf_counter()
            try:
                resp = await client.get("/health")
                resp.raise_for_status()
                self.latency_ms[name] = (perf_counter() - t0) * 1000.0
            except Exception as exc:  # noqa: BLE001 — recorded; health endpoint reports it
                self.latency_ms[name] = None
                logger.error("startup probe failed for %s: %s", name, exc)
        self._started = True
        logger.info("connection manager warm — latencies(ms): %s", self.latency_ms)

    def get_openalgo(self) -> httpx.AsyncClient:
        if not self._started or self.openalgo is None:
            raise RuntimeError("ConnectionManager not started")
        return self.openalgo

    def get_mt5(self) -> httpx.AsyncClient:
        if not self._started or self.mt5 is None:
            raise RuntimeError("ConnectionManager not started")
        return self.mt5

    async def shutdown(self) -> None:
        if self.openalgo is not None:
            await self.openalgo.aclose()
        if self.mt5 is not None:
            await self.mt5.aclose()
        self._started = False
