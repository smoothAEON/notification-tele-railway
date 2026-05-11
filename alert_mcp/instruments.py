"""OANDA instrument normalization helpers."""

from __future__ import annotations

import re

INSTRUMENT_ALIASES: dict[str, str] = {
    "GOLD": "XAU_USD",
    "XAUUSD": "XAU_USD",
    "SILVER": "XAG_USD",
    "XAGUSD": "XAG_USD",
    "OIL": "WTICO_USD",
    "WTI": "WTICO_USD",
    "USOIL": "WTICO_USD",
    "WTICOUSD": "WTICO_USD",
    "BRENT": "BCO_USD",
    "BCOUSD": "BCO_USD",
    "BTC": "BTC_USD",
    "BTCUSD": "BTC_USD",
    "ETH": "ETH_USD",
    "ETHUSD": "ETH_USD",
    "SPX": "SPX500_USD",
    "SPX500": "SPX500_USD",
    "SPX500USD": "SPX500_USD",
    "US500": "SPX500_USD",
    "US500USD": "SPX500_USD",
    "JP225": "JP225_USD",
    "JP225USD": "JP225_USD",
}

_PAIR_RE = re.compile(r"^[A-Z0-9]{2,10}_[A-Z0-9]{2,10}$")


def normalize_instrument(raw: str) -> str:
    """Normalize user input to an OANDA instrument symbol."""

    if raw is None:
        raise ValueError("instrument is required.")

    text = str(raw).strip().upper()
    if not text:
        raise ValueError("instrument is required.")

    compact = text.replace("/", "").replace("-", "").replace("_", "").replace(" ", "")
    if compact in INSTRUMENT_ALIASES:
        return INSTRUMENT_ALIASES[compact]

    normalized = text.replace("/", "_").replace("-", "_").replace(" ", "")
    if normalized in INSTRUMENT_ALIASES:
        return INSTRUMENT_ALIASES[normalized]

    if "_" not in normalized and len(normalized) == 6 and normalized.isalnum():
        normalized = f"{normalized[:3]}_{normalized[3:]}"

    if not _PAIR_RE.match(normalized):
        raise ValueError(f"invalid OANDA instrument: {raw}")

    return normalized


def normalize_instruments(values: list[str] | tuple[str, ...] | set[str]) -> tuple[str, ...]:
    """Normalize, de-duplicate, and sort a collection of instruments."""

    normalized = {normalize_instrument(value) for value in values}
    return tuple(sorted(normalized))
