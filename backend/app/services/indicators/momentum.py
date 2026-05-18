import pandas as pd


def rsi(candles: pd.DataFrame, window: int = 14) -> pd.Series:
    """Relative Strength Index placeholder."""
    raise NotImplementedError("RSI calculation is not implemented yet.")


def stochastic(
    candles: pd.DataFrame,
    k_window: int = 14,
    d_window: int = 3,
) -> pd.DataFrame:
    """Stochastic Oscillator placeholder."""
    raise NotImplementedError("Stochastic calculation is not implemented yet.")
