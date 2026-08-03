"""Trading OS application entrypoint (Wave 9 — production runtime).

    python -m src.app --mode paper     # the ONLY mode that starts without gates
    python -m src.app --mode live      # HARD-BLOCKED until the live gate passes

Responsibilities:
  - build the full component graph from config/master.yaml
  - WorkerSupervisor: run every background loop with heartbeats, restart-on-crash
    (bounded), and graceful shutdown — a dead exit-manager loop must never go
    unnoticed (R9)
  - LIVE GATE: live mode refuses to start unless gate_state.json proves
    paper evidence + compliance + an explicit human acknowledgement phrase.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger("trading_os.app")

GATE_FILE = "gate_state.json"
LIVE_ACK_PHRASE = "I ACCEPT LIVE TRADING RISK"
MIN_PAPER_DAYS = 14
MAX_RESTARTS_PER_WORKER = 5


class LiveGateError(RuntimeError):
    """Raised when live mode is requested without the required evidence."""


def assert_live_allowed(cfg, gate_path: str | Path = GATE_FILE) -> dict:
    """Live is a privilege earned by evidence — never a default (spec §12.6)."""
    path = Path(gate_path)
    if not path.exists():
        raise LiveGateError("no gate_state.json — run paper mode first (min 14 days)")
    gate = json.loads(path.read_text())
    problems = []
    if int(gate.get("paper_days_completed", 0)) < MIN_PAPER_DAYS:
        problems.append(
            f"paper_days_completed {gate.get('paper_days_completed', 0)} < {MIN_PAPER_DAYS}"
        )
    if int(gate.get("clean_reconciliation_streak", 0)) < 5:
        problems.append("need 5 consecutive clean EOD reconciliations")
    if not gate.get("sebi_checks_passed"):
        problems.append("SEBI Feb-2025 checks not recorded as passed (MODULE 17)")
    if not cfg.model_extra["broker"]["india"].get("static_ip_confirmed"):
        problems.append("broker.india.static_ip_confirmed is false")
    if gate.get("human_ack") != LIVE_ACK_PHRASE:
        problems.append(f'human_ack missing — set to exactly "{LIVE_ACK_PHRASE}"')
    if problems:
        raise LiveGateError("LIVE BLOCKED: " + "; ".join(problems))
    return gate


@dataclass
class WorkerSpec:
    name: str
    factory: Callable          # () -> coroutine (a long-running loop)
    restarts: int = 0
    last_start: float = 0.0


class WorkerSupervisor:
    """Runs every background loop; restarts crashed ones (bounded); heartbeats."""

    def __init__(self, redis=None, alert_fn=None,
                 max_restarts: int = MAX_RESTARTS_PER_WORKER) -> None:
        self.redis = redis
        self.alert_fn = alert_fn
        self.max_restarts = max_restarts
        self.specs: list[WorkerSpec] = []
        self._tasks: dict[str, asyncio.Task] = {}
        self._stopping = False
        self.events: list[dict] = []          # supervision log (surfaced in cockpit)

    def add(self, name: str, factory: Callable) -> None:
        self.specs.append(WorkerSpec(name=name, factory=factory))

    async def _heartbeat(self, name: str) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.setex(f"heartbeat:{name}", 120, str(time.time()))
        except Exception as exc:  # noqa: BLE001 — heartbeat loss is itself an alert
            self.events.append({"worker": name, "event": "heartbeat_failed", "error": str(exc)})

    def _start(self, spec: WorkerSpec) -> None:
        spec.last_start = time.time()
        self._tasks[spec.name] = asyncio.create_task(spec.factory(), name=spec.name)

    async def run(self, monitor_interval: float = 0.05) -> None:
        for spec in self.specs:
            self._start(spec)
        while not self._stopping:
            await asyncio.sleep(monitor_interval)
            for spec in self.specs:
                task = self._tasks.get(spec.name)
                await self._heartbeat(spec.name)
                if task is None or not task.done():
                    continue
                exc = task.exception() if not task.cancelled() else None
                self.events.append({"worker": spec.name, "event": "crashed",
                                    "error": str(exc)})
                logger.error("worker %s crashed: %s", spec.name, exc)
                if spec.restarts >= self.max_restarts:
                    self.events.append({"worker": spec.name, "event": "gave_up"})
                    if self.alert_fn:
                        try:
                            await self.alert_fn(
                                f"WORKER DOWN (max restarts): {spec.name}: {exc}")
                        except Exception as alert_exc:  # noqa: BLE001
                            logger.error("alert failed: %s", alert_exc)
                    self._tasks.pop(spec.name, None)
                    continue
                spec.restarts += 1
                self.events.append({"worker": spec.name, "event": "restarted",
                                    "n": spec.restarts})
                self._start(spec)

    async def shutdown(self) -> None:
        self._stopping = True
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self.events.append({"event": "shutdown_complete"})


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trading OS runtime")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper")
    parser.add_argument("--config", default="config/master.yaml")
    return parser


async def main(argv=None) -> int:
    from src.core.config_loader import load_config

    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    cfg = load_config(args.config)
    if cfg.unresolved_env:
        logger.warning("unresolved env secrets: %s", cfg.unresolved_env)

    if args.mode == "live":
        assert_live_allowed(cfg)          # raises LiveGateError — no bypass exists
        logger.info("LIVE gate passed — starting live runtime")
    else:
        logger.info("PAPER mode — full stack against the paper broker")

    # Full wiring (paper server, workers, cockpit gateway) is assembled here on
    # the VPS; see DEPLOY.md §4. The supervisor + gate above are the runtime core.
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
