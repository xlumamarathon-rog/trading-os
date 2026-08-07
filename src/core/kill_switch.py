"""MODULE 1 — Kill Switch (spec §Phase 1, safety rule §12.1).

Emergency stop for the entire system. Semantics (v2):
- FAIL-CLOSED: if Redis is unreachable, the system is considered HALTED.
- Dual flag: Redis key + local sentinel file (dead-man fallback). Halted if EITHER is set.
- kill_all cancels every open order and closes every open position on every leg,
  continuing past individual failures and recording them.
- Unlock requires the exact configured phrase AND reachable Redis (fail-closed).
"""
from __future__ import annotations

import inspect
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

HALT_KEY = "TRADING_HALTED"


class TradingHaltedError(RuntimeError):
    """Raised by require_trading_allowed() when the system is halted."""


async def _maybe_await(result: Any) -> Any:
    if inspect.isawaitable(result):
        return await result
    return result


@dataclass
class KillReport:
    reason: str
    triggered_at: float
    orders_cancelled: list = field(default_factory=list)
    positions_closed: list = field(default_factory=list)
    failures: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "reason": self.reason,
            "triggered_at": self.triggered_at,
            "orders_cancelled": self.orders_cancelled,
            "positions_closed": self.positions_closed,
            "failures": self.failures,
        }


class KillSwitch:
    def __init__(
        self,
        redis,
        brokers: dict[str, Any],
        sentinel_path: str | Path,
        unlock_phrase: str,
        auto_trigger_daily_loss_pct: float,
        auto_trigger_var_breach: bool,
        max_var_daily: float,
        alert_fn: Optional[Callable] = None,
        audit_fn: Optional[Callable] = None,
    ) -> None:
        self.redis = redis
        self.brokers = brokers  # e.g. {"india": openalgo_adapter, "mt5": mt5_adapter}
        self.sentinel_path = Path(sentinel_path)
        self._unlock_phrase = unlock_phrase
        self._auto_daily_loss = auto_trigger_daily_loss_pct
        self._auto_var = auto_trigger_var_breach
        self._max_var_daily = max_var_daily
        self._alert_fn = alert_fn
        self._audit_fn = audit_fn

    # ---------- state ----------

    async def is_halted(self) -> bool:
        """FAIL-CLOSED: Redis error ⇒ halted. Sentinel file ⇒ halted."""
        try:
            flag = await self.redis.get(HALT_KEY)
        except Exception:
            return True  # fail-closed (config only permits "halt")
        if flag in ("1", b"1", 1, True, "true"):
            return True
        return self.sentinel_path.exists()

    async def require_trading_allowed(self) -> None:
        if await self.is_halted():
            raise TradingHaltedError("trading halted — kill switch active or state unknown")

    # ---------- the red button ----------

    async def kill_all(self, reason: str) -> KillReport:
        report = KillReport(reason=reason, triggered_at=time.time())

        # 1. Raise flags FIRST (both stores; best-effort each; sentinel is local so it always works)
        try:
            await self.redis.set(HALT_KEY, "1")
        except Exception as exc:  # noqa: BLE001 — recorded, halt still holds via sentinel
            report.failures.append(f"redis_set_failed:{exc}")
        self.sentinel_path.parent.mkdir(parents=True, exist_ok=True)
        self.sentinel_path.write_text(
            json.dumps({"reason": reason, "at": report.triggered_at})
        )

        # 2. Cancel all open orders, close all positions — per leg, continue past failures
        for leg_name, broker in self.brokers.items():
            if broker is None:
                continue
            try:
                orders = await broker.get_open_orders()
            except Exception as exc:  # noqa: BLE001
                report.failures.append(f"{leg_name}:fetch_orders_failed:{exc}")
                orders = []
            for order in orders:
                oid = order["id"]
                try:
                    await broker.cancel_order(oid)
                    report.orders_cancelled.append(f"{leg_name}:{oid}")
                except Exception as exc:  # noqa: BLE001
                    report.failures.append(f"{leg_name}:cancel_failed:{oid}:{exc}")

            try:
                positions = await broker.get_open_positions()
            except Exception as exc:  # noqa: BLE001
                report.failures.append(f"{leg_name}:fetch_positions_failed:{exc}")
                positions = []
            for pos in positions:
                pid = pos["id"]
                try:
                    await broker.close_position_market(pid)
                    report.positions_closed.append(f"{leg_name}:{pid}")
                except Exception as exc:  # noqa: BLE001
                    report.failures.append(f"{leg_name}:close_failed:{pid}:{exc}")

        # 3. Audit + alert (never let notification failure mask the kill)
        if self._audit_fn is not None:
            try:
                await _maybe_await(self._audit_fn(report.to_dict()))
            except Exception as exc:  # noqa: BLE001
                report.failures.append(f"audit_failed:{exc}")
        if self._alert_fn is not None:
            try:
                await _maybe_await(self._alert_fn(report.to_dict()))
            except Exception as exc:  # noqa: BLE001
                report.failures.append(f"alert_failed:{exc}")

        return report

    # ---------- manual unlock ----------

    async def unlock(self, phrase: str) -> None:
        # The unlock phrase is a SECOND secret beyond the operator token — it is
        # the only thing standing between a compromised operator credential and
        # resuming trading after a safety halt. Compare in constant time so the
        # comparison itself can't leak the phrase byte-by-byte (compare_digest);
        # an unset phrase always refuses (fail-closed). Semantics unchanged.
        if not self._unlock_phrase or not secrets.compare_digest(
                str(phrase).encode(), str(self._unlock_phrase).encode()):
            raise PermissionError("unlock refused: wrong confirmation phrase")
        # Fail-closed: refuse to unlock while Redis is unreachable — otherwise the
        # Redis flag would spring back the moment Redis returns, in an ambiguous state.
        try:
            await self.redis.delete(HALT_KEY)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"unlock refused: redis unreachable (fail-closed): {exc}") from exc
        if self.sentinel_path.exists():
            self.sentinel_path.unlink()

    # ---------- auto triggers ----------

    async def check_auto_triggers(
        self,
        daily_pnl_pct: Optional[float] = None,
        var_95: Optional[float] = None,
    ) -> Optional[KillReport]:
        if daily_pnl_pct is not None and daily_pnl_pct <= -self._auto_daily_loss:
            return await self.kill_all(
                f"auto: daily loss {daily_pnl_pct:.4f} breached -{self._auto_daily_loss:.4f}"
            )
        if self._auto_var and var_95 is not None and var_95 > self._max_var_daily:
            return await self.kill_all(
                f"auto: VaR95 {var_95:.4f} breached limit {self._max_var_daily:.4f}"
            )
        return None
