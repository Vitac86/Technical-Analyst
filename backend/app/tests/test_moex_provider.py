from decimal import Decimal

import pytest

from app.services.market_data.moex_provider import (
    normalize_instrument_rows,
    normalize_candle_rows,
    timeframe_to_interval,
)


def test_normalize_candle_rows_handles_mocked_moex_response() -> None:
    payload = {
        "candles": {
            "columns": [
                "begin",
                "end",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "value",
            ],
            "data": [
                [
                    "2024-01-03 10:00:00",
                    "2024-01-03 18:45:00",
                    270.1,
                    275.2,
                    269.5,
                    274.0,
                    123456,
                    1000000,
                ],
                [
                    "2024-01-04 10:00:00",
                    "2024-01-04 18:45:00",
                    None,
                    276.0,
                    270.0,
                    271.0,
                    100,
                    1000,
                ],
            ],
        }
    }

    candles = normalize_candle_rows(payload, ticker="sber", timeframe="1d")

    assert len(candles) == 1
    assert candles[0]["ticker"] == "SBER"
    assert candles[0]["timeframe"] == "1d"
    assert candles[0]["timestamp"].isoformat() == "2024-01-03T10:00:00+03:00"
    assert candles[0]["open"] == Decimal("270.1")
    assert candles[0]["high"] == Decimal("275.2")
    assert candles[0]["low"] == Decimal("269.5")
    assert candles[0]["close"] == Decimal("274.0")
    assert candles[0]["volume"] == Decimal("123456")


def test_normalize_instrument_rows_maps_legacy_ruble_code() -> None:
    payload = {
        "securities": {
            "columns": [
                "SECID",
                "SHORTNAME",
                "SECNAME",
                "BOARDID",
                "STATUS",
                "IS_TRADED",
                "FACEUNIT",
            ],
            "data": [
                ["SBER", "SBER", "Sberbank", "TQBR", "A", 1, "SUR"],
            ],
        }
    }

    instruments = normalize_instrument_rows(payload)

    assert instruments == [
        {
            "ticker": "SBER",
            "name": "Sberbank",
            "market": "shares",
            "board": "TQBR",
            "currency": "RUB",
            "is_active": True,
        }
    ]


def test_timeframe_mapping_accepts_supported_values() -> None:
    assert timeframe_to_interval("1m") == 1
    assert timeframe_to_interval("10m") == 10
    assert timeframe_to_interval("1h") == 60
    assert timeframe_to_interval("1d") == 24
    assert timeframe_to_interval("1w") == 7
    assert timeframe_to_interval("1mo") == 31


def test_timeframe_mapping_rejects_unsupported_value() -> None:
    with pytest.raises(ValueError, match="Unsupported MOEX timeframe"):
        timeframe_to_interval("5m")
