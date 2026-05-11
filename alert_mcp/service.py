"""Alert service orchestration."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Iterable

from alert_mcp.instruments import normalize_instrument, normalize_instruments
from alert_mcp.models import Alert, AlertDirection, PriceQuote
from alert_mcp.oanda import OandaPriceClient
from alert_mcp.settings import Settings
from alert_mcp.store import AlertStore
from alert_mcp.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


class AlertService:
    """Coordinates alert persistence, price monitoring, and Telegram delivery."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: AlertStore,
        price_client: OandaPriceClient,
        notifier: TelegramNotifier,
    ) -> None:
        self.settings = settings
        self.store = store
        self.price_client = price_client
        self.notifier = notifier
        self._price_cache: dict[str, PriceQuote] = {}
        self._price_lock = asyncio.Lock()
        self._watch_set_changed = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._monitor_task: asyncio.Task[None] | None = None
        self._streaming_instruments: tuple[str, ...] = ()
        self._stream_error: str | None = None

    async def start(self) -> None:
        logger.info("alert service starting db_path=%s", self.settings.alert_db_path)
        recovered = self.store.recover_firing_alerts()
        if recovered:
            logger.warning("recovered %s stale FIRING alerts", recovered)
        self._stop_event.clear()
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_loop(), name="oanda-alert-monitor")
            logger.info("alert monitor task created")

    async def stop(self) -> None:
        logger.info("alert service stopping")
        self._stop_event.set()
        self._watch_set_changed.set()
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        logger.info("alert service stopped")

    async def get_service_status(self) -> dict:
        counts = self.store.status_counts()
        watched = self.get_watched_instruments()
        configured = (
            normalize_instruments(self.settings.stream_instrument_list)
            if self.settings.stream_instrument_list
            else ()
        )
        now = datetime.now(timezone.utc)
        async with self._price_lock:
            cache = {
                instrument: {
                    "source": quote.source,
                    "time": quote.time.isoformat(),
                    "age_seconds": max(0.0, (now - quote.time).total_seconds()),
                    "bid": quote.bid,
                    "ask": quote.ask,
                    "mid": quote.mid,
                }
                for instrument, quote in sorted(self._price_cache.items())
            }
        return {
            "service": "oanda-alert-mcp",
            "oanda_environment": self.settings.oanda_environment,
            "mcp_path": self.settings.mcp_http_path,
            "db_path": str(self.settings.alert_db_path),
            "telegram_ready": self.notifier.ready,
            "monitor_running": bool(self._monitor_task and not self._monitor_task.done()),
            "active_alert_instruments": list(watched),
            "streaming_instruments": list(self._streaming_instruments),
            "configured_stream_instruments": list(configured),
            "watched_instruments": list(watched),
            "stream_error": self._stream_error,
            "alert_counts": counts,
            "price_cache": cache,
        }

    async def get_current_prices(self, instruments: Iterable[str] | None = None) -> dict:
        requested = self._resolve_requested_instruments(instruments)
        if not requested:
            return {"prices": []}

        fresh: dict[str, PriceQuote] = {}
        missing: list[str] = []
        now = datetime.now(timezone.utc)
        async with self._price_lock:
            for instrument in requested:
                quote = self._price_cache.get(instrument)
                if quote and (now - quote.time).total_seconds() <= self.settings.price_cache_ttl_seconds:
                    fresh[instrument] = quote
                else:
                    missing.append(instrument)

        if missing:
            logger.info("fetching current prices instruments=%s", ",".join(missing))
            rest_quotes = await self.price_client.get_prices(missing)
            for quote in rest_quotes.values():
                await self._cache_quote(quote)
            fresh.update(rest_quotes)

        return {
            "prices": [
                fresh[instrument].to_dict()
                for instrument in requested
                if instrument in fresh
            ],
            "missing": [instrument for instrument in requested if instrument not in fresh],
        }

    async def create_price_alert(
        self,
        *,
        instrument: str,
        target_price: float,
        direction: AlertDirection,
        note: str | None = None,
    ) -> dict:
        resolved = normalize_instrument(instrument)
        if direction not in {"above", "below"}:
            raise ValueError("direction must be 'above' or 'below'.")
        alert = self.store.create_alert(
            instrument=resolved,
            target_price=float(target_price),
            direction=direction,
            note=note,
        )
        logger.info(
            "created price alert id=%s instrument=%s direction=%s target_price=%s",
            alert.id,
            alert.instrument,
            alert.direction,
            alert.target_price,
        )
        current = await self.get_current_prices([resolved])
        self._watch_set_changed.set()
        return {
            "alert": alert.to_dict(),
            "current_price": current["prices"][0] if current["prices"] else None,
        }

    async def list_current_alerts(self, *, instrument: str | None = None) -> dict:
        alerts = self.store.list_current_alerts(instrument=instrument)
        price_map = await self._price_map_for_alerts(alerts)
        return {"alerts": [self._alert_with_price(alert, price_map.get(alert.instrument)) for alert in alerts]}

    async def list_fired_alerts(self, *, instrument: str | None = None, limit: int = 50) -> dict:
        alerts = self.store.list_fired_alerts(instrument=instrument, limit=limit)
        return {"alerts": [alert.to_dict() for alert in alerts]}

    async def cancel_alert(self, *, alert_id: int) -> dict:
        alert = self.store.cancel_alert(alert_id)
        logger.info("cancel alert requested id=%s cancelled=%s", alert_id, alert is not None)
        self._watch_set_changed.set()
        return {"cancelled": alert is not None, "alert": alert.to_dict() if alert else None}

    async def cancel_current_alerts(self, *, instrument: str | None = None) -> dict:
        alerts = self.store.cancel_current_alerts(instrument=instrument)
        logger.info(
            "cancel current alerts requested instrument=%s cancelled_count=%s",
            instrument,
            len(alerts),
        )
        self._watch_set_changed.set()
        return {"cancelled_count": len(alerts), "alerts": [alert.to_dict() for alert in alerts]}

    def get_watched_instruments(self) -> tuple[str, ...]:
        return self.store.active_instruments()

    async def process_quote(self, quote: PriceQuote) -> None:
        quote = PriceQuote(
            instrument=normalize_instrument(quote.instrument),
            bid=float(quote.bid),
            ask=float(quote.ask),
            time=quote.time,
            tradeable=quote.tradeable,
            source=quote.source,
        )
        await self._cache_quote(quote)
        alerts = self.store.list_current_alerts(instrument=quote.instrument)
        for alert in alerts:
            if self._is_triggered(alert, quote.mid):
                await self._fire_alert(alert, trigger_price=quote.mid)

    async def _monitor_loop(self) -> None:
        backoff = 1.0
        last_logged_instruments: tuple[str, ...] | None = None
        logger.info("alert monitor loop started")
        while not self._stop_event.is_set():
            instruments = self.get_watched_instruments()
            if instruments != last_logged_instruments:
                if instruments:
                    logger.info("alert monitor watching instruments=%s", ",".join(instruments))
                else:
                    logger.info("alert monitor idle no_active_alerts=true")
                last_logged_instruments = instruments
            if not instruments:
                self._streaming_instruments = ()
                await self._wait_for_watch_change(timeout=5.0)
                continue

            self._watch_set_changed.clear()
            stream_task = asyncio.create_task(self._consume_stream(instruments), name="oanda-price-stream")
            change_task = asyncio.create_task(self._watch_set_changed.wait(), name="watch-set-change")
            stop_task = asyncio.create_task(self._stop_event.wait(), name="alert-service-stop")

            done, pending = await asyncio.wait(
                {stream_task, change_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            if stop_task in done:
                stream_task.cancel()
                await asyncio.gather(stream_task, return_exceptions=True)
                return

            if change_task in done:
                stream_task.cancel()
                await asyncio.gather(stream_task, return_exceptions=True)
                backoff = 1.0
                continue

            if stream_task in done:
                exc = stream_task.exception()
                if exc is not None:
                    self._stream_error = str(exc)
                    logger.warning("price stream failed: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, float(self.settings.stream_reconnect_max_seconds))
        logger.info("alert monitor loop stopped")

    async def _consume_stream(self, instruments: tuple[str, ...]) -> None:
        self._streaming_instruments = instruments
        self._stream_error = None
        logger.info("price stream starting instruments=%s", ",".join(instruments))
        try:
            async for quote in self.price_client.stream_prices(instruments):
                await self.process_quote(quote)
        finally:
            logger.info("price stream stopped instruments=%s", ",".join(instruments))

    async def _wait_for_watch_change(self, *, timeout: float) -> None:
        self._watch_set_changed.clear()
        try:
            await asyncio.wait_for(self._watch_set_changed.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    async def _cache_quote(self, quote: PriceQuote) -> None:
        async with self._price_lock:
            self._price_cache[quote.instrument] = quote

    async def _fire_alert(self, alert: Alert, *, trigger_price: float) -> None:
        logger.info(
            "price alert firing id=%s instrument=%s trigger_price=%s",
            alert.id,
            alert.instrument,
            trigger_price,
        )
        firing = self.store.mark_firing(alert.id, trigger_price=trigger_price)
        if firing is None:
            logger.info("price alert skipped id=%s reason=not_pending", alert.id)
            return

        delivery = await self.notifier.send_price_alert(firing, trigger_price=trigger_price)
        if delivery.ok:
            fired = self.store.mark_fired(firing.id, trigger_price=trigger_price)
            if fired:
                logger.info("price alert fired id=%s instrument=%s", fired.id, fired.instrument)
                self._watch_set_changed.set()
            return

        logger.warning(
            "price alert delivery failed id=%s instrument=%s error=%s",
            firing.id,
            firing.instrument,
            delivery.error,
        )
        self.store.release_firing(
            firing.id,
            error=delivery.error or "Telegram delivery failed.",
        )

    @staticmethod
    def _is_triggered(alert: Alert, current_price: float) -> bool:
        if alert.status != "PENDING":
            return False
        if alert.direction == "above":
            return current_price >= alert.target_price
        return current_price <= alert.target_price

    def _resolve_requested_instruments(self, instruments: Iterable[str] | None) -> tuple[str, ...]:
        if instruments is None:
            return self.get_watched_instruments()
        return normalize_instruments(tuple(instruments))

    async def _price_map_for_alerts(self, alerts: list[Alert]) -> dict[str, PriceQuote]:
        instruments = sorted({alert.instrument for alert in alerts})
        if not instruments:
            return {}
        result = await self.get_current_prices(instruments)
        quotes: dict[str, PriceQuote] = {}
        for payload in result["prices"]:
            quotes[payload["instrument"]] = PriceQuote(
                instrument=payload["instrument"],
                bid=float(payload["bid"]),
                ask=float(payload["ask"]),
                time=datetime.fromisoformat(payload["time"]),
                tradeable=bool(payload.get("tradeable", True)),
                source=str(payload.get("source", "unknown")),
            )
        return quotes

    @staticmethod
    def _alert_with_price(alert: Alert, quote: PriceQuote | None) -> dict:
        payload = alert.to_dict()
        if quote is None:
            payload["current_price"] = None
            payload["distance"] = None
            payload["distance_pct"] = None
            return payload
        distance = alert.target_price - quote.mid
        payload["current_price"] = quote.to_dict()
        payload["distance"] = distance
        payload["distance_pct"] = (distance / quote.mid) * 100 if quote.mid else None
        return payload


def build_service(settings: Settings) -> AlertService:
    store = AlertStore(settings.alert_db_path)
    price_client = OandaPriceClient(settings)
    notifier = TelegramNotifier(settings)
    return AlertService(
        settings=settings,
        store=store,
        price_client=price_client,
        notifier=notifier,
    )
