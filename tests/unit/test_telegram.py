from pathlib import Path

import pytest

from alert_mcp.settings import Settings
from alert_mcp.store import AlertStore
from alert_mcp.telegram import TelegramNotifier


def build_settings(tmp_path: Path) -> Settings:
    return Settings(
        OANDA_API_KEY="token",
        OANDA_ACCOUNT_ID="acct",
        OANDA_ENVIRONMENT="practice",
        TELEGRAM_BOT_TOKEN="telegram-token",
        TELEGRAM_CHAT_ID="-100123",
        MCP_HTTP_API_KEY="secret",
        ALERT_DB_PATH=tmp_path / "alerts.db",
        STREAM_INSTRUMENTS="",
    )


class FakeResponse:
    def __init__(self, *, ok: bool, status_code: int = 200, text: str = "") -> None:
        self.ok = ok
        self.status_code = status_code
        self.text = text


@pytest.mark.asyncio
async def test_telegram_notifier_reports_success(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return FakeResponse(ok=True)

    monkeypatch.setattr("alert_mcp.telegram.requests.post", fake_post)
    settings = build_settings(tmp_path)
    store = AlertStore(settings.alert_db_path)
    alert = store.create_alert(instrument="EUR_USD", target_price=1.1, direction="above", note="<test>")

    result = await TelegramNotifier(settings).send_price_alert(alert, trigger_price=1.101)

    assert result.ok is True
    assert calls[0][1]["chat_id"] == "-100123"
    assert "&lt;test&gt;" in calls[0][1]["text"]


@pytest.mark.asyncio
async def test_telegram_notifier_reports_http_failure(monkeypatch, tmp_path: Path) -> None:
    def fake_post(url, json, timeout):
        return FakeResponse(ok=False, status_code=400, text="bad request")

    monkeypatch.setattr("alert_mcp.telegram.requests.post", fake_post)
    settings = build_settings(tmp_path)
    store = AlertStore(settings.alert_db_path)
    alert = store.create_alert(instrument="EUR_USD", target_price=1.1, direction="above")

    result = await TelegramNotifier(settings).send_price_alert(alert, trigger_price=1.101)

    assert result.ok is False
    assert "Telegram HTTP 400" in result.error

