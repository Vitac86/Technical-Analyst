import pandas as pd


def bollinger_bands(
    candles: pd.DataFrame,
    window: int = 20,
    standard_deviations: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands placeholder."""
    raise NotImplementedError("Bollinger Bands calculation is not implemented yet.")


def atr(candles: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average True Range placeholder."""
    raise NotImplementedError("ATR calculation is not implemented yet.")
