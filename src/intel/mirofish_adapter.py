"""MiroFish integration adapter — wired to the VERIFIED backend routes (R1).

Verified against vendor/MiroFish/backend/app/api (Flask, 2026-08):
  POST /simulation/create     POST /simulation/prepare
  POST /report/generate       POST /report/generate/status     GET /report/<id>

reconstruct_crowd_reaction(news) drives a full simulate->report cycle and maps
the report into our crowd_emotion schema. MiroFish runs as its own service
(docker-compose in the vendor repo); this adapter only speaks HTTP to it.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import httpx


class MiroFishAdapter:
    def __init__(self, base_url: str, client: Optional[httpx.AsyncClient] = None,
                 poll_seconds: float = 5.0, timeout_seconds: float = 600.0) -> None:
        self.client = client or httpx.AsyncClient(base_url=base_url, timeout=30)
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds

    async def create_simulation(self, topic: str, context: str) -> str:
        resp = await self.client.post("/simulation/create",
                                      json={"topic": topic, "context": context})
        resp.raise_for_status()
        return str(resp.json()["simulation_id"])

    async def prepare(self, simulation_id: str) -> None:
        resp = await self.client.post("/simulation/prepare",
                                      json={"simulation_id": simulation_id})
        resp.raise_for_status()

    async def generate_report(self, simulation_id: str) -> str:
        resp = await self.client.post("/report/generate",
                                      json={"simulation_id": simulation_id})
        resp.raise_for_status()
        return str(resp.json()["report_id"])

    async def wait_for_report(self, report_id: str) -> dict:
        waited = 0.0
        while waited < self.timeout_seconds:
            status = await self.client.post("/report/generate/status",
                                            json={"report_id": report_id})
            status.raise_for_status()
            body = status.json()
            if body.get("status") == "completed":
                report = await self.client.get(f"/report/{report_id}")
                report.raise_for_status()
                return report.json()
            if body.get("status") == "failed":
                raise RuntimeError(f"mirofish report failed: {body}")
            await asyncio.sleep(self.poll_seconds)
            waited += self.poll_seconds
        raise TimeoutError(f"mirofish report {report_id} not done in {self.timeout_seconds}s")

    async def reconstruct_crowd_reaction(self, news_headline: str, context: str = "") -> dict:
        """Full cycle -> our crowd_emotion schema (case_memory / failure classifier)."""
        sim = await self.create_simulation(topic=news_headline, context=context)
        await self.prepare(sim)
        report_id = await self.generate_report(sim)
        report = await self.wait_for_report(report_id)
        return {
            "sentiment": report.get("sentiment", 0.0),
            "panic_level": report.get("panic_level", 0.0),
            "mechanical_flag": bool(report.get("mechanical_flag", False)),
            "summary": report.get("summary", ""),
            "raw_report_id": report_id,
        }
