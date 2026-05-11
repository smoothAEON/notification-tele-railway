from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from alert_mcp.models import PriceQuote
from alert_mcp.service import AlertService
from alert_mcp.settings import Settings
from alert_mcp.store import AlertStore
from alert_mcp.telegram import DeliveryResult


class FakePriceClient:
    def __init__(self, quotes: dict[str, PriceQuote] | None = None) -> None:
        self.quotes = quotes or {}

    async def get_prices(self, instruments):
        return {instrument: self.quotes[instrument] for instrument in instruments if instrument in self.quotes}

    async def stream_prices(self, instruments):
        if False:
            yield None


class FakeNotifier:
    ready = True

    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[tuple[int, float]] = []

    async def send_price_alert(self, alert, *, trigger_price: float):
        self.sent.append((alert.id, trigger_price))
        if self.ok:
            return DeliveryResult(ok=True)
        return DeliveryResult(ok=False, error="send failed")


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


def build_settings_with_streams(tmp_path: Path, streams: str) -> Settings:
    return Settings(
        OANDA_API_KEY="token",
        OANDA_ACCOUNT_ID="acct",
        OANDA_ENVIRONMENT="practice",
        TELEGRAM_BOT_TOKEN="telegram",
        TELEGRAM_CHAT_ID="123",
        MCP_HTTP_API_KEY="secret",
        ALERT_DB_PATH=tmp_path / "alerts.db",
        STREAM_INSTRUMENTS=streams,
    )


def quote(instrument: str, bid: float, ask: float) -> PriceQuote:
    return PriceQuote(
        instrument=instrument,
        bid=bid,
        ask=ask,
        time=datetime.now(timezone.utc),
        source="test",
    )


@pytest.mark.asyncio
async def test_process_quote_fires_above_alert_once(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    notifier = FakeNotifier()
    service = AlertService(
        settings=settings,
        store=AlertStore(settings.alert_db_path),
        price_client=FakePriceClient(),
        notifier=notifier,
    )
    alert = service.store.create_alert(instrument="EUR_USD", target_price=1.1, direction="above")

    await service.process_quote(quote("EUR_USD", 1.098, 1.1))
    assert service.store.get_alert(alert.id).status == "PENDING"
    assert notifier.sent == []

    await service.process_quote(quote("EUR_USD", 1.1, 1.102))
    stored = service.store.get_alert(alert.id)
    assert stored.status == "FIRED"
    assert notifier.sent == [(alert.id, pytest.approx(1.101))]

    await service.process_quote(quote("EUR_USD", 1.11, 1.112))
    assert len(notifier.sent) == 1


@pytest.mark.asyncio
async def test_process_quote_releases_alert_when_telegram_fails(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    notifier = FakeNotifier(ok=False)
    service = AlertService(
        settings=settings,
        store=AlertStore(settings.alert_db_path),
        price_client=FakePriceClient(),
        notifier=notifier,
    )
    alert = service.store.create_alert(instrument="GBP_USD", target_price=1.25, direction="below")

    await service.process_quote(quote("GBP_USD", 1.248, 1.25))
    stored = service.store.get_alert(alert.id)
    assert stored.status == "PENDING"
    assert stored.last_error == "send failed"


@pytest.mark.asyncio
async def test_create_alert_returns_current_price_context(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    price_client = FakePriceClient({"XAU_USD": quote("XAU_USD", 3049.9, 3050.1)})
    service = AlertService(
        settings=settings,
        store=AlertStore(settings.alert_db_path),
        price_client=price_client,
        notifier=FakeNotifier(),
    )

    result = await service.create_price_alert(
        instrument="gold",
        target_price=3060.0,
        direction="above",
        note="breakout",
    )

    assert result["alert"]["instrument"] == "XAU_USD"
    assert result["current_price"]["mid"] == pytest.approx(3050.0)


@pytest.mark.asyncio
async def test_watched_instruments_are_active_alerts_only(tmp_path: Path) -> None:
    settings = build_settings_with_streams(tmp_path, "EUR_USD,GBP_USD,XAU_USD")
    service = AlertService(
        settings=settings,
        store=AlertStore(settings.alert_db_path),
        price_client=FakePriceClient(),
        notifier=FakeNotifier(),
    )

    assert service.get_watched_instruments() == ()
    assert await service.get_current_prices() == {"prices": []}

    alert = service.store.create_alert(instrument="spx500usd", target_price=5000.0, direction="above")
    assert service.get_watched_instruments() == ("SPX500_USD",)

    service.store.cancel_alert(alert.id)
    assert service.get_watched_instruments() == ()


@pytest.mark.asyncio
async def test_status_separates_active_and_configured_instruments(tmp_path: Path) -> None:
    settings = build_settings_with_streams(tmp_path, "EUR_USD,GBP_USD")
    service = AlertService(
        settings=settings,
        store=AlertStore(settings.alert_db_path),
        price_client=FakePriceClient(),
        notifier=FakeNotifier(),
    )
    service.store.create_alert(instrument="wticousd", target_price=70.0, direction="below")

    status = await service.get_service_status()

    assert status["active_alert_instruments"] == ["WTICO_USD"]
    assert status["watched_instruments"] == ["WTICO_USD"]
    assert status["configured_stream_instruments"] == ["EUR_USD", "GBP_USD"]


@pytest.mark.asyncio
async def test_fired_alert_drops_instrument_from_watchlist(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    service = AlertService(
        settings=settings,
        store=AlertStore(settings.alert_db_path),
        price_client=FakePriceClient(),
        notifier=FakeNotifier(),
    )
    alert = service.store.create_alert(instrument="bcousd", target_price=80.0, direction="above")
    assert service.get_watched_instruments() == ("BCO_USD",)

    assert service.store.mark_firing(alert.id, trigger_price=80.1) is not None
    assert service.get_watched_instruments() == ("BCO_USD",)

    assert service.store.mark_fired(alert.id, trigger_price=80.1) is not None
    assert service.get_watched_instruments() == ()
