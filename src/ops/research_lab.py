"""MODULE 63 — Research Lab (Aug 2026).

Backtests from the cockpit, on the CERTIFIED harness: every run is a real
`scripts/research_replay.py` subprocess — full stack, real cost schedules,
reconciliation-CLEAN or it doesn't count. Real numbers only (standing rule
§5.2): this module launches and catalogs runs; it computes nothing itself.

Safety rails:
  - strategy and dataset are ALLOWLISTED (the signals registry + the repo's
    real data dirs) — no arbitrary argv ever reaches the shell
  - one run at a time (a replay saturates a core; the cockpit is not a grid)
  - results live under data/research_runs/<id>/ — never near gate state
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Optional

DATASETS = {
    "india_6m": "data/market_india_6m",
    "forex_6m": "data/market_forex_6m",
    "crypto_6m": "data/market_crypto_6m",
    "covid_2020": "data/real_covid",
    "gfc_2008": "data/scenario_gfc_2008",
    "flash_crash_2012": "data/scenario_flash_crash_2012",
}

_ID_RE = re.compile(r"[^a-z0-9_\-]")


class ResearchLab:
    def __init__(self, repo_root: str | Path = ".",
                 out_root: str | Path = "data/research_runs",
                 strategies: Optional[list] = None) -> None:
        self.root = Path(repo_root)
        self.out_root = Path(out_root)
        self.out_root.mkdir(parents=True, exist_ok=True)
        if strategies is None:
            from src.strategies.signals import SIGNALS
            strategies = sorted(SIGNALS)
        self.strategies = list(strategies)
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._current: Optional[str] = None

    # ---------------- run lifecycle ----------------

    def _run_dir(self, run_id: str) -> Path:
        return self.out_root / run_id

    async def start(self, strategy: str, dataset: str, actor: str = "") -> dict:
        if strategy not in self.strategies:
            raise ValueError(f"unknown strategy {strategy!r} — "
                             f"one of {self.strategies}")
        if dataset not in DATASETS:
            raise ValueError(f"unknown dataset {dataset!r} — "
                             f"one of {sorted(DATASETS)}")
        if self._proc is not None and self._proc.returncode is None:
            raise RuntimeError(f"a run is already in progress: {self._current}")

        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_id = _ID_RE.sub("", f"{strategy}_{dataset}_{stamp}".lower())
        out_dir = self._run_dir(run_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        meta = {"id": run_id, "strategy": strategy, "dataset": dataset,
                "data_dir": DATASETS[dataset], "status": "running",
                "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "actor_token_tail": actor}
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=1))

        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, str(self.root / "scripts/research_replay.py"),
            strategy, str(self.root / DATASETS[dataset]), str(out_dir),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            cwd=str(self.root))
        self._current = run_id
        asyncio.get_event_loop().create_task(self._reap(run_id))
        return meta

    async def _reap(self, run_id: str) -> None:
        proc = self._proc
        _, err = await proc.communicate()
        out_dir = self._run_dir(run_id)
        meta = json.loads((out_dir / "meta.json").read_text())
        results = out_dir / "results.json"
        if proc.returncode == 0 and results.exists():
            meta["status"] = "done"
        else:
            meta["status"] = "failed"
            meta["error"] = (err or b"")[-800:].decode(errors="replace")
        meta["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=1))

    # ---------------- read API ----------------

    _RESULT_KEYS = ("return_pct", "MAX_DRAWDOWN_pct", "sharpe_annualized",
                    "sortino_annualized", "calmar_ratio", "win_rate_pct",
                    "closed_trades", "entries", "total_costs",
                    "reconciliation", "audit_chain_ok",
                    "buy_hold_equal_weight_return_pct", "window")

    def runs(self) -> list:
        rows = []
        for d in sorted(self.out_root.iterdir(), reverse=True):
            meta_p = d / "meta.json"
            if not meta_p.exists():
                continue
            try:
                meta = json.loads(meta_p.read_text())
            except json.JSONDecodeError:
                continue
            results_p = d / "results.json"
            if meta.get("status") == "done" and results_p.exists():
                try:
                    res = json.loads(results_p.read_text())
                    meta["results"] = {k: res.get(k) for k in self._RESULT_KEYS}
                except json.JSONDecodeError:
                    meta["status"] = "failed"
            rows.append(meta)
        return rows[:50]

    def options(self) -> dict:
        return {"strategies": self.strategies, "datasets": sorted(DATASETS),
                "busy": bool(self._proc is not None
                             and self._proc.returncode is None)}
