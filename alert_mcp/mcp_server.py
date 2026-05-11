"""FastMCP tool surface."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from alert_mcp.models import AlertDirection
from alert_mcp.service import AlertService
from alert_mcp.settings import Settings

TOOL_SPECS: tuple[dict[str, str], ...] = (
    {"name": "get_service_status", "description": "Return runtime, stream, price-cache, alert-count, and Telegram status."},
    {"name": "get_current_prices", "description": "Return current OANDA bid/ask/mid prices for requested or watched instruments."},
    {"name": "create_price_alert", "description": "Create a one-shot OANDA price alert."},
    {"name": "list_current_alerts", "description": "List pending and firing price alerts."},
    {"name": "list_fired_alerts", "description": "List fired price alerts ordered newest first."},
    {"name": "cancel_alert", "description": "Cancel one current alert by id."},
    {"name": "cancel_current_alerts", "description": "Cancel all current alerts, optionally filtered by instrument."},
)


class AlertMcpTools:
    """Async tool adapter around AlertService."""

    def __init__(self, service: AlertService) -> None:
        self.service = service

    async def get_service_status(self) -> dict[str, Any]:
        return await self.service.get_service_status()

    async def get_current_prices(self, instruments: list[str] | None = None) -> dict[str, Any]:
        return await self.service.get_current_prices(instruments)

    async def create_price_alert(
        self,
        instrument: str,
        target_price: float,
        direction: AlertDirection,
        note: str | None = None,
    ) -> dict[str, Any]:
        return await self.service.create_price_alert(
            instrument=instrument,
            target_price=target_price,
            direction=direction,
            note=note,
        )

    async def list_current_alerts(self, instrument: str | None = None) -> dict[str, Any]:
        return await self.service.list_current_alerts(instrument=instrument)

    async def list_fired_alerts(self, instrument: str | None = None, limit: int = 50) -> dict[str, Any]:
        return await self.service.list_fired_alerts(instrument=instrument, limit=limit)

    async def cancel_alert(self, alert_id: int) -> dict[str, Any]:
        return await self.service.cancel_alert(alert_id=alert_id)

    async def cancel_current_alerts(self, instrument: str | None = None) -> dict[str, Any]:
        return await self.service.cancel_current_alerts(instrument=instrument)


def build_mcp_server(*, service: AlertService, settings: Settings) -> FastMCP:
    tools = AlertMcpTools(service)
    server = FastMCP(
        name="OANDA Alert MCP",
        instructions=(
            "Use these tools to create, list, cancel, and inspect one-shot OANDA price alerts. "
            "Alerts notify the configured Telegram chat when fired."
        ),
        log_level=settings.log_level,
        host=settings.mcp_http_host,
        port=settings.effective_port,
        streamable_http_path=settings.mcp_http_path,
        stateless_http=True,
        json_response=True,
    )
    for spec in TOOL_SPECS:
        server.tool(
            name=spec["name"],
            description=spec["description"],
            structured_output=True,
        )(getattr(tools, spec["name"]))
    return server
