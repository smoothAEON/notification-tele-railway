"""Telegram notification delivery."""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass

import requests

from alert_mcp.models import Alert
from alert_mcp.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    error: str | None = None


class TelegramNotifier:
    """Send fired alert messages to the configured Telegram chat."""

    def __init__(self, settings: Settings) -> None:
        self._token = settings.telegram_bot_token.get_secret_value().strip()
        self._chat_id = settings.telegram_chat_id.strip()
        self._url = f"https://api.telegram.org/bot{self._token}/sendMessage"

    @property
    def ready(self) -> bool:
        return bool(self._token and self._chat_id)

    async def send_price_alert(self, alert: Alert, *, trigger_price: float) -> DeliveryResult:
        return await asyncio.to_thread(self._send_price_alert_sync, alert, trigger_price)

    def _send_price_alert_sync(self, alert: Alert, trigger_price: float) -> DeliveryResult:
        if not self.ready:
            logger.warning("telegram_not_ready alert_id=%s instrument=%s", alert.id, alert.instrument)
            return DeliveryResult(ok=False, error="Telegram bot token or chat id is missing.")

        direction = "above" if alert.direction == "above" else "below"
        escaped_note = html.escape(alert.note or "-")
        message = (
            "<b>PRICE ALERT TRIGGERED</b>\n"
            f"Instrument: <b>{html.escape(alert.instrument)}</b>\n"
            f"Direction: <code>{direction}</code>\n"
            f"Target: <code>{alert.target_price:.10g}</code>\n"
            f"Current: <code>{trigger_price:.10g}</code>\n"
            f"Note: {escaped_note}"
        )
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(self._url, json=payload, timeout=10)
        except requests.RequestException as exc:
            logger.warning("telegram_send_failed", exc_info=True)
            return DeliveryResult(ok=False, error=str(exc))

        if response.ok:
            logger.info("telegram_alert_sent alert_id=%s instrument=%s", alert.id, alert.instrument)
            return DeliveryResult(ok=True)

        error = f"Telegram HTTP {response.status_code}: {response.text[:500]}"
        logger.warning("telegram_send_rejected: %s", error)
        return DeliveryResult(ok=False, error=error)
