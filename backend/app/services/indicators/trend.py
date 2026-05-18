import pandas as pd

from app.services.indicators.candle_frame import normalize_candle_dataframe


def sma(candles: pd.DataFrame, window: int = 20) -> pd.Series:
    """Calculate a simple moving average over close prices."""
    _validate_positive_int(window, "window")
    frame = normalize_candle_dataframe(candles)
    result = frame["close"].rolling(window=window, min_periods=window).mean()
    result.name = "sma"
    return result


def ema(candles: pd.DataFrame, window: int = 20) -> pd.Series:
    """Calculate an exponential moving average over close prices."""
    _validate_positive_int(window, "window")
    frame = normalize_candle_dataframe(candles)
    result = frame["close"].ewm(
        span=window,
        adjust=False,
        min_periods=window,
    ).mean()
    result.name = "ema"
    return result


def macd(
    candles: pd.DataFrame,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    """Calculate MACD, signal line, and histogram over close prices."""
    _validate_positive_int(fast_period, "fast_period")
    _validate_positive_int(slow_period, "slow_period")
    _validate_positive_int(signal_period, "signal_period")
    if fast_period >= slow_period:
        raise ValueError("MACD fast_period must be less than slow_period.")

    frame = normalize_candle_dataframe(candles)
    close = frame["close"]
    fast_ema = close.ewm(
        span=fast_period,
        adjust=False,
        min_periods=fast_period,
    ).mean()
    slow_ema = close.ewm(
        span=slow_period,
        adjust=False,
        min_periods=slow_period,
    ).mean()
    macd_line = fast_ema - slow_ema
    signal = macd_line.ewm(
        span=signal_period,
        adjust=False,
        min_periods=signal_period,
    ).mean()
    histogram = macd_line - signal

    return pd.DataFrame(
        {
            "macd": macd_line,
            "signal": signal,
            "histogram": histogram,
        },
        index=frame.index,
    )


def adx(candles: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average Directional Index placeholder."""
    raise NotImplementedError("ADX calculation is not implemented yet.")


def _validate_positive_int(value: int, field_name: str) -> None:
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")
