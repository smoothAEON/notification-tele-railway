from pathlib import Path

from starlette.testclient import TestClient

from alert_mcp.asgi import create_app
from alert_mcp.service import AlertService
from alert_mcp.settings import Settings
from alert_mcp.store import AlertStore
from tests.unit.test_service import FakeNotifier, FakePriceClient


def build_settings(tmp_path: Path) -> Settings:
    return Settings(
        OANDA_API_KEY="token",
        OANDA_ACCOUNT_ID="acct",
        OANDA_ENVIRONMENT="practice",
        TELEGRAM_BOT_TOKEN="telegram",
        TELEGRAM_CHAT_ID="123",
        MCP_HTTP_API_KEY="secret",
        ALERT_DB_PATH=tmp_path / "alerts.db",
        STREAM_INSTRUMENTS="EUR_USD",
    )


def test_health_is_unauthenticated_and_mcp_requires_bearer(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    service = AlertService(
        settings=settings,
        store=AlertStore(settings.alert_db_path),
        price_client=FakePriceClient(),
        notifier=FakeNotifier(),
    )
    app = create_app(settings=settings, service=service)

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True

        unauthorized = client.get("/mcp")
        assert unauthorized.status_code == 401

