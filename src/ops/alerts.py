"""Alerting (Wave 9): Telegram adapter + fan-out. Alert failure NEVER breaks
the trading path — recorded and surfaced via health instead (R5)."""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class TelegramAlerter:
    def __init__(self, bot_token: str, chat_id: str, transport=None) -> None:
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = chat_id
        self._transport = transport
        self.failures: list = []

    async def send(self, text: str) -> bool:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=10) as client:
                resp = await client.post(self.url, json={"chat_id": self.chat_id,
                                                         "text": text[:4000]})
                resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001 — alerts must not take down trading
            self.failures.append(str(exc))
            logger.error("telegram alert failed: %s", exc)
            return False


class AlertFanout:
    """One call, every channel; individual failure tolerated + counted."""

    def __init__(self, channels: list) -> None:
        self.channels = channels

    async def send(self, text: str) -> int:
        delivered = 0
        for ch in self.channels:
            if await ch.send(text):
                delivered += 1
        return delivered
