import pandas as pd


def sma(candles: pd.DataFrame, window: int) -> pd.Series:
    """Simple Moving Average placeholder."""
    raise NotImplementedError("SMA calculation is not implemented yet.")


def ema(candles: pd.DataFrame, window: int) -> pd.Series:
    """Exponential Moving Average placeholder."""
    raise NotImplementedError("EMA calculation is not implemented yet.")


def macd(
    candles: pd.DataFrame,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    """MACD placeholder."""
    raise NotImplementedError("MACD calculation is not implemented yet.")


def adx(candles: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average Directional Index placeholder."""
    raise NotImplementedError("ADX calculation is not implemented yet.")
