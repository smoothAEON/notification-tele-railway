from pathlib import Path

import pytest

from alert_mcp.mcp_server import AlertMcpTools
from alert_mcp.models import PriceQuote
from alert_mcp.service import AlertService
from alert_mcp.settings import Settings
from alert_mcp.store import AlertStore
from tests.unit.test_service import FakeNotifier, FakePriceClient, quote


def build_settings(tmp_path: Path) -> Settings:
    return Settings(
        OANDA_API_KEY="token",
        OANDA_ACCOUNT_ID="acct",
        OANDA_ENVIRONMENT="practice",
        TELEGRAM_BOT_TOKEN="telegram",
        TELEGRAM_CHAT_ID="123",
        MCP_HTTP_API_KEY="secret",
        ALERT_DB_PATH=tmp_path / "alerts.db",
        STREAM_INSTRUMENTS="",
    )


@pytest.mark.asyncio
async def test_mcp_tool_adapter_returns_structured_alert_payload(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    prices: dict[str, PriceQuote] = {"EUR_USD": quote("EUR_USD", 1.099, 1.101)}
    service = AlertService(
        settings=settings,
        store=AlertStore(settings.alert_db_path),
        price_client=FakePriceClient(prices),
        notifier=FakeNotifier(),
    )
    tools = AlertMcpTools(service)

    created = await tools.create_price_alert("eurusd", 1.11, "above", "target")
    current = await tools.list_current_alerts()
    status = await tools.get_service_status()

    assert created["alert"]["instrument"] == "EUR_USD"
    assert current["alerts"][0]["target_price"] == 1.11
    assert status["alert_counts"]["PENDING"] == 1

