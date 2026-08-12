"""MODULE 60 — Broker Settings provider (Aug 2026).

Backs the cockpit Settings→Brokers page. Three jobs:

  status()  broker cards for the UI: active provider, endpoints, and which
            credential ENV VARS are set — booleans only, never values.
  test()    reachability ping of the configured OpenAlgo base_url / MT5 exec
            service health endpoint. Sends NO credentials.
  save()    persist NON-SECRET settings (provider, base_url, exec URL) to a
            local overlay file — never master.yaml, never credentials.

Security invariants (spec §12):
  - Credentials live in env vars only. This module never accepts, stores,
    logs, or returns a credential value. The UI gets set/unset booleans.
  - `static_ip_confirmed` and every gate key are OUTSIDE the allowlist: the
    save path structurally cannot touch the live gate. (The gateway refuses
    those keys too — two independent layers.)
  - The overlay file (config/brokers_local.yaml) is gitignored deployment
    state, applied on top of master.yaml at assembly time.

India multi-broker model: the system talks to OpenAlgo (self-hosted broker
hub) and OpenAlgo talks to the chosen provider — dhan | shoonya | fyers |
zerodha — so "switching brokers" is a provider+key change, not a code change.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import httpx
import yaml

# The ONLY keys the save path will persist, per broker. Everything else —
# credentials, gate flags, unknown keys — is refused loudly.
SAVE_ALLOWLIST: dict[str, set] = {
    "india": {"provider", "base_url", "default_exchange", "max_orders_per_sec"},
    "mt5": {"exec_service_url", "server_note"},
}
# angel = the 2026 data-feed verdict: free real-time NSE websocket +
# documented headless re-login (see DEPLOY.md daily runbook + ledger)
INDIA_PROVIDERS = ("dhan", "shoonya", "fyers", "zerodha", "angel", "upstox")

# env vars the runtime resolves for each leg (report set/unset only)
ENV_VARS = {
    "india": ("INDIA_BROKER_API_KEY", "INDIA_BROKER_SECRET"),
    "mt5": ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "MT5_SERVICE_TOKEN"),
}


class BrokerSettings:
    def __init__(self, cfg, overlay_path: str | Path = "config/brokers_local.yaml",
                 http_timeout: float = 4.0) -> None:
        self._cfg = cfg
        self.overlay_path = Path(overlay_path)
        self.http_timeout = http_timeout

    # ---------------- helpers ----------------

    def _broker_cfg(self) -> dict:
        base = dict((self._cfg.model_extra or {}).get("broker") or {})
        overlay = self._load_overlay()
        merged = {}
        for leg in ("india", "mt5"):
            merged[leg] = dict(base.get(leg) or {})
            merged[leg].update(overlay.get(leg) or {})
        return merged

    def _load_overlay(self) -> dict:
        if not self.overlay_path.exists():
            return {}
        try:
            return yaml.safe_load(self.overlay_path.read_text()) or {}
        except yaml.YAMLError:
            return {}

    @staticmethod
    def _env_status(leg: str) -> dict:
        return {name: bool(os.environ.get(name)) for name in ENV_VARS.get(leg, ())}

    # ---------------- provider fns (wired into the gateway) ----------------

    async def status(self) -> dict:
        b = self._broker_cfg()
        india, mt5 = b.get("india", {}), b.get("mt5", {})
        return {
            "india": {
                "hub": "openalgo",
                "provider": india.get("provider", ""),
                "providers_available": list(INDIA_PROVIDERS),
                "base_url": india.get("base_url", ""),
                "default_exchange": india.get("default_exchange", ""),
                "env": self._env_status("india"),
                # read-only surface of the deployment gate flag — display only
                "static_ip_confirmed": bool(india.get("static_ip_confirmed")),
            },
            "mt5": {
                "exec_service_url": mt5.get("exec_service_url", ""),
                "symbol_classes": mt5.get("symbol_classes", {}),
                "env": self._env_status("mt5"),
            },
            "overlay_file": str(self.overlay_path),
        }

    async def test(self, broker: str) -> dict:
        """Reachability probe. No credentials leave this process."""
        b = self._broker_cfg()
        if broker == "india":
            url = (b.get("india") or {}).get("base_url", "")
        elif broker == "mt5":
            url = (b.get("mt5") or {}).get("exec_service_url", "")
        else:
            return {"ok": False, "detail": f"unknown broker {broker!r}"}
        if not url:
            return {"ok": False, "detail": "no endpoint configured"}
        try:
            async with httpx.AsyncClient(timeout=self.http_timeout,
                                         verify=False) as client:
                resp = await client.get(url.rstrip("/") + "/health")
            return {"ok": resp.status_code < 500,
                    "detail": f"HTTP {resp.status_code} from {url}"}
        except Exception as exc:  # noqa: BLE001 — report, never crash the gateway
            return {"ok": False, "detail": f"unreachable: {type(exc).__name__}: {exc}"}

    async def save(self, broker: str, settings: dict, actor: str) -> dict:
        allow = SAVE_ALLOWLIST.get(broker)
        if allow is None:
            raise ValueError(f"unknown broker {broker!r}")
        rejected = sorted(set(map(str, settings)) - allow)
        if rejected:
            raise PermissionError(
                f"keys outside the {broker} allowlist refused: {rejected}")
        clean = {k: settings[k] for k in settings}
        if broker == "india" and "provider" in clean:
            if clean["provider"] not in INDIA_PROVIDERS:
                raise ValueError(
                    f"provider must be one of {INDIA_PROVIDERS}, "
                    f"got {clean['provider']!r}")
        if "max_orders_per_sec" in clean:
            clean["max_orders_per_sec"] = int(clean["max_orders_per_sec"])

        overlay = self._load_overlay()
        overlay.setdefault(broker, {}).update(clean)
        overlay["_meta"] = {"updated_by_token_tail": actor}
        self.overlay_path.parent.mkdir(parents=True, exist_ok=True)
        self.overlay_path.write_text(yaml.safe_dump(overlay, sort_keys=True))
        return {"saved": clean, "overlay_file": str(self.overlay_path)}
