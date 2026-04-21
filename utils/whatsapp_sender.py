"""WhatsApp send abstraction.

Phase 1: ManualWaLinkSender — builds a wa.me URL; the operator clicks it.
Phase 2: AutomationSender  — POSTs to an external webhook (n8n / automation tool).

To swap: set COMMUNICATIONS_SEND_MODE="automation" in .env and implement
AutomationSender.send(). No schema or UI changes required.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from config import config
from logging_config import get_logger

logger = get_logger("whatsapp_sender")


@dataclass
class SendResult:
    success: bool
    wa_url: str | None = None   # populated for manual mode
    error: str | None = None


@runtime_checkable
class WhatsAppSender(Protocol):
    async def send(self, phone: str, message_body: str) -> SendResult:
        ...


class ManualWaLinkSender:
    """Builds a wa.me deep-link; does not actually send anything automatically."""

    async def send(self, phone: str, message_body: str) -> SendResult:
        # Strip non-digit chars, remove leading zeros, ensure country-code prefix exists
        digits = "".join(c for c in phone if c.isdigit())
        if not digits:
            return SendResult(success=False, error="Invalid phone number")
        encoded = urllib.parse.quote(message_body, safe="")
        wa_url = f"https://wa.me/{digits}?text={encoded}"
        return SendResult(success=True, wa_url=wa_url)


class AutomationSender:
    """Sends via external webhook (n8n or similar). Swap in for Phase 2."""

    def __init__(self, webhook_url: str):
        self._webhook_url = webhook_url

    async def send(self, phone: str, message_body: str) -> SendResult:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    self._webhook_url,
                    json={"phone": phone, "message": message_body},
                )
                resp.raise_for_status()
            return SendResult(success=True)
        except Exception as exc:
            logger.error(f"AutomationSender failed: {exc}", extra={"data": {"phone": phone}})
            return SendResult(success=False, error=str(exc))


def get_sender() -> WhatsAppSender:
    mode = config.COMMUNICATIONS_SEND_MODE
    if mode == "automation":
        webhook_url = config.COMMUNICATIONS_AUTOMATION_WEBHOOK_URL
        if not webhook_url:
            logger.warning("COMMUNICATIONS_SEND_MODE=automation but no webhook URL configured; falling back to manual")
            return ManualWaLinkSender()
        return AutomationSender(webhook_url)
    return ManualWaLinkSender()
