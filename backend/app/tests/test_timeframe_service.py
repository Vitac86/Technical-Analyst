"""Tests for the timeframe mapping and candle aggregation service."""

from decimal import Decimal

import pandas as pd
import pytest

from app.services.market_data.timeframe_service import (
    APP_TIMEFRAMES,
    aggregate_candles,
    get_moex_fetch_timeframe,
    needs_aggregation,
    validate_app_timeframe,
)


# ---------------------------------------------------------------------------
# Timeframe mapping
# ---------------------------------------------------------------------------


def test_all_app_timeframes_are_valid() -> None:
    for tf in APP_TIMEFRAMES:
        assert validate_app_timeframe(tf) == tf


def test_validate_rejects_moex_only_timeframe() -> None:
    with pytest.raises(ValueError, match="Unsupported app timeframe"):
        validate_app_timeframe("1m")


def test_validate_rejects_unknown_timeframe() -> None:
    with pytest.raises(ValueError, match="Unsupported app timeframe"):
        validate_app_timeframe("2d")


def test_moex_fetch_timeframe_direct() -> None:
    assert get_moex_fetch_timeframe("1d") == "1d"
    assert get_moex_fetch_timeframe("1h") == "1h"


def test_moex_fetch_timeframe_aggregated() -> None:
    assert get_moex_fetch_timeframe("5m") == "1m"
    assert get_moex_fetch_timeframe("15m") == "1m"
    assert get_moex_fetch_timeframe("4h") == "1h"


def test_needs_aggregation_flags() -> None:
    assert needs_aggregation("5m") is True
    assert needs_aggregation("15m") is True
    assert needs_aggregation("4h") is True
    assert needs_aggregation("1h") is False
    assert needs_aggregation("1d") is False


# ---------------------------------------------------------------------------
# Candle aggregation
# ---------------------------------------------------------------------------


def _make_candle(ts: str, open_: float, high: float, low: float, close: float, vol: float = 1000.0) -> dict:
    """Build a minimal candle dict for testing (Moscow timezone naive → UTC)."""
    return {
        "ticker": "TEST",
        "timeframe": "1m",
        "timestamp": pd.Timestamp(ts, tz="UTC"),
        "open": Decimal(str(open_)),
        "high": Decimal(str(high)),
        "low": Decimal(str(low)),
        "close": Decimal(str(close)),
        "volume": Decimal(str(vol)),
    }


def test_aggregate_5m_open_is_first() -> None:
    candles = [
        _make_candle("2024-01-02 10:00", 100.0, 105.0, 98.0, 103.0),
        _make_candle("2024-01-02 10:01", 103.0, 106.0, 102.0, 104.0),
        _make_candle("2024-01-02 10:02", 104.0, 107.0, 103.0, 105.0),
        _make_candle("2024-01-02 10:03", 105.0, 108.0, 104.0, 106.0),
        _make_candle("2024-01-02 10:04", 106.0, 109.0, 105.0, 107.0),
    ]
    result = aggregate_candles(candles, "5m")
    assert len(result) == 1
    bar = result[0]
    assert float(bar["open"]) == pytest.approx(100.0)


def test_aggregate_5m_high_is_max() -> None:
    candles = [
        _make_candle("2024-01-02 10:00", 100.0, 105.0, 98.0, 103.0),
        _make_candle("2024-01-02 10:01", 103.0, 110.0, 102.0, 104.0),
        _make_candle("2024-01-02 10:02", 104.0, 107.0, 103.0, 105.0),
        _make_candle("2024-01-02 10:03", 105.0, 108.0, 104.0, 106.0),
        _make_candle("2024-01-02 10:04", 106.0, 109.0, 105.0, 107.0),
    ]
    result = aggregate_candles(candles, "5m")
    assert float(result[0]["high"]) == pytest.approx(110.0)


def test_aggregate_5m_low_is_min() -> None:
    candles = [
        _make_candle("2024-01-02 10:00", 100.0, 105.0, 90.0, 103.0),
        _make_candle("2024-01-02 10:01", 103.0, 106.0, 95.0, 104.0),
        _make_candle("2024-01-02 10:02", 104.0, 107.0, 97.0, 105.0),
        _make_candle("2024-01-02 10:03", 105.0, 108.0, 96.0, 106.0),
        _make_candle("2024-01-02 10:04", 106.0, 109.0, 99.0, 107.0),
    ]
    result = aggregate_candles(candles, "5m")
    assert float(result[0]["low"]) == pytest.approx(90.0)


def test_aggregate_5m_close_is_last() -> None:
    candles = [
        _make_candle("2024-01-02 10:00", 100.0, 105.0, 98.0, 103.0),
        _make_candle("2024-01-02 10:01", 103.0, 106.0, 102.0, 104.0),
        _make_candle("2024-01-02 10:02", 104.0, 107.0, 103.0, 111.0),
    ]
    result = aggregate_candles(candles, "5m")
    assert float(result[0]["close"]) == pytest.approx(111.0)


def test_aggregate_5m_volume_is_sum() -> None:
    candles = [
        _make_candle("2024-01-02 10:00", 100.0, 105.0, 98.0, 103.0, vol=1000.0),
        _make_candle("2024-01-02 10:01", 103.0, 106.0, 102.0, 104.0, vol=2000.0),
        _make_candle("2024-01-02 10:02", 104.0, 107.0, 103.0, 105.0, vol=3000.0),
    ]
    result = aggregate_candles(candles, "5m")
    assert float(result[0]["volume"]) == pytest.approx(6000.0)


def test_aggregate_15m_produces_correct_buckets() -> None:
    """15 1m candles should produce exactly one 15m bar."""
    candles = [
        _make_candle(f"2024-01-02 10:{m:02d}", 100.0 + m, 110.0, 90.0, 101.0 + m)
        for m in range(15)
    ]
    result = aggregate_candles(candles, "15m")
    assert len(result) == 1
    assert result[0]["timeframe"] == "15m"


def test_aggregate_4h_from_1h() -> None:
    """4 hourly candles should produce one 4h bar."""
    candles = [
        _make_candle(f"2024-01-02 0{h}:00", 100.0 + h, 110.0, 90.0, 105.0 + h)
        for h in range(4)
    ]
    # Relabel timeframe to "1h" as if they came from MOEX
    for c in candles:
        c["timeframe"] = "1h"
    result = aggregate_candles(candles, "4h")
    assert len(result) == 1
    assert result[0]["timeframe"] == "4h"


def test_aggregate_empty_candles() -> None:
    result = aggregate_candles([], "5m")
    assert result == []


def test_aggregate_direct_timeframe_relabels_only() -> None:
    """For 1d (no aggregation), candles should just get relabelled."""
    candles = [
        _make_candle("2024-01-02 10:00", 100.0, 105.0, 98.0, 103.0),
    ]
    candles[0]["timeframe"] = "1d"
    result = aggregate_candles(candles, "1d")
    assert len(result) == 1
    assert result[0]["timeframe"] == "1d"
    assert float(result[0]["open"]) == pytest.approx(100.0)


def test_aggregate_timeframe_label_is_set() -> None:
    candles = [
        _make_candle("2024-01-02 10:00", 100.0, 105.0, 98.0, 103.0),
        _make_candle("2024-01-02 10:01", 103.0, 106.0, 102.0, 104.0),
    ]
    result = aggregate_candles(candles, "5m")
    for bar in result:
        assert bar["timeframe"] == "5m"
