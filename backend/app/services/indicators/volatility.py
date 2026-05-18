import pandas as pd

from app.services.indicators.candle_frame import normalize_candle_dataframe


def bollinger_bands(
    candles: pd.DataFrame,
    window: int = 20,
    standard_deviations: float = 2.0,
) -> pd.DataFrame:
    """Calculate Bollinger Bands over close prices."""
    if window <= 0:
        raise ValueError("window must be greater than zero.")
    if standard_deviations <= 0:
        raise ValueError("standard_deviations must be greater than zero.")

    frame = normalize_candle_dataframe(candles)
    close = frame["close"]
    middle = close.rolling(window=window, min_periods=window).mean()
    rolling_std = close.rolling(window=window, min_periods=window).std(ddof=0)
    upper = middle + (rolling_std * standard_deviations)
    lower = middle - (rolling_std * standard_deviations)
    band_range = upper - lower

    return pd.DataFrame(
        {
            "middle": middle,
            "upper": upper,
            "lower": lower,
            "bandwidth": band_range / middle.where(middle != 0),
            "percent_b": (close - lower) / band_range.where(band_range != 0),
        },
        index=frame.index,
    )


def atr(candles: pd.DataFrame, window: int = 14) -> pd.Series:
    """Calculate Average True Range."""
    if window <= 0:
        raise ValueError("window must be greater than zero.")

    frame = normalize_candle_dataframe(candles)
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result = true_range.rolling(window=window, min_periods=window).mean()
    result.name = "atr"
    return result
