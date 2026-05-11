import pytest

from alert_mcp.instruments import normalize_instrument, normalize_instruments


def test_normalize_common_aliases_and_pairs() -> None:
    assert normalize_instrument("gold") == "XAU_USD"
    assert normalize_instrument("xauusd") == "XAU_USD"
    assert normalize_instrument("xagusd") == "XAG_USD"
    assert normalize_instrument("eur/usd") == "EUR_USD"
    assert normalize_instrument("gbpusd") == "GBP_USD"
    assert normalize_instrument("usdjpy") == "USD_JPY"
    assert normalize_instrument("spx500") == "SPX500_USD"
    assert normalize_instrument("spx500usd") == "SPX500_USD"
    assert normalize_instrument("wticousd") == "WTICO_USD"
    assert normalize_instrument("bcousd") == "BCO_USD"


def test_normalize_instruments_dedupes_and_sorts() -> None:
    assert normalize_instruments(["gold", "XAU_USD", "eurusd"]) == ("EUR_USD", "XAU_USD")


def test_invalid_instrument_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_instrument("not a symbol")
