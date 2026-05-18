from collections.abc import Sequence
from typing import Any

import pandas as pd


REQUIRED_CANDLE_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
REQUIRED_OHLC_COLUMNS = ("timestamp", "open", "high", "low", "close")
NUMERIC_CANDLE_COLUMNS = ("open", "high", "low", "close", "volume")


def candles_to_dataframe(candles: Sequence[Any]) -> pd.DataFrame:
    """Convert Candle ORM objects into normalized, timestamp-sorted OHLCV data."""
    records = [
        {
            "timestamp": candle.timestamp,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        }
        for candle in candles
    ]
    frame = pd.DataFrame.from_records(records, columns=REQUIRED_CANDLE_COLUMNS)
    return normalize_candle_dataframe(frame, drop_missing_ohlc=True)


def normalize_candle_dataframe(
    candles: pd.DataFrame,
    *,
    drop_missing_ohlc: bool = False,
) -> pd.DataFrame:
    """Validate and normalize a candle DataFrame for indicator calculations."""
    missing_columns = [
        column for column in REQUIRED_CANDLE_COLUMNS if column not in candles.columns
    ]
    if missing_columns:
        missing = ", ".join(missing_columns)
        raise ValueError(f"Candle DataFrame is missing required columns: {missing}.")

    frame = candles.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    for column in NUMERIC_CANDLE_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    if drop_missing_ohlc:
        frame = frame.dropna(subset=REQUIRED_OHLC_COLUMNS)

    return frame.sort_values("timestamp", kind="mergesort")
