"""Timeframe mapping and candle aggregation for app timeframes.

App timeframes and their MOEX fetch sources:
  1d  → fetch 1d candles directly (no aggregation)
  1h  → fetch 1h candles directly (no aggregation)
  4h  → fetch 1h candles, aggregate to 4h
  15m → fetch 1m candles, aggregate to 15m
  5m  → fetch 1m candles, aggregate to 5m

The final candles are stored in SQLite with the *app* timeframe label (e.g. "5m"),
not the MOEX fetch timeframe. MOEX implementation details are not exposed upstream.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pandas as pd


APP_TIMEFRAMES: frozenset[str] = frozenset({"5m", "15m", "1h", "4h", "1d"})

# (moex_fetch_timeframe, minutes_per_bucket | None)
# None means direct fetch — no aggregation step.
_TIMEFRAME_CONFIG: dict[str, tuple[str, int | None]] = {
    "1d": ("1d", None),
    "1h": ("1h", None),
    "4h": ("1h", 240),
    "15m": ("1m", 15),
    "5m": ("1m", 5),
}


def validate_app_timeframe(timeframe: str) -> str:
    """Return timeframe if valid; raise ValueError otherwise."""
    if timeframe not in _TIMEFRAME_CONFIG:
        supported = ", ".join(sorted(_TIMEFRAME_CONFIG))
        raise ValueError(
            f"Unsupported app timeframe '{timeframe}'. Supported: {supported}."
        )
    return timeframe


def get_moex_fetch_timeframe(app_timeframe: str) -> str:
    """Return the MOEX ISS timeframe to request for a given app timeframe."""
    validate_app_timeframe(app_timeframe)
    return _TIMEFRAME_CONFIG[app_timeframe][0]


def needs_aggregation(app_timeframe: str) -> bool:
    """Return True when the app timeframe requires aggregating lower-TF candles."""
    validate_app_timeframe(app_timeframe)
    return _TIMEFRAME_CONFIG[app_timeframe][1] is not None


def aggregate_candles(
    candles: list[dict[str, Any]],
    app_timeframe: str,
) -> list[dict[str, Any]]:
    """Aggregate candle dicts from a lower MOEX timeframe into app_timeframe buckets.

    Input candle dicts must have keys: ticker, timeframe, timestamp, open, high,
    low, close, volume (volume may be None).

    Returns new candle dicts with ``timeframe`` relabelled to *app_timeframe*.
    OHLCV aggregation rules:
      open   = first open in bucket
      high   = max high in bucket
      low    = min low in bucket
      close  = last close in bucket
      volume = sum of volumes in bucket (NaN → 0 before summing)
    """
    validate_app_timeframe(app_timeframe)
    minutes = _TIMEFRAME_CONFIG[app_timeframe][1]

    if not candles:
        return []

    if minutes is None:
        # Direct timeframe — just relabel and return.
        return [dict(c, timeframe=app_timeframe) for c in candles]

    df = pd.DataFrame(candles)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["open"] = df["open"].apply(_to_float)
    df["high"] = df["high"].apply(_to_float)
    df["low"] = df["low"].apply(_to_float)
    df["close"] = df["close"].apply(_to_float)
    df["volume"] = df["volume"].apply(_to_float_nullable).fillna(0.0)
    df = df.sort_values("timestamp").set_index("timestamp")

    rule = f"{minutes}min"
    agg = df.resample(rule, closed="left", label="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    agg = agg.dropna(subset=["open", "high", "low", "close"])
    agg = agg.reset_index()

    ticker = candles[0].get("ticker", "") if candles else ""
    result: list[dict[str, Any]] = []
    for _, row in agg.iterrows():
        result.append(
            {
                "ticker": ticker,
                "timeframe": app_timeframe,
                "timestamp": row["timestamp"].to_pydatetime(),
                "open": Decimal(str(round(row["open"], 6))),
                "high": Decimal(str(round(row["high"], 6))),
                "low": Decimal(str(round(row["low"], 6))),
                "close": Decimal(str(round(row["close"], 6))),
                "volume": Decimal(str(round(row["volume"], 6)))
                if pd.notna(row["volume"])
                else None,
            }
        )

    return result


def _to_float(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _to_float_nullable(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
