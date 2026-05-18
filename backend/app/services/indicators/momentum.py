import pandas as pd

from app.services.indicators.candle_frame import normalize_candle_dataframe


def rsi(candles: pd.DataFrame, window: int = 14) -> pd.Series:
    """Calculate Relative Strength Index using Wilder-style smoothing."""
    if window <= 0:
        raise ValueError("window must be greater than zero.")

    frame = normalize_candle_dataframe(candles)
    delta = frame["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    average_gain = gain.ewm(
        alpha=1 / window,
        adjust=False,
        min_periods=window,
    ).mean()
    average_loss = loss.ewm(
        alpha=1 / window,
        adjust=False,
        min_periods=window,
    ).mean()

    relative_strength = average_gain / average_loss
    result = 100 - (100 / (1 + relative_strength))
    result = result.mask((average_loss == 0) & (average_gain > 0), 100)
    result = result.mask((average_gain == 0) & (average_loss > 0), 0)
    result = result.mask((average_gain == 0) & (average_loss == 0), 50)
    result.name = "rsi"
    return result


def stochastic(
    candles: pd.DataFrame,
    k_window: int = 14,
    d_window: int = 3,
) -> pd.DataFrame:
    """Stochastic Oscillator placeholder."""
    raise NotImplementedError("Stochastic calculation is not implemented yet.")
