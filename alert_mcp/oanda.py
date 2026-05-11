"""OANDA REST and stream pricing client."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Iterable, Iterator

from alert_mcp.instruments import normalize_instrument, normalize_instruments
from alert_mcp.models import PriceQuote
from alert_mcp.settings import Settings

logger = logging.getLogger(__name__)


class OandaPriceClient:
    """Read-only OANDA pricing client."""

    def __init__(self, settings: Settings, api_client: object | None = None) -> None:
        self.settings = settings
        self._api_client = api_client

    async def get_prices(self, instruments: Iterable[str]) -> dict[str, PriceQuote]:
        resolved = normalize_instruments(tuple(instruments))
        if not resolved:
            return {}
        logger.info("oanda_rest_pricing_request instruments=%s", ",".join(resolved))
        return await asyncio.to_thread(self._get_prices_sync, resolved)

    async def stream_prices(self, instruments: Iterable[str]) -> AsyncGenerator[PriceQuote, None]:
        resolved = normalize_instruments(tuple(instruments))
        if not resolved:
            return

        endpoint: object | None = None
        iterator: Iterator[dict[str, Any]] | None = None
        pending_next: asyncio.Task[tuple[bool, dict[str, Any] | None]] | None = None
        try:
            endpoint, iterator = await asyncio.to_thread(self._open_stream, resolved)
            logger.info("oanda_stream_opened instruments=%s", ",".join(resolved))
            while True:
                pending_next = asyncio.create_task(asyncio.to_thread(self._next_payload, iterator))
                try:
                    finished, payload = await pending_next
                finally:
                    pending_next = None

                if finished:
                    logger.warning("oanda_stream_ended instruments=%s", ",".join(resolved))
                    return
                if payload is None or str(payload.get("type", "PRICE")).upper() == "HEARTBEAT":
                    continue
                yield self._payload_to_quote(payload, source="stream")
        finally:
            if pending_next is not None:
                pending_next.cancel()
                with contextlib.suppress(Exception):
                    await pending_next
            if endpoint is not None and iterator is not None:
                await asyncio.to_thread(self._close_stream, endpoint, iterator)

    def _get_prices_sync(self, instruments: tuple[str, ...]) -> dict[str, PriceQuote]:
        pricing_endpoint = self._import_pricing_info_endpoint()
        endpoint = pricing_endpoint(
            accountID=self.settings.oanda_account_id.get_secret_value(),
            params={"instruments": ",".join(instruments)},
        )
        response = self._get_api_client().request(endpoint)
        prices = response.get("prices", []) if isinstance(response, dict) else []
        result: dict[str, PriceQuote] = {}
        for payload in prices:
            quote = self._payload_to_quote(payload, source="rest")
            result[quote.instrument] = quote
        logger.info(
            "oanda_rest_pricing_response instruments=%s price_count=%s",
            ",".join(instruments),
            len(result),
        )
        return result

    def _open_stream(self, instruments: tuple[str, ...]) -> tuple[object, Iterator[dict[str, Any]]]:
        logger.info("oanda_stream_opening instruments=%s", ",".join(instruments))
        stream_endpoint = self._import_pricing_stream_endpoint()
        endpoint = stream_endpoint(
            accountID=self.settings.oanda_account_id.get_secret_value(),
            params={"instruments": ",".join(instruments)},
        )
        response = self._get_api_client().request(endpoint)
        return endpoint, self._coerce_iterator(response if response is not None else getattr(endpoint, "response", None))

    def _get_api_client(self) -> object:
        if self._api_client is None:
            api_class = self._import_api_class()
            self._api_client = api_class(
                access_token=self.settings.oanda_api_key.get_secret_value(),
                environment=self.settings.oanda_environment,
            )
        return self._api_client

    @staticmethod
    def _next_payload(iterator: Iterator[dict[str, Any]]) -> tuple[bool, dict[str, Any] | None]:
        try:
            return False, next(iterator)
        except StopIteration:
            return True, None

    @staticmethod
    def _coerce_iterator(payloads: object) -> Iterator[dict[str, Any]]:
        if payloads is None:
            return iter(())
        if isinstance(payloads, dict):
            return iter((payloads,))
        try:
            return iter(payloads)
        except TypeError as exc:
            raise RuntimeError("OANDA stream response is not iterable.") from exc

    @staticmethod
    def _close_stream(endpoint: object, iterator: Iterator[dict[str, Any]]) -> None:
        terminate = getattr(endpoint, "terminate", None)
        if callable(terminate):
            with contextlib.suppress(Exception):
                terminate("stream closed")
        close = getattr(iterator, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()

    @staticmethod
    def _payload_to_quote(payload: dict[str, Any], *, source: str) -> PriceQuote:
        instrument = normalize_instrument(str(payload.get("instrument", "")))
        bid = OandaPriceClient._extract_price(payload, direct_key="closeoutBid", levels_key="bids")
        ask = OandaPriceClient._extract_price(payload, direct_key="closeoutAsk", levels_key="asks")
        return PriceQuote(
            instrument=instrument,
            bid=bid,
            ask=ask,
            time=OandaPriceClient._parse_time(payload.get("time")),
            tradeable=bool(payload.get("tradeable", True)),
            source=source,
        )

    @staticmethod
    def _extract_price(payload: dict[str, Any], *, direct_key: str, levels_key: str) -> float:
        levels = payload.get(levels_key)
        if isinstance(levels, list) and levels:
            try:
                return float(levels[0]["price"])
            except (KeyError, TypeError, ValueError, IndexError):
                pass
        if payload.get(direct_key) is not None:
            return float(payload[direct_key])
        raise RuntimeError(f"OANDA price payload missing {direct_key}/{levels_key}.")

    @staticmethod
    def _parse_time(value: object) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _import_api_class() -> object:
        from oandapyV20 import API

        return API

    @staticmethod
    def _import_pricing_info_endpoint() -> object:
        from oandapyV20.endpoints.pricing import PricingInfo

        return PricingInfo

    @staticmethod
    def _import_pricing_stream_endpoint() -> object:
        from oandapyV20.endpoints.pricing import PricingStream

        return PricingStream
