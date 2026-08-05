"""MODULE 4 — Order Router (spec §Phase 1 — the single door to every broker).

Nothing else in the system may place, modify, or cancel an entry order.
Flow (hot path, <10ms internal budget):
  1. kill_switch.require_trading_allowed()      — FAIL-CLOSED, first, always
  2. anomaly_guard.entries_paused()             — FAIL-CLOSED
  3. parallel pre-checks (asyncio.gather): signal valid, VaR headroom (cache-only),
     price-band/circuit, session open, SEBI algo-id tag (india), margin (M42)
  4. classify leg: india | mt5_forex | mt5_crypto (config symbol_classes)
  5. size via MODULE 3 (0 ⇒ reject with named reason)
  6. dispatch through MODULE 41 state machine (timeout ⇒ UNKNOWN ⇒ one reconcile)
  7. audit EVERY outcome; notify exit-manager hook on fill

Wave-2 wiring note: signal/band/session checks are injected callables with the
real implementations arriving in Wave 3 (M11/M6/M43). Their INTERFACES and
fail-closed handling are final now.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)

from src.core.kill_switch import KillSwitch, TradingHaltedError
from src.core.margin_checker import MarginChecker
from src.core.order_state_machine import OrderRecord, OrderState, OrderStateMachine
from src.core.position_sizer import calculate_position_size

VAR_CACHE_KEY = "portfolio:var:95"


async def _maybe_await(result):
    if inspect.isawaitable(result):
        return await result
    return result


@dataclass
class OrderRequest:
    symbol: str
    direction: str                      # "buy" | "sell"
    entry: float
    stop: float
    atr: float
    product: str = "delivery"           # india only
    lot_size: float = 1.0
    algo_id: Optional[str] = None       # SEBI Feb-2025: mandatory tag on india orders
    p_win: Optional[float] = None
    payoff_ratio: Optional[float] = None
    expected_profit_per_unit: Optional[float] = None


@dataclass
class RouteResult:
    accepted: bool
    reason: str
    record: Optional[OrderRecord] = None
    checks: dict = field(default_factory=dict)


class OrderRouter:
    def __init__(
        self,
        *,
        config,
        kill_switch: KillSwitch,
        anomaly_guard,
        margin_checker: MarginChecker,
        connections,
        osm: Optional[OrderStateMachine] = None,
        redis=None,
        balance_fn: Callable = None,
        signal_valid_fn: Optional[Callable] = None,
        band_check_fn: Optional[Callable] = None,
        session_open_fn: Optional[Callable] = None,
        audit_fn: Optional[Callable] = None,
        on_filled: Optional[Callable] = None,
        cost_fn_factory: Optional[Callable] = None,
        portfolio_guard_fn: Optional[Callable] = None,  # (req) -> (ok, reason):
        # portfolio-LEVEL gate (heat cap / session guard) — runs after the
        # per-order pre-checks, before sizing (MODULE 46/48, Aug 2026)
    ) -> None:
        self.cfg = config
        self.kill_switch = kill_switch
        self.guard = anomaly_guard
        self.margin = margin_checker
        self.connections = connections
        self.osm = osm or OrderStateMachine()
        self.redis = redis
        self.balance_fn = balance_fn
        self.signal_valid_fn = signal_valid_fn
        self.band_check_fn = band_check_fn
        self.session_open_fn = session_open_fn
        self.audit_fn = audit_fn
        self.on_filled = on_filled
        self.cost_fn_factory = cost_fn_factory
        self.portfolio_guard_fn = portfolio_guard_fn
        mt5_classes = self.cfg.model_extra["broker"]["mt5"]["symbol_classes"]
        self._forex = set(mt5_classes.get("forex", []))
        self._crypto = set(mt5_classes.get("crypto_cfd", []))

    # ---------- classification ----------

    def classify_market(self, symbol: str) -> str:
        if symbol in self._crypto:
            return "mt5_crypto"
        if symbol in self._forex:
            return "mt5_forex"
        return "india"

    # ---------- pre-checks ----------

    async def _check_var_headroom(self) -> tuple[bool, str, float]:
        """Cache-only read (never computes VaR live). Missing/unreachable ⇒ reject."""
        if self.redis is None:
            return False, "var_cache_missing_fail_closed", 0.0
        try:
            raw = await self.redis.get(VAR_CACHE_KEY)
        except Exception:
            return False, "var_cache_unreachable_fail_closed", 0.0
        if raw is None:
            return False, "var_cache_missing_fail_closed", 0.0
        var_95 = float(raw)
        if var_95 >= self.cfg.risk_limits.max_var_daily:
            return False, "var_at_limit", var_95
        return True, "ok", var_95

    async def _run_optional(self, fn: Optional[Callable], name: str, *args) -> tuple[bool, str]:
        """Injected checks are fail-closed too: an exception counts as a failure."""
        if fn is None:
            return True, f"{name}_not_wired"
        try:
            ok = await _maybe_await(fn(*args))
        except Exception as exc:  # noqa: BLE001 — fail-closed (R4)
            return False, f"{name}_error_fail_closed:{exc}"
        return (True, "ok") if ok else (False, f"{name}_failed")

    # ---------- the single door ----------

    async def route_order(self, req: OrderRequest) -> RouteResult:
        checks: dict = {}

        # 1. kill switch — first, unconditional, fail-closed
        try:
            await self.kill_switch.require_trading_allowed()
        except TradingHaltedError:
            return await self._reject(req, "trading_halted", checks)

        # 2. anomaly pause — fail-closed
        if await self.guard.entries_paused():
            return await self._reject(req, "entries_paused_shock", checks)

        leg = self.classify_market(req.symbol)
        checks["leg"] = leg

        # SEBI algo-id tag is mandatory on the india leg (MODULE 17 hook)
        if leg == "india" and not req.algo_id:
            return await self._reject(req, "missing_sebi_algo_id", checks)

        # 3. parallel pre-checks
        (var_ok, var_reason, var_95), (sig_ok, sig_reason), (band_ok, band_reason), (
            sess_ok,
            sess_reason,
        ) = await asyncio.gather(
            self._check_var_headroom(),
            self._run_optional(self.signal_valid_fn, "signal", req.symbol, req.direction),
            self._run_optional(self.band_check_fn, "price_band", req.symbol, req.entry),
            self._run_optional(self.session_open_fn, "session", leg),
        )
        checks.update(
            {"var": var_reason, "signal": sig_reason, "band": band_reason, "session": sess_reason}
        )
        if not (var_ok and sig_ok and band_ok and sess_ok):
            failed = [r for ok, r in
                      [(var_ok, var_reason), (sig_ok, sig_reason), (band_ok, band_reason), (sess_ok, sess_reason)]
                      if not ok]
            return await self._reject(req, f"precheck_failed:{failed[0]}", checks)

        # 3b. portfolio-level guard (MODULE 46 heat cap / MODULE 48 session
        #     guard) — a NEW position must clear the book-level gates too
        if self.portfolio_guard_fn is not None:
            g_ok, g_reason = await _maybe_await(self.portfolio_guard_fn(req))
            checks["portfolio_guard"] = g_reason
            if not g_ok:
                return await self._reject(req, f"portfolio_guard:{g_reason}", checks)

        # 4-5. size (stop-distance based, VaR headroom, gap, costs)
        balance = float(await _maybe_await(self.balance_fn()))
        cost_fn = self.cost_fn_factory(req, leg) if self.cost_fn_factory else None
        size = calculate_position_size(
            entry=req.entry,
            stop=req.stop,
            atr=req.atr,
            balance=balance,
            current_var=var_95,
            risk=self.cfg.risk_limits,
            lot_size=req.lot_size,
            p_win=req.p_win,
            payoff_ratio=req.payoff_ratio,
            expected_profit_per_unit=req.expected_profit_per_unit,
            cost_fn=cost_fn,
        )
        checks["size_factors"] = size.factors
        if size.qty <= 0:
            return await self._reject(req, f"sizing:{size.reason}", checks)

        # margin (fail-closed inside M42)
        if leg == "india":
            m = await self.margin.check_india(req.symbol, size.qty, req.entry, req.product,
                                              int(req.lot_size))
        else:
            m = await self.margin.check_mt5(req.symbol, size.qty)
        checks["margin"] = m.reason
        if not m.ok:
            return await self._reject(req, f"margin:{m.reason}", checks)

        # 6. dispatch via state machine
        record = self.osm.create(req.symbol, req.direction, size.qty, leg)
        result = await self._dispatch(record, req)
        await self._audit(req, result.reason, checks, record)
        if record.state in (OrderState.FILLED, OrderState.PARTIAL) and self.on_filled:
            await _maybe_await(self.on_filled(record))
        result.checks = checks
        return result

    # ---------- executor (the ONLY broker touchpoint besides kill_switch) ----------

    async def _dispatch(self, record: OrderRecord, req: OrderRequest) -> RouteResult:
        client: httpx.AsyncClient = (
            self.connections.get_openalgo() if record.leg == "india" else self.connections.get_mt5()
        )
        if record.leg == "india":
            # Schema verified against vendor/openalgo restx_api source (R1).
            from src.core.broker_payloads import openalgo_order_payload

            broker_cfg = self.cfg.model_extra["broker"]["india"]
            path = "/api/v1/placeorder"
            payload = openalgo_order_payload(
                apikey=broker_cfg.get("api_key", ""),
                algo_id=req.algo_id or "",
                exchange=broker_cfg.get("default_exchange", "NSE"),
                symbol=record.symbol,
                action=record.direction,
                quantity=record.requested_qty,
                product="CNC" if req.product == "delivery" else "MIS",
            )
        else:
            path = "/order"
            payload = {
                "client_order_id": record.client_order_id,
                "symbol": record.symbol,
                "direction": record.direction,
                "qty": record.requested_qty,
                "algo_id": req.algo_id,
                "product": req.product,
            }
        self.osm.mark_sent(record)
        try:
            resp = await client.post(path, json=payload)
        except (httpx.TimeoutException, httpx.TransportError):
            self.osm.on_timeout(record)
            await self.osm.reconcile_unknown(record, self._broker_lookup(client))
            return RouteResult(
                accepted=record.state in (OrderState.ACKED, OrderState.PARTIAL, OrderState.FILLED),
                reason=f"dispatch_timeout_reconciled:{record.state.value}",
                record=record,
            )

        if resp.status_code >= 400:
            self.osm.on_reject(record, f"http_{resp.status_code}")
            return RouteResult(False, f"broker_rejected:http_{resp.status_code}", record)

        data = resp.json()
        # OpenAlgo returns "orderid"; our mt5_service returns "broker_order_id"
        self.osm.on_ack(record, str(data.get("orderid") or data.get("broker_order_id", "")))
        filled = float(data.get("filled_qty", 0.0))
        if filled > 0:
            self.osm.on_fill(record, filled, float(data.get("avg_price", req.entry)))
        return RouteResult(True, f"accepted:{record.state.value}", record)

    def _broker_lookup(self, client: httpx.AsyncClient):
        async def lookup(client_order_id: str):
            resp = await client.get(f"/order/{client_order_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

        return lookup

    # ---------- bookkeeping ----------

    async def _reject(self, req: OrderRequest, reason: str, checks: dict) -> RouteResult:
        await self._audit(req, f"rejected:{reason}", checks, None)
        return RouteResult(False, reason, None, checks)

    async def _audit(self, req: OrderRequest, outcome: str, checks: dict, record) -> None:
        if self.audit_fn is None:
            return
        try:
            await _maybe_await(
                self.audit_fn(
                    {
                        "symbol": req.symbol,
                        "direction": req.direction,
                        "outcome": outcome,
                        "checks": checks,
                        "client_order_id": record.client_order_id if record else None,
                        "state": record.state.value if record else None,
                        "filled_qty": record.filled_qty if record else 0,
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001 — R5: log at ERROR, never mask the order outcome.
            # Escalation to health-alert lands with MODULE 8 (audit persistence) in Wave 3.
            logger.error("audit_fn failed (order outcome unaffected): %s", exc)
